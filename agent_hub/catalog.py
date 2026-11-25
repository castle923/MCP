"""Model catalog, team defaults, and per-agent launch commands."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

from .db import PKG_ROOT, home

PY = sys.executable
BOARD_TOOLS = ("post_message", "read_messages", "claim_task", "complete_task", "board_post")
# main (codex) also runs these without a prompt so parallel dispatch is not stalled on approvals;
# hub_cancel keeps codex's approval prompt.
MAIN_TOOLS = ("hub_models", "hub_assign", "hub_send", "hub_status", "hub_wait", "hub_wait_all",
              "hub_pause")
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Notification", "Stop")

PROTOCOL = (
    "You are hub agent '{role}' coordinated by agent-hub. Use the agent_hub MCP tools: "
    "read_messages to read your inbox, claim_task / complete_task to take and report tasks "
    "(always finish a task with complete_task and a short result), post_message to talk to other roles "
    "('main' is the orchestrator; act=request needs OBJECTIVE/OUTPUT/TOOLS/BOUNDARIES sections). "
    "Pass file paths or message ids instead of pasting long content. Ask 'main' only when you need a decision."
)


MAIN_GUIDE = (
    "You are 'main', the orchestrator of an agent-hub team whose agents run in other VS Code terminal panes. "
    "In this session 'sub agents', 'subagents', '서브 에이전트', 'builder' and 'reviewer' always mean these hub agents, "
    "reached only through the agent_hub MCP tools whose names start with hub_. Never use your built-in "
    "collaboration tools (spawn_agent, send_message, followup_task, wait_agent): they start other model threads "
    "that are not part of this team, even when the user's instructions mention sub-agents or model routing.\n"
    "1. Do not edit files or run build/test/install commands yourself. Read files and run read-only commands "
    "(git status/diff, ls, rg) only to plan and to check results.\n"
    "2. Split the user's request into small tasks with non-overlapping files. Give implementation to builders and "
    "review to the reviewer with agent_hub hub_assign(role, objective, output, tools, boundaries). For a role that "
    "is already running it only assigns the task (no new pane, no waiting).\n"
    "3. Assign independent tasks to every builder in the same turn, then call agent_hub hub_wait_all with all task "
    "ids; call it again while tasks are still running.\n"
    "4. When builders finish, give the reviewer a task naming the changed files. Send findings back with hub_assign "
    "to the builder that owns those files. Repeat until the reviewer passes.\n"
    "5. Never give two builders the same file. Never message yourself; talk to the user in chat.\n"
    "6. If an agent looks stuck, call hub_status: 'waiting_approval' means the user must approve in that pane.\n"
    "Finish with a report to the user: what each agent did, the changed files, and the review result.")


def load_catalog(path: Path | None = None) -> dict:
    return tomllib.loads(Path(path or PKG_ROOT / "models.toml").read_text(encoding="utf-8"))


def load_team(path: Path | None = None) -> dict:
    return tomllib.loads(Path(path or PKG_ROOT / "team.toml").read_text(encoding="utf-8"))


def validate(cat: dict, cli: str, model: str, effort: str | None) -> str:
    """Return the effort to use; raise ValueError for anything not in the catalog."""
    spec = cat["cli"].get(cli)
    if not spec:
        raise ValueError(f"unknown cli '{cli}' (catalog: {', '.join(cat['cli'])})")
    ids = [m["id"] for m in spec["models"]]
    if model not in ids:
        raise ValueError(f"model '{model}' is not in the {cli} catalog: {', '.join(ids)}")
    effort = effort or cat.get("default_effort", "medium")
    if effort not in spec["efforts"]:
        raise ValueError(f"effort '{effort}' not supported by {cli}: {', '.join(spec['efforts'])}")
    return effort


RESERVED_ROLES = ("main", "board", "human", "broadcast")


def check_role(role: str) -> str:
    """Roles name panes, board rows and state folders: keep them short and path-safe."""
    if not role or len(role) > 32 or not all(c.isalnum() or c in "-_" for c in role):
        raise ValueError(f"bad agent name '{role}': use 1-32 letters, digits, - or _")
    return role


def roster(team: dict, n: int | None = None, names: list[str] | None = None) -> list[dict]:
    """main + n subs: n=1 -> builder; n>=2 -> builders (builder, builder2, ...) + 1 reviewer."""
    n = team.get("default_subs", 2) if n is None else n
    cap = team.get("max_workers", 6)
    if not 0 <= n <= cap:
        raise ValueError(f"sub agent count must be 0..{cap} (max_workers in team.toml)")
    out = [{**team["main"], "job": "main"}]
    builders = n if n < 2 else n - 1
    for k in range(builders):
        out.append({**team["builder"], "job": "builder", "role": team["builder"]["role"] + (str(k + 1) if k else "")})
    if n >= 2:
        out.append({**team["reviewer"], "job": "reviewer"})
    for p, name in zip(out[1:], names or []):
        if name:
            p["role"] = name
    seen = set()
    for p in out[1:]:
        check_role(p["role"])
        if p["role"] in RESERVED_ROLES or p["role"] in seen:
            raise ValueError(f"agent name '{p['role']}' is reserved or used twice")
        seen.add(p["role"])
    return out


def job_text(team: dict, job: str | None) -> str | None:
    return (team.get("jobs") or {}).get(job) if job else None


def context_window(cat: dict, cli: str, model: str) -> int | None:
    spec = cat["cli"][cli]
    m = next((m for m in spec["models"] if m["id"] == model), {})
    return m.get("context_window") or spec.get("context_window")


def label(cat: dict, cli: str, model: str) -> str:
    return next((m["label"] for m in cat["cli"][cli]["models"] if m["id"] == model), model)


def _exe(name: str) -> str:
    return shutil.which(name) or name


def _hub_cmd(*args: str) -> list[str]:
    return [PY, "-m", "agent_hub", *args]


def codex_user_notify() -> list[str] | None:
    """The user's existing codex notify command, so ours can chain to it."""
    cfg = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    try:
        n = tomllib.loads(cfg.read_text(encoding="utf-8")).get("notify")
        return n if isinstance(n, list) and n else None
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def build_command(cat: dict, role: str, cli: str, model: str, effort: str, profile: str,
                  job: str | None = None, plugin_dirs: list[str] | None = None, mcp_extra: dict | None = None,
                  codex_args: list[str] | None = None, claude_args: list[str] | None = None,
                  resume: str | None = None) -> list[str]:
    """argv for an interactive agent. Writes per-agent config files under state/agents/<role>/."""
    adir = home() / "agents" / role
    adir.mkdir(parents=True, exist_ok=True)
    spec = cat["cli"][cli]
    serve = _hub_cmd("serve", "--profile", profile, "--role", role)
    env = {"AGENT_HUB_HOME": str(home()), "PYTHONPATH": str(PKG_ROOT)}
    prompt = PROTOCOL.format(role=role) + (f"\n{job}" if job else "")

    if cli == "claude":
        mcp = adir / "mcp.json"
        # hub-desk: browser / PC servers the run asked for sit next to the hub's own
        mcp.write_text(json.dumps({"mcpServers": {**(mcp_extra or {}), "agent_hub": {
            "command": serve[0], "args": serve[1:], "env": env}}}, indent=1), encoding="utf-8")
        # forward slashes + plain quotes survive both cmd.exe and bash hook runners
        hook = " ".join('"' + a.replace("\\", "/") + '"' for a in _hub_cmd("hook"))
        settings = adir / "claude-settings.json"  # attached with --settings; project settings untouched
        settings.write_text(json.dumps({"hooks": {ev: [{"hooks": [{"type": "command", "command": hook, "timeout": 10}]}]
                                                   for ev in HOOK_EVENTS}}, indent=1), encoding="utf-8")
        argv = [_exe(spec["command"]), spec["model_flag"], model, spec["effort_flag"], effort,
                "--mcp-config", str(mcp), "--settings", str(settings),
                # only this hub's server: the user-scope 'agent-hub' entry is another install with its own pane
                # host, and a second set of hub tools in the same session sends work to the wrong one
                "--strict-mcp-config",
                # the board tools only touch the hub's own SQLite; like codex, hub_cancel keeps its prompt
                "--allowedTools", ",".join(f"mcp__agent_hub__{t}" for t in
                                           BOARD_TOOLS + (MAIN_TOOLS if profile == "main" else ()))]
        for d in plugin_dirs or []:  # hub-desk: installed plugins the run asked for, this session only
            argv += ["--plugin-dir", d]
        argv += claude_args or []  # hub-desk: e.g. --no-chrome
        if resume:  # hub-desk: continue an earlier conversation (needs the same folder as before)
            argv += ["--resume", resume]
        return argv + ["--append-system-prompt", job if profile == "main" else prompt] if (job or profile != "main") \
            else argv

    if cli == "codex":
        notify = _hub_cmd("notify", "--role", role)
        chain = codex_user_notify()
        if chain:
            notify += ["--chain", json.dumps(chain)]
        # hub-desk: `codex resume <id>` takes the same options and continues that session
        argv = [_exe(spec["command"]), *(["resume", resume] if resume else []), spec["model_flag"], model,
                "-c", f'{spec["effort_config"]}="{effort}"',
                "-c", f"mcp_servers.agent_hub.command={json.dumps(serve[0])}",
                "-c", f"mcp_servers.agent_hub.args={_toml_list(serve[1:])}",
                "-c", "mcp_servers.agent_hub.env={" + ", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items()) + "}",
                "-c", f"notify={_toml_list(notify)}"]
        # codex asks before every MCP tool call and gives no hook for that wait; the board tools only
        # touch the hub's own SQLite, so pre-approve them; main also gets MAIN_TOOLS (hub_cancel keeps the prompt).
        for tool in BOARD_TOOLS + (MAIN_TOOLS if profile == "main" else ()):
            argv += ["-c", f'mcp_servers.agent_hub.tools.{tool}.approval_mode="approve"']
        # codex's built-in sub-agents made main delegate to Sol/Terra threads instead of the hub panes. codex 0.169
        # still exposes collaboration.spawn_agent with this off, so MAIN_GUIDE also forbids it by name.
        argv += ["--disable", "multi_agent"]
        argv += codex_args or []  # hub-desk: browser / PC servers switched off or isolated for this run
        if profile == "main":
            return argv + (["-c", f"developer_instructions={json.dumps(job)}"] if job else [])
        return argv if resume else argv + [prompt]  # a resumed worker already had its first message

    raise ValueError(f"no launcher for cli '{cli}'")


def ps_command(argv: list[str]) -> str:
    """One PowerShell command line: UTF-8 console, then the call operator with quoted args.
    Windows PowerShell 5.1 strips embedded double quotes from native args (k="v" arrives as k=v),
    so agent argv never goes through here: panes run `agent-hub pane <spec.json>` and Python launches the CLI."""
    quoted = " ".join("'" + a.replace("'", "''") + "'" for a in argv)
    return "chcp 65001 > $null; [Console]::OutputEncoding=[Text.Encoding]::UTF8; & " + quoted

