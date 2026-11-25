"""Browser and PC access for the agents, and ponytail as the default way of working.

Browser = Playwright MCP: each agent gets its own browser (--isolated), not the person's Chrome and its logins.
PC = agent-desktop MCP: reads and operates real desktop apps through accessibility.
Claude windows run with --strict-mcp-config, so these servers are added to their own MCP file. Codex windows load
the person's global config, which already has them; a run that turns one off disables it with -c for that run.
The person's config files are only read.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

HOME = Path.home()
BROWSER = ("playwright", "agent-browser")  # Codex's names in config.toml
# cua_repl comes from Codex's bundled computer-use plugin, not config.toml; switching the plugin off with -c does not
# remove it (checked with `codex mcp list`, codex 0.172.0), so the server itself is turned off
PC = ("agent-desktop", "cua_repl")
# Codex's own bundled plugins that do the same jobs; turned off with the matching switch
CODEX_PLUGINS = {"browser": ("browser@openai-bundled",),
                 "pc": ("computer-use@openai-bundled", "unified-computer-use@openai-bundled")}


def _codex_plugins() -> dict:
    try:
        return tomllib.loads((HOME / ".codex" / "config.toml").read_text(encoding="utf-8")).get("plugins") or {}
    except (OSError, ValueError):
        return {}


def _claude_user() -> dict:
    try:
        return json.loads((HOME / ".claude.json").read_text(encoding="utf-8")).get("mcpServers") or {}
    except (OSError, ValueError):
        return {}


def _codex_user() -> dict:
    try:
        return tomllib.loads((HOME / ".codex" / "config.toml").read_text(encoding="utf-8")).get("mcp_servers") or {}
    except (OSError, ValueError):
        return {}


def _ponytail() -> dict:
    claude = sorted((HOME / ".claude" / "plugins" / "cache" / "ponytail" / "ponytail").glob("*/skills/ponytail/SKILL.md"))
    codex = HOME / ".codex" / "skills" / "ponytail" / "SKILL.md"
    return {"claude": str(claude[-1]) if claude else None, "codex": str(codex) if codex.is_file() else None}


def desktop_problem(command: str) -> str | None:
    """agent-desktop's own health check; the reason it refuses to work, or None."""
    import subprocess
    try:
        r = subprocess.run([command, "status"], capture_output=True, timeout=10)
        j = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return str(e)[:160]
    return None if j.get("ok") else f"{(j.get('error') or {}).get('code', '?')}: {(j.get('error') or {}).get('message', '')}"[:160]


def available() -> dict:
    """What can be offered on this computer, for the setup page."""
    cl, cx, pt = _claude_user(), _codex_user(), _ponytail()
    pw = cl.get("playwright") or cx.get("playwright")
    desk = cx.get("agent-desktop")
    problem = desktop_problem(desk["command"]) if desk and Path(desk.get("command", "")).is_file() else None
    out = {
        "browser": {"ok": bool(pw), "detail": "Playwright MCP · 에이전트마다 따로 여는 브라우저 (사용자 Chrome·로그인과 분리)"
                    if pw else "Playwright MCP가 등록되어 있지 않습니다"},
        "pc": {"ok": bool(desk and Path(desk.get("command", "")).is_file()),
               "detail": "agent-desktop MCP · 실제 데스크톱 앱을 읽고 조작" if desk else "agent-desktop MCP가 등록되어 있지 않습니다"},
        "ponytail": {"ok": bool(pt["claude"] or pt["codex"]),
                     "detail": "가장 작은 완전한 변경으로 일하는 방식 (Claude: ponytail 플러그인, Codex: ponytail 스킬)"},
    }
    if problem:  # Codex still has its own computer use; Claude agents get no working PC tool
        out["pc"]["detail"] += f" · 지금 agent-desktop이 오류를 냅니다({problem}). Claude 에이전트는 PC 제어를 못 쓰고, Codex 에이전트는 Codex 자체 기능으로 씁니다"
    return out


def resolve(body: dict) -> dict:
    """The tool choice of a start request; anything not installed here is off."""
    t, ok = body.get("tools") or {}, available()
    return {k: bool(t.get(k, True)) and ok[k]["ok"] for k in ("browser", "pc", "ponytail")}


def claude_servers(choice: dict, in_wsl: bool = False) -> dict:
    """MCP servers to add to a Claude window's own MCP file. in_wsl: started from inside WSL."""
    out, cl, cx = {}, _claude_user(), _codex_user()
    if choice.get("browser"):
        pw = dict(cl.get("playwright") or cx.get("playwright") or {})
        if pw:
            args = list(pw.get("args") or [])
            pw["args"] = args + (["--isolated"] if "--isolated" not in args else [])  # two agents, two browsers
            out["playwright"] = {k: pw[k] for k in ("command", "args", "env") if k in pw}
    if choice.get("pc") and cx.get("agent-desktop"):
        d = cx["agent-desktop"]
        from agent_hub.wsl import to_linux
        out["agent-desktop"] = {"command": to_linux(d["command"]) if in_wsl else d["command"],
                                "args": list(d.get("args") or [])}
    return out


def codex_overrides(choice: dict) -> list[str]:
    """-c arguments for a Codex window: turn off what the run does not want, isolate the browser."""
    cx, plugins, args = _codex_user(), _codex_plugins(), []
    for name in BROWSER + PC:
        if not choice.get("pc" if name in PC else "browser"):
            if name in cx:
                args += ["-c", f"mcp_servers.{name}.enabled=false"]
            elif name == "cua_repl":  # defined by a plugin: a bare enabled=false fails to load, so give a whole entry
                args += ["-c", 'mcp_servers.cua_repl={command="none", enabled=false}']
    for kind, names in CODEX_PLUGINS.items():
        for name in names:
            if not choice.get(kind) and (plugins.get(name) or {}).get("enabled"):
                args += ["-c", f'plugins."{name}".enabled=false']
    pw = cx.get("playwright")
    if choice.get("browser") and pw and "--isolated" not in (pw.get("args") or []):
        args += ["-c", "mcp_servers.playwright.args=" + json.dumps(list(pw.get("args") or []) + ["--isolated"])]
    return args


def claude_args(choice: dict) -> list[str]:
    """Extra flags for a Claude window. Claude in Chrome would act in the person's own Chrome, with their logins;
    the browser offered here is Playwright's separate one, so Chrome stays off."""
    return ["--no-chrome"]


def guide(choice: dict, cli: str) -> str:
    """What the agent is told about these tools."""
    lines = []
    if choice.get("ponytail"):
        skill = _ponytail()["claude" if cli == "claude" else "codex"]
        how = "the ponytail:ponytail skill" if cli == "claude" else f"the ponytail skill ({skill})"
        lines.append(f"Way of working: ponytail by default. Follow {how} for every coding task: the smallest change "
                     "that fully solves the task, reuse what exists, no unrequested features or abstractions, and "
                     "finish with one line on what you skipped and any risk.")
    if choice.get("browser"):
        lines.append("Browser: you may use the Playwright MCP tools (browser_*) to open and test web pages. It is your "
                     "own browser, separate from the person's; never enter their passwords or payment details.")
    if choice.get("pc"):
        lines.append("PC: you may use the agent-desktop MCP tools (desktop_*) to see and operate desktop apps on this "
                     "computer, only when the task needs it. Other agents may share the screen: act on the window "
                     "you need, leave other windows alone, and do not purchase, send messages, delete data or "
                     "change system settings without the person's approval through main.")
    return "\n".join(lines)
