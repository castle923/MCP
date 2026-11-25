"""hub-desk: pick the teams in the browser, fix the choice, run the agents in pseudo consoles.

A team is one main (orchestrator) and its subs. Each team has its own agent-hub board (state/<run>/<team>/),
so teams do not see each other's mail. The mains hand work to their subs with the agent_hub MCP tools.

127.0.0.1 only. Every /api call needs the page token; agent_hub's pane calls (/ext) need the pane token.
Whoever reaches this server can type into a terminal.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from urllib.parse import quote

from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
from starlette.staticfiles import StaticFiles

from agent_hub.catalog import (MAIN_GUIDE, RESERVED_ROLES, check_role, job_text, label, load_catalog, load_team,
                               validate)
from agent_hub.context import ctx_text
from agent_hub.db import Board

from agent_hub import wsl

from . import folders, harness, tools, worktrees
from .pane import Pane

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).parent / "web"
HOST, PORT = "127.0.0.1", int(os.environ.get("HUB_DESK_PORT", "8765"))
TOKEN, EXT_TOKEN = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
MAX_TEAMS = 6  # a Codex agent holds ~50 processes: six teams is already a lot for one PC
CLI_LABEL = {"claude": "Claude Code", "codex": "Codex"}
COLS, ROWS = 120, 32
KNOWN = ROOT / "state" / "known_extras.json"  # extras already seen: the rest are shown as new
TRUST_PROMPT = re.compile(r"Yes, I trust this folder|Trust this folder\?|Trust and continue|Do you trust the", re.I)
NAME_RULE = {"pattern": r"^[\w-]{1,32}$", "reserved": list(RESERVED_ROLES), "hint": "글자·숫자·-·_ 1~32자"}
# Files the agents post are opened by the page from /board/<BOARD_CAP>/...: an iframe cannot send the token
# header, so the unguessable path is the key. It only reads files inside the run's workspace.
BOARD_CAP = secrets.token_urlsafe(24)
KINDS = {**dict.fromkeys((".html", ".htm"), "html"),
         **dict.fromkeys((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"), "image")}
LAST = ROOT / "state" / "last.json"  # the last started choice: the wizard is filled from it after a stop or a reload
RESOURCE_GUIDE = ("Resources: give back what you use. Before you call complete_task and before you end: stop every "
                  "process, server, watcher, container or background job you started for the task; close the files, "
                  "ports and connections you opened; delete temporary files you created (never deliverables, "
                  "sources or logs someone still needs). Do not stop anything you did not start. In the task result "
                  "list what you started, what you stopped, and anything left running with the reason.")
RESOURCE_MAIN = (" As main: before you report a phase or the project as finished, check that every sub reported its "
                 "cleanup. When a sub has no more work in this project, release it with hub_cancel(target) so its "
                 "processes are returned; hub_assign opens it again if work comes back. hub-desk itself ends every "
                 "remaining process of an agent when the agent exits, and of all teams when the user stops the run.")
BOARD_GUIDE = ("Board: when you produce something the user should look at (a result summary, a report, a chart, "
               "an HTML page or dashboard they asked for), save it as a file inside the project folder and call "
               "the agent_hub tool board_post(title, path, note). hub-desk shows it to the user in a board window "
               "over the dashboard. Post again after you update the file. A dashboard is one self-contained .html "
               "file (inline CSS/JS, relative paths for its own assets, no CDN).")
# The main's guide names codex's built-in agent tools; Claude Code has its own.
CLAUDE_MAIN = ("\nAs Claude Code: never use the Agent/Task tool, workflows or other built-in subagents for this "
               "team's work. The hub agents are the team.")


class Team:
    def __init__(self, tid: str, name: str, home: Path):
        self.id, self.name, self.home = tid, name, home
        self.panes: dict[str, Pane] = {}
        self.specs: dict[str, dict] = {}
        self.spec_list: list[dict] = []  # as started (main first): what last.json and config.json record
        home.mkdir(parents=True, exist_ok=True)
        (home / "ext.json").write_text(json.dumps(
            {"url": f"http://{HOST}:{PORT}/ext/{tid}", "token": EXT_TOKEN}), encoding="utf-8")
        self.board = Board(home / "hub.sqlite3")

    def launch(self, role: str, loop) -> Pane:
        """Start the agent whose spec is in agents/<role>/pane.json (written here or by agent_hub's open_pane)."""
        check_role(role)
        path = self.home / "agents" / role / "pane.json"
        spec = json.loads(path.read_text(encoding="utf-8"))
        in_wsl = wsl.parse_unc(STATE["workspace"])
        if not spec.get("addon_done"):  # also for agents a main opens later: same harness, same extras
            if in_wsl:  # the distro's own CLI, in the Linux folder
                spec["wsl"] = {"distro": in_wsl[0], "cwd": in_wsl[1]}
            text, dirs = harness.addon(STATE["addon"], spec["cli"], STATE["workspace"], wsl=bool(in_wsl))
            choice = STATE["addon"].get("tools") or {}
            spec.setdefault("name", role)
            spec["job_text"] = "\n".join(filter(None, [
                spec.get("job_text"), f"Project: {STATE['project']}.", tools.guide(choice, spec["cli"]),
                RESOURCE_GUIDE + (RESOURCE_MAIN if spec.get("profile") == "main" else ""), BOARD_GUIDE, text]))
            spec["plugin_dirs"], spec["addon_done"] = dirs, True
            spec["mcp_extra"] = tools.claude_servers(choice, bool(in_wsl)) if spec["cli"] == "claude" else {}
            # a Codex inside WSL has its own config: switching off servers the Windows config has would break it
            spec["codex_args"] = tools.codex_overrides(choice) if spec["cli"] == "codex" and not in_wsl else []
            spec["claude_args"] = tools.claude_args(choice) if spec["cli"] == "claude" else []
            wt = STATE.get("git")
            if wt:
                if spec.get("job") == "builder":
                    spec["cwd"] = wt.ensure(self.id, role)
                    item = wt.items[f"{self.id}/{role}"]
                    if item.get("resumed"):
                        spec["job_text"] += ("\nThis copy still holds your unmerged changes from an earlier run "
                                             f"(see `git diff {item['base']}`): continue from them.")
                    spec["job_text"] += (
                        f"\nYour folder is your own git worktree (a separate copy of the project) on branch "
                        f"{item['branch']}. Work only here: do not edit {STATE['workspace']}, do not switch branches, "
                        "do not push. The person reviews your changes on the dashboard and merges them into the "
                        "project; until then they exist only in this copy. Tell main the files you changed.")
                else:  # main and reviewers stay in the project and read the builders' copies
                    copies = str(wt.base)
                    # Claude needs the folder named to read it; Codex reads anywhere and refuses --add-dir
                    # ("only for writable roots") under the person's sandbox setting
                    spec["claude_args"] += ["--add-dir", copies] if spec["cli"] == "claude" else []
                    spec["job_text"] += (
                        f"\nGit copies: each builder works in its own git worktree under {copies}\\<team>-<name> "
                        "(see `git -C <that folder> diff HEAD` and its commits since the start). Their changes reach "
                        f"the project folder {STATE['workspace']} only when the person merges them on the dashboard, "
                        "so review builder work in its copy, not in the project folder.")
            path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        old = self.panes.get(role)
        if old and spec.pop("resume", None):
            path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        if old:
            old.close()
        env = {**os.environ, "AGENT_HUB_ROLE": role, "AGENT_HUB_HOME": str(self.home),
               "PYTHONPATH": str(ROOT), "AGENT_HUB_NO_DRAIN": "1"}
        # a WSL agent's launcher starts in this folder; wsl.exe --cd takes it to the Linux folder
        pane = Pane([sys.executable, "-m", "agent_hub", "pane", str(path)], COLS, ROWS, loop,
                    cwd=str(ROOT) if in_wsl else spec.get("cwd") or STATE["workspace"], env=env)
        self.panes[role], self.specs[role] = pane, spec
        return pane

    def close(self) -> tuple[int, int]:
        """End every agent of the team. Returns (processes ended, processes still alive)."""
        done = [pane.close() for pane in self.panes.values()]
        self.board.close()
        return sum(d[0] for d in done), sum(d[1] for d in done)


STATE: dict = {"phase": "setup", "workspace": None, "project": "", "started_at": None, "teams": {}, "addon": {},
               "released": None, "run": None, "next_team": 0, "harness_choice": {"mode": "auto", "path": ""},
               "git": None}


def sub_roles(subs: list[dict]) -> list[str]:
    """builder, builder2, ..., reviewer, reviewer2, ... in the order given."""
    seen: dict[str, int] = {}
    out = []
    for s in subs:
        seen[s["job"]] = seen.get(s["job"], 0) + 1
        out.append(s["job"] + (str(seen[s["job"]]) if seen[s["job"]] > 1 else ""))
    return out


def sub_names(subs: list[dict]) -> list[str]:
    """A sub's name is its id in the team (the main calls it by that name): the user's, else the default."""
    out = []
    for default, s in zip(sub_roles(subs), subs):
        name = str(s.get("name") or default).strip()
        try:
            check_role(name)
        except ValueError:
            raise ValueError(f"에이전트 이름 '{name}'은(는) 쓸 수 없습니다 ({NAME_RULE['hint']})") from None
        if name in RESERVED_ROLES or name in out:
            raise ValueError(f"에이전트 이름 '{name}'은(는) 예약되어 있거나 팀 안에서 겹칩니다")
        out.append(name)
    return out


def plan(body: dict) -> tuple[str, str, list[list[dict]]]:
    """Check a start request against the catalog. Returns workspace, project name and, per team, the pane specs."""
    cat, team_cfg = load_catalog(), load_team()
    jobs = team_cfg.get("jobs") or {}
    max_subs = team_cfg.get("max_workers", 6)
    project = " ".join(str(body.get("project") or "").split())
    if not 1 <= len(project) <= 60:
        raise ValueError("프로젝트 이름을 1~60자로 입력하세요")
    workspace = str(body.get("workspace") or "").strip()
    if not workspace or not Path(workspace).is_dir():
        raise ValueError(f"작업 폴더가 없습니다: {workspace or '(비어 있음)'}")
    teams = body.get("teams")
    if not isinstance(teams, list) or not 1 <= len(teams) <= MAX_TEAMS:
        raise ValueError(f"메인은 1~{MAX_TEAMS}개여야 합니다")
    return str(Path(workspace).resolve()), project, [team_specs(t) for t in teams]


def team_specs(t: dict) -> list[dict]:
    """Check one team (main + subs) against the catalog and build its pane specs. The team's name rides on the main."""
    cat, team_cfg = load_catalog(), load_team()
    jobs = team_cfg.get("jobs") or {}
    max_subs = team_cfg.get("max_workers", 6)
    if not isinstance(t, dict):
        raise ValueError("팀 설정이 올바르지 않습니다")
    team_name = " ".join(str(t.get("name") or "").split())
    if len(team_name) > 20:
        raise ValueError("팀 이름은 20자까지 쓸 수 있습니다")
    main, subs = t.get("main") or {}, t.get("subs") or []
    if len(subs) > max_subs:
        raise ValueError(f"서브는 메인당 최대 {max_subs}개입니다")
    for s in subs:
        if s.get("job") not in jobs:
            raise ValueError(f"알 수 없는 역할: {s.get('job')}")
    roles = sub_names(subs)
    line = (", ".join(f'"{r}" (job {s["job"]}, {s["cli"]})' for r, s in zip(roles, subs))
            + ". Address them by these exact names: hub_assign(role=<name>, ...). A name that is not running "
              "(for example the job word 'builder' when the sub is called something else) opens a NEW agent, "
              "so do that only when you really want another one"
            ) if subs else "no sub agents"
    specs = []
    shown = " ".join(str(main.get("name") or "main").split())[:24] or "main"  # the main's id stays 'main'
    for role, job, a in [("main", "main", main)] + [(r, s["job"], s) for r, s in zip(roles, subs)]:
        cli, model = a.get("cli"), a.get("model")
        effort = validate(cat, cli, model, a.get("effort"))
        if job == "main":
            text = MAIN_GUIDE + (CLAUDE_MAIN if cli == "claude" else "") + f"\nYour team in this session: {line}."
        else:
            text = job_text(team_cfg, job)
        specs.append({"role": role, "name": shown if job == "main" else role,
                      "cli": cli, "model": model, "effort": effort, "source": "hub-desk",
                      "profile": "main" if job == "main" else "worker", "job": job, "job_text": text})
    specs[0]["team_name"] = team_name
    return specs


def guard(request, token: str = TOKEN) -> Response | None:
    origin = request.headers.get("origin")
    if origin and origin not in (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"):
        return JSONResponse({"error": "bad origin"}, status_code=403)
    if not secrets.compare_digest(request.headers.get("x-token", ""), token):
        return JSONResponse({"error": "bad token"}, status_code=403)
    return None


def find_pane(request) -> Pane | None:
    team = STATE["teams"].get(request.path_params["team"])
    return team.panes.get(request.path_params["role"]) if team else None


# ---- page ---------------------------------------------------------------------
async def index(request):
    html = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


async def catalog(request):
    if (bad := guard(request)):
        return bad
    cat, team = load_catalog(), load_team()
    return JSONResponse({
        "workspace": STATE["workspace"] or os.getcwd(),
        "project": STATE["project"], "name_rule": NAME_RULE, "last": read_last(),
        "limits": {"max_teams": MAX_TEAMS, "max_subs": team.get("max_workers", 6)},
        "jobs": list((team.get("jobs") or {}).keys()),
        "cli": {k: {"label": CLI_LABEL.get(k, k), "efforts": s["efforts"],
                    "default_effort": cat.get("default_effort", "medium"),
                    "models": [{"id": m["id"], "label": m["label"], "verified": bool(m.get("verified"))}
                               for m in s["models"]]} for k, s in cat["cli"].items()},
        "defaults": {k: {f: team[k][f] for f in ("cli", "model", "effort")} for k in ("main", "builder", "reviewer")},
    })


def team_state(team: Team, cat: dict) -> dict:
    rows = {a["role"]: a for a in team.board.agents()}
    agents = []
    for role, spec in team.specs.items():
        row, pane = rows.get(role) or {}, team.panes[role]
        status, note = (row.get("status") or "configuring") if pane.alive else "ended", None
        # A CLI that has not started its session yet may be waiting at "trust this folder?" (new folders, git copies).
        # That needs the person in the terminal; anything typed there by the hub could pick "No, exit".
        if status in ("configuring", "booting") and not row.get("session_id") and TRUST_PROMPT.search(pane.tail(1500)):
            status, note = "waiting_approval", "폴더 신뢰 확인: 터미널에서 직접 답하세요"
        agents.append({
            "role": role, "name": spec.get("name") or role,
            "job": spec.get("job"), "cli": spec["cli"], "model": spec["model"],
            "model_label": label(cat, spec["cli"], spec["model"]), "effort": spec.get("effort"),
            "alive": pane.alive, "procs": pane.procs, "status": status, "note": note,
            "ctx": ctx_text(row.get("ctx_used"), row.get("ctx_window")) if row.get("ctx_used") else None,
            "unread": len(team.board.inbox(role)), "breaker": row.get("breaker") or "healthy",
            "paused": bool(row.get("paused"))})
    tasks = []
    for t in team.board.q("SELECT * FROM tasks ORDER BY created_at"):
        first = (t["spec"] or "").splitlines()[0] if t["spec"] else ""
        tasks.append({"id": t["id"], "assignee": t["assignee"], "status": t["status"],
                      "objective": first.removeprefix("OBJECTIVE:").strip(), "updated_at": t["updated_at"]})
    events = []
    for e in reversed(team.board.q("SELECT * FROM events WHERE kind != 'PostToolUse' ORDER BY seq DESC LIMIT 40")):
        data = json.loads(e["data"] or "{}")
        text = " ".join(f"{k}={v}" for k, v in data.items() if v not in (None, "") and k != "input")
        events.append({"seq": e["seq"], "ts": e["ts"], "role": e["role"], "kind": e["kind"], "text": text[:160],
                       # what the office animates: never the tool input (it can hold file contents)
                       "data": {k: data[k] for k in ("id", "to", "act", "subject", "assignee", "tool", "title", "via",
                                                     "messages") if data.get(k) is not None}})
    posts, roots = [], board_roots()
    for e in team.board.q("SELECT * FROM events WHERE kind='board_post' ORDER BY seq"):
        d = json.loads(e["data"] or "{}")
        p = Path(d.get("path") or "")
        post = {"id": e["seq"], "ts": e["ts"], "role": e["role"],
                "name": (team.specs.get(e["role"]) or {}).get("name") or e["role"],
                "title": d.get("title") or p.name, "note": d.get("note") or "",
                "file": str(p), "kind": "missing", "url": None}
        root = next((k for k, r in roots.items() if p.is_file() and p.is_relative_to(r)), None)
        if root is not None:
            rel = (Path(root) / p.relative_to(roots[root])).as_posix() if root else p.relative_to(roots[root]).as_posix()
            post.update(file=rel, kind=KINDS.get(p.suffix.lower(), "text"),
                        url=f"/board/{BOARD_CAP}/{team.id}/{quote(rel)}")
        posts.append(post)
    return {"id": team.id, "name": team.name, "agents": agents, "tasks": tasks, "events": events, "posts": posts}


def board_roots() -> dict[str, Path]:
    """Folders a posted file may live in: the project, and (with git copies) the builders' copies as '~wt/...'."""
    roots = {"": Path(STATE["workspace"]).resolve()}
    if STATE.get("git"):
        roots["~wt"] = STATE["git"].base.resolve()
    return roots


async def board_file(request):
    """A posted file, or a file next to it (a page's own css/js/images). Inside the workspace or a builder's copy."""
    if not secrets.compare_digest(request.path_params["cap"], BOARD_CAP) or STATE["phase"] != "running" \
            or request.path_params["team"] not in STATE["teams"]:
        return Response(status_code=404)
    roots, rel = board_roots(), request.path_params["path"]
    key = "~wt" if rel.startswith("~wt/") and "~wt" in roots else ""
    base = roots[key]
    p = (base / (rel[4:] if key else rel)).resolve()
    if not p.is_file() or not p.is_relative_to(base):
        return Response(status_code=404)
    # agent-written pages get no access to this origin, even when opened outside the board's iframe
    return FileResponse(p, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                    "Content-Security-Policy": "sandbox allow-scripts"})


def _wt(request):
    wt = STATE.get("git")
    key = f"{request.path_params['team']}/{request.path_params['role']}"
    if not wt or key not in wt.items:
        raise KeyError("이 에이전트에는 작업 사본이 없습니다")
    return wt, key


async def changes(request):
    """Every builder copy of the run with its changed files (git work, so asked for, not polled)."""
    if (bad := guard(request)):
        return bad
    wt = STATE.get("git")
    if not wt:
        return JSONResponse({"enabled": False, "items": []})
    items = []
    for key in list(wt.items):
        try:
            items.append(await asyncio.to_thread(wt.changes, key))
        except (RuntimeError, OSError) as e:
            items.append({**{k: wt.items[key][k] for k in ("team", "role", "path", "branch")}, "error": str(e)})
    return JSONResponse({"enabled": True, "repo": wt.info, "items": items})


async def change_diff(request):
    if (bad := guard(request)):
        return bad
    try:
        wt, key = _wt(request)
        return Response(await asyncio.to_thread(wt.diff, key), media_type="text/plain; charset=utf-8")
    except (KeyError, RuntimeError) as e:
        return JSONResponse({"error": str(e).strip("'")}, status_code=404)


async def change_merge(request):
    if (bad := guard(request)):
        return bad
    try:
        wt, key = _wt(request)
        return JSONResponse({"ok": True, **await asyncio.to_thread(wt.merge, key)})
    except KeyError as e:
        return JSONResponse({"error": str(e).strip("'")}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)


async def change_discard(request):
    if (bad := guard(request)):
        return bad
    try:
        wt, key = _wt(request)
        team = STATE["teams"].get(request.path_params["team"])
        pane = team.panes.get(request.path_params["role"]) if team else None
        if pane and pane.alive:
            return JSONResponse({"error": "에이전트가 실행 중입니다. 그 에이전트를 먼저 끝낸 뒤 버리세요."}, status_code=409)
        await asyncio.to_thread(wt.discard, key)
        return JSONResponse({"ok": True})
    except KeyError as e:
        return JSONResponse({"error": str(e).strip("'")}, status_code=404)


async def scan(request):
    if (bad := guard(request)):
        return bad
    try:
        workspace = str((await request.json()).get("workspace") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        workspace = ""
    if not workspace or not Path(workspace).is_dir():
        return JSONResponse({"error": f"작업 폴더가 없습니다: {workspace or '(비어 있음)'}"}, status_code=400)
    found = await asyncio.to_thread(harness.scan, workspace, KNOWN)
    for e in found["extras"]:
        e.pop("path")  # the page picks by id; the server looks the path up again at start
    found["tools"] = await asyncio.to_thread(tools.available)
    found["git"] = await asyncio.to_thread(worktrees.repo_info, workspace)
    return JSONResponse(found)


async def folder_list(request):
    if (bad := guard(request)):
        return bad
    return JSONResponse({"folders": await asyncio.to_thread(folders.candidates, ROOT / "state")})


async def folder_pick(request):
    """Opens Windows' folder dialog on this computer and waits for the person to choose."""
    if (bad := guard(request)):
        return bad
    try:
        initial = str((await request.json()).get("initial") or "")
    except (json.JSONDecodeError, AttributeError):
        initial = ""
    try:
        path = await asyncio.to_thread(folders.pick, initial)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    return JSONResponse({"path": path, **(folders.describe(path) if path else {})})


async def state(request):
    if (bad := guard(request)):
        return bad
    if STATE["phase"] != "running":
        return JSONResponse({"phase": "setup", "released": STATE["released"]})
    cat = load_catalog()
    addon = STATE["addon"]
    return JSONResponse({"phase": "running", "workspace": STATE["workspace"], "started_at": STATE["started_at"],
                         "project": STATE["project"],
                         "harness": addon.get("harness"), "tools": addon.get("tools"),
                         "extras": [{"name": e["name"], "kind": e["kind"]} for e in addon.get("extras") or []],
                         "teams": [team_state(t, cat) for t in STATE["teams"].values()]})


async def start(request):
    if (bad := guard(request)):
        return bad
    if STATE["phase"] == "running":
        return JSONResponse({"error": "이미 실행 중입니다. 먼저 정지하세요."}, status_code=409)
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return await begin(body)


async def begin(body: dict, resume: list[dict] | None = None, note: str = "") -> JSONResponse:
    """Start a run. `resume`: per team, {role: session id} from an earlier run, so those agents continue."""
    try:
        workspace, project, teams = plan(body)
        addon = harness.resolve(body, workspace, KNOWN)
        addon["tools"] = tools.resolve(body)
        # Windows git on a WSL repository is slow and refuses it as owned by another user: no copies there
        git_info = worktrees.repo_info(workspace) if body.get("worktrees", True) and not wsl.parse_unc(workspace) else None
        addon["worktrees"] = bool(git_info)
    except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in project)[:30]
    run = ROOT / "state" / f"{time.strftime('%Y%m%dT%H%M%S')}-{slug}"
    run.mkdir(parents=True, exist_ok=True)
    # the fixed choice, as it was started
    harness.remember(KNOWN, addon.pop("all"))
    h = body.get("harness") or {"mode": "auto"}
    STATE["git"] = (worktrees.Worktrees(ROOT / "state" / "worktrees" / worktrees.repo_key(git_info["root"]), workspace,
                                        git_info) if git_info else None)
    STATE.update(phase="running", workspace=workspace, project=project, started_at=time.time(), teams={}, addon=addon,
                 released=None, run=run, next_team=0,
                 harness_choice={"mode": h.get("mode", "auto"), "path": str(h.get("path") or "")})
    for i, specs in enumerate(teams):
        for spec in specs:
            sid = (resume[i] if resume and i < len(resume) else {}).get(spec["role"])
            if sid:
                spec["resume"] = sid
                spec["job_text"] = (spec.get("job_text") or "") + f"\n{note}"
    loop = asyncio.get_running_loop()
    try:
        for specs in teams:
            add_team(specs, loop)
    except Exception as e:  # a CLI that cannot start: leave nothing half running
        stop_all()
        return JSONResponse({"error": f"시작 실패: {e}"}, status_code=500)
    return JSONResponse({"ok": True})


def save_choice() -> None:
    """The run as it stands (teams added later included): state/last.json for the wizard, config.json for the record."""
    pick = ("cli", "model", "effort", "name")
    addon = STATE["addon"]
    LAST.write_text(json.dumps({
        "project": STATE["project"], "workspace": STATE["workspace"],
        "teams": [{"name": t[0].get("team_name") or "", "main": {k: t[0][k] for k in pick},
                   "subs": [{"job": a["job"], **{k: a[k] for k in pick}} for a in t[1:]]}
                  for t in (team.spec_list for team in STATE["teams"].values())],
        "harness": STATE["harness_choice"], "extras": [e["id"] for e in addon.get("extras") or []],
        "tools": addon.get("tools")}, ensure_ascii=False, indent=1), encoding="utf-8")
    (STATE["run"] / "config.json").write_text(json.dumps(
        {"project": STATE["project"], "workspace": STATE["workspace"],
         "teams": [t.spec_list for t in STATE["teams"].values()], **addon},
        ensure_ascii=False, indent=1), encoding="utf-8")


def add_team(specs: list[dict], loop) -> "Team":
    """Start one team of the running project. Its agents get the run's harness, extras and tools on launch."""
    STATE["next_team"] = n = STATE.get("next_team", 0) + 1  # never reused, even after a team is stopped
    tid = f"team{n}"
    team = STATE["teams"][tid] = Team(tid, specs[0].get("team_name") or f"팀 {n}", STATE["run"] / tid)
    team.spec_list = specs
    specs[0]["team_id"] = tid
    save_choice()
    for spec in specs:
        adir = team.home / "agents" / spec["role"]
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "pane.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        team.launch(spec["role"], loop)
    return team


async def new_team(request):
    """Add a team (main + subs) to the running project."""
    if (bad := guard(request)):
        return bad
    if STATE["phase"] != "running":
        return JSONResponse({"error": "실행 중일 때만 팀을 추가할 수 있습니다."}, status_code=409)
    if len(STATE["teams"]) >= MAX_TEAMS:
        return JSONResponse({"error": f"팀은 최대 {MAX_TEAMS}개입니다."}, status_code=409)
    try:
        specs = team_specs(await request.json())
    except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    tid = f"team{STATE.get('next_team', 0) + 1}"
    try:
        team = add_team(specs, asyncio.get_running_loop())
    except Exception as e:  # this team only: the others keep running
        broken = STATE["teams"].pop(tid, None)
        if broken:
            await asyncio.to_thread(broken.close)
            save_choice()
        return JSONResponse({"error": f"팀을 시작하지 못했습니다: {e}"}, status_code=500)
    return JSONResponse({"ok": True, "team": team.id})


def read_last() -> dict | None:
    try:
        return json.loads(LAST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stop_all() -> dict:
    """End every team and say what was given back: agents, processes ended, processes still alive."""
    agents = sum(len(t.panes) for t in STATE["teams"].values())
    done = [team.close() for team in STATE["teams"].values()]
    released = {"agents": agents, "processes": sum(d[0] for d in done), "left": sum(d[1] for d in done)}
    if STATE.get("git"):  # after the agents: no process holds the copies' files any more
        try:
            released["worktrees"] = STATE["git"].close()
        except Exception as e:  # never block a stop on git
            released["worktrees"] = {"removed": 0, "kept": [], "error": str(e)[:200]}
        STATE["git"] = None
    STATE.update(phase="setup", teams={}, started_at=None, addon={}, released=released if agents else STATE["released"])
    return released


def _agent(request):
    team = STATE["teams"].get(request.path_params["team"])
    role = request.path_params["role"]
    if not team or role not in team.specs:
        raise KeyError("그런 에이전트가 없습니다")
    return team, role


async def say(request):
    """A message from the person to one agent, typed into its terminal."""
    if (bad := guard(request)):
        return bad
    try:
        team, role = _agent(request)
        text = str((await request.json()).get("text") or "").strip()
    except KeyError as e:
        return JSONResponse({"error": str(e).strip("'")}, status_code=404)
    except (json.JSONDecodeError, AttributeError):
        text = ""
    if not text or len(text) > 20000:
        return JSONResponse({"error": "보낼 내용을 1~20000자로 쓰세요"}, status_code=400)
    pane = team.panes[role]
    if not pane.alive:
        return JSONResponse({"error": "에이전트가 끝났습니다. 다시 시작한 뒤 보내세요."}, status_code=409)
    row = team.board.agent(role) or {}
    if not row.get("session_id") and TRUST_PROMPT.search(pane.tail(1500)):
        # Enter here would answer the prompt, and Claude's default is "No, exit"
        return JSONResponse({"error": "이 에이전트는 폴더 신뢰 확인을 기다립니다. 터미널에서 먼저 답하세요."}, status_code=409)
    # Codex took an Enter 0.6 s after the paste as part of it (tested, codex 0.172.0); 1.5 s submits
    pane.say(text, asyncio.get_running_loop(), 1.5 if team.specs[role]["cli"] == "codex" else 0.6)
    team.board.event("human", "say", {"to": role, "subject": " ".join(text.split())[:80]})
    return JSONResponse({"ok": True})


async def agent_stop(request):
    """End one agent and give back what it started. Its git copy (if any) stays until the run ends."""
    if (bad := guard(request)):
        return bad
    try:
        team, role = _agent(request)
    except KeyError as e:
        return JSONResponse({"error": str(e).strip("'")}, status_code=404)
    ended, left = await asyncio.to_thread(team.panes[role].close)
    team.board.update_agent(role, status="ended", ended_at=time.time())
    team.board.event("human", "agent_stop", {"target": role})
    return JSONResponse({"ok": True, "released": {"agents": 1, "processes": ended, "left": left}})


async def agent_restart(request):
    """Start one agent again (a fresh CLI session) with the same settings, folder and instructions."""
    if (bad := guard(request)):
        return bad
    try:
        team, role = _agent(request)
    except KeyError as e:
        return JSONResponse({"error": str(e).strip("'")}, status_code=404)
    await asyncio.to_thread(team.panes[role].close)
    try:
        team.launch(role, asyncio.get_running_loop())
    except Exception as e:
        return JSONResponse({"error": f"다시 시작하지 못했습니다: {e}"}, status_code=500)
    team.board.event("human", "agent_restart", {"target": role})
    return JSONResponse({"ok": True})


async def team_stop(request):
    """End one team: its agents, their processes, and its git copies that hold nothing unmerged.
    Stopping the last team ends the run."""
    if (bad := guard(request)):
        return bad
    tid = request.path_params["team"]
    if tid not in STATE["teams"]:
        return JSONResponse({"error": "그런 팀이 없습니다"}, status_code=404)
    if len(STATE["teams"]) == 1:
        return JSONResponse({"ok": True, "ended_run": True, "released": await asyncio.to_thread(stop_all)})
    team = STATE["teams"].pop(tid)
    agents = len(team.panes)
    ended, left = await asyncio.to_thread(team.close)
    released = {"agents": agents, "processes": ended, "left": left}
    if STATE.get("git"):
        try:
            released["worktrees"] = await asyncio.to_thread(STATE["git"].close, tid)
        except Exception as e:
            released["worktrees"] = {"removed": 0, "kept": [], "error": str(e)[:200]}
    save_choice()
    return JSONResponse({"ok": True, "ended_run": False, "released": released})


def _run_dir(name: str) -> Path | None:
    p = (ROOT / "state" / name).resolve()
    ok = p.parent == (ROOT / "state").resolve() and (p / "config.json").is_file() and p.name != "worktrees"
    return p if ok else None


def _run_boards(run: Path, cfg: dict):
    """(team id, team name, specs, board rows) for each team of a recorded run."""
    import sqlite3
    for i, specs in enumerate(cfg.get("teams") or []):
        tid = specs[0].get("team_id") or f"team{i + 1}"  # runs before team ids were recorded: in order
        db = run / tid / "hub.sqlite3"
        rows = {"agents": [], "tasks": [], "posts": []}
        if db.is_file():
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            try:
                rows["agents"] = [dict(r) for r in con.execute("SELECT role, session_id, status FROM agents")]
                rows["tasks"] = [dict(r) for r in con.execute(
                    "SELECT id, assignee, status, spec, result, updated_at FROM tasks ORDER BY created_at")]
                rows["posts"] = [dict(r) for r in con.execute(
                    "SELECT seq, ts, role, data FROM events WHERE kind='board_post' ORDER BY seq")]
            finally:
                con.close()
        yield tid, specs[0].get("team_name") or f"팀 {i + 1}", specs, rows


def _started(run: Path) -> float:
    try:
        return time.mktime(time.strptime(run.name[:15], "%Y%m%dT%H%M%S"))
    except ValueError:
        return run.stat().st_mtime


async def runs(request):
    """Earlier runs, newest first."""
    if (bad := guard(request)):
        return bad

    def read():
        out = []
        for cfg_file in sorted((ROOT / "state").glob("*/config.json"), reverse=True)[:60]:
            run = cfg_file.parent
            try:
                cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            teams = list(_run_boards(run, cfg))
            tasks = [t for _, _, _, r in teams for t in r["tasks"]]
            out.append({"id": run.name, "project": cfg.get("project") or run.name, "workspace": cfg.get("workspace"),
                        "workspace_ok": bool(cfg.get("workspace")) and Path(cfg["workspace"]).is_dir(),
                        "started_at": _started(run), "teams": len(teams),
                        "agents": sum(len(s) for _, _, s, _ in teams),
                        "tasks": {"total": len(tasks), "done": sum(t["status"] == "done" for t in tasks),
                                  "failed": sum(t["status"] == "failed" for t in tasks)},
                        "posts": sum(len(r["posts"]) for _, _, _, r in teams),
                        "resumable": any(a.get("session_id") for _, _, _, r in teams for a in r["agents"]),
                        "current": STATE["phase"] == "running" and STATE.get("run") == run})
        return out
    return JSONResponse({"runs": await asyncio.to_thread(read)})


async def run_detail(request):
    if (bad := guard(request)):
        return bad
    run = _run_dir(request.path_params["run"])
    if not run:
        return JSONResponse({"error": "그런 실행 기록이 없습니다"}, status_code=404)
    cfg = json.loads((run / "config.json").read_text(encoding="utf-8"))
    ws = Path(cfg.get("workspace") or ".")
    teams = []
    for tid, name, specs, rows in _run_boards(run, cfg):
        sessions = {a["role"]: a.get("session_id") for a in rows["agents"]}
        posts = []
        for e in rows["posts"]:
            d = json.loads(e["data"] or "{}")
            p = Path(d.get("path") or "")
            rel = _history_rel(p, ws)
            posts.append({"id": e["seq"], "ts": e["ts"], "role": e["role"], "title": d.get("title") or p.name,
                          "note": d.get("note") or "", "file": rel or str(p),
                          "kind": KINDS.get(p.suffix.lower(), "text") if rel else "missing",
                          "url": f"/history/{BOARD_CAP}/{quote(run.name)}/{quote(rel)}" if rel else None})
        teams.append({"id": tid, "name": name,
                      "agents": [{"role": s["role"], "name": s.get("name") or s["role"], "job": s.get("job"),
                                  "cli": s["cli"], "model": s["model"], "effort": s.get("effort"),
                                  "resumable": bool(sessions.get(s["role"]))} for s in specs],
                      "tasks": [{"id": t["id"], "assignee": t["assignee"], "status": t["status"],
                                 "objective": ((t["spec"] or "").splitlines() or [""])[0].removeprefix("OBJECTIVE:").strip(),
                                 "result": (t["result"] or "")[:4000], "updated_at": t["updated_at"]} for t in rows["tasks"]],
                      "posts": posts})
    return JSONResponse({"id": run.name, "project": cfg.get("project"), "workspace": cfg.get("workspace"),
                         "started_at": _started(run), "harness": cfg.get("harness"),
                         "extras": [e.get("name") for e in cfg.get("extras") or []], "tools": cfg.get("tools"),
                         "worktrees": cfg.get("worktrees"), "teams": teams})


def _history_rel(p: Path, ws: Path) -> str | None:
    """A recorded post's path as '<rel>' (project) or '~wt/<rel>' (a builder copy), if the file is still there."""
    try:
        p = p.resolve()
    except OSError:
        return None
    if not p.is_file():
        return None
    copies = (ROOT / "state" / "worktrees").resolve()
    if p.is_relative_to(ws.resolve()):
        return p.relative_to(ws.resolve()).as_posix()
    if p.is_relative_to(copies):
        return "~wt/" + p.relative_to(copies).as_posix()
    return None


async def history_file(request):
    """A file posted in an earlier run (or one next to it), read from that run's project folder or the copies."""
    run = _run_dir(request.path_params["run"])
    if not secrets.compare_digest(request.path_params["cap"], BOARD_CAP) or not run:
        return Response(status_code=404)
    cfg = json.loads((run / "config.json").read_text(encoding="utf-8"))
    rel = request.path_params["path"]
    base = (ROOT / "state" / "worktrees").resolve() if rel.startswith("~wt/") else Path(cfg.get("workspace") or "").resolve()
    p = (base / (rel[4:] if rel.startswith("~wt/") else rel)).resolve()
    if not cfg.get("workspace") or not p.is_file() or not p.is_relative_to(base):
        return Response(status_code=404)
    return FileResponse(p, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                    "Content-Security-Policy": "sandbox allow-scripts"})


async def run_resume(request):
    """Start a new run with an earlier run's settings; agents that had a session continue that conversation."""
    if (bad := guard(request)):
        return bad
    if STATE["phase"] == "running":
        return JSONResponse({"error": "이미 실행 중입니다. 먼저 정지하세요."}, status_code=409)
    run = _run_dir(request.path_params["run"])
    if not run:
        return JSONResponse({"error": "그런 실행 기록이 없습니다"}, status_code=404)
    cfg = json.loads((run / "config.json").read_text(encoding="utf-8"))
    teams, resume = [], []
    for tid, name, specs, rows in _run_boards(run, cfg):
        pick = ("name", "cli", "model", "effort")
        teams.append({"name": specs[0].get("team_name") or "", "main": {k: specs[0].get(k) for k in pick},
                      "subs": [{"job": s.get("job"), **{k: s.get(k) for k in pick}} for s in specs[1:]]})
        resume.append({a["role"]: a["session_id"] for a in rows["agents"] if a.get("session_id")})
    h = cfg.get("harness")
    body = {"project": cfg.get("project"), "workspace": cfg.get("workspace"), "teams": teams,
            "harness": {"mode": "path", "path": h} if h else {"mode": "off"},
            "extras": [e["id"] for e in cfg.get("extras") or [] if e.get("id")],
            "tools": cfg.get("tools") or {}, "worktrees": cfg.get("worktrees", True)}
    note = (f"This run continues the earlier run {run.name} of this project: your conversation from then is "
            "restored. The task board is new; re-read your inbox and continue where you left off.")
    return await begin(body, resume, note)


async def stop(request):
    if (bad := guard(request)):
        return bad
    return JSONResponse({"ok": True, "released": await asyncio.to_thread(stop_all)})


# ---- terminals ----------------------------------------------------------------
async def out(ws: WebSocket):
    """A terminal's output. A WebSocket, not a streamed HTTP response: the browser keeps at most 6 HTTP connections
    per server, so seven terminals left no connection for typing or polling (a second team was enough)."""
    origin = ws.headers.get("origin")
    if origin not in (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}") \
            or not secrets.compare_digest(ws.query_params.get("t", ""), TOKEN):
        return await ws.close(1008)
    pane = find_pane(ws)
    if not pane:
        return await ws.close(4404)
    await ws.accept()
    # first message: bytes of old output replayed first; the page ignores the terminal's own answers until then
    await ws.send_text(str(pane.size))

    async def send():
        async for chunk in pane.stream():
            await ws.send_bytes(chunk)
        await ws.close()  # the process exited

    sender = asyncio.create_task(send())
    try:
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass  # the page sends nothing; this only notices it leaving, which a quiet pane would never show
    except (RuntimeError, WebSocketDisconnect):
        pass
    finally:
        sender.cancel()


async def inp(request):
    if (bad := guard(request)):
        return bad
    pane = find_pane(request)
    if not pane or not pane.alive:
        return JSONResponse({"error": "no pane"}, status_code=404)
    pane.write((await request.body()).decode("utf-8"))
    return Response(status_code=204)


async def resize(request):
    if (bad := guard(request)):
        return bad
    pane = find_pane(request)
    if not pane or not pane.alive:
        return JSONResponse({"error": "no pane"}, status_code=404)
    body = await request.json()
    pane.resize(int(body["cols"]), int(body["rows"]))
    return Response(status_code=204)


# ---- agent_hub pane calls (agent_hub/panes.py): open / send / close / list -----
async def ext(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "bad json"}, status_code=400)
    if not secrets.compare_digest(str(body.get("token", "")), EXT_TOKEN):
        return JSONResponse({"ok": False, "error": "bad token"}, status_code=403)
    team = STATE["teams"].get(request.path_params["team"])
    if not team:
        return JSONResponse({"ok": False, "error": "this team is not running"})
    op, role = body.get("op"), body.get("role")
    try:
        if op == "list":
            return JSONResponse({"ok": True, "panes": [{"role": r} for r, p in team.panes.items() if p.alive]})
        if op == "open":
            # the command line in the request is not used: the pane spec on disk says what to run
            pane = team.launch(role, asyncio.get_running_loop())
            return JSONResponse({"ok": True, "pid": pane.pid})
        pane = team.panes.get(role)
        if not pane or not pane.alive:
            return JSONResponse({"ok": False, "error": f"no live pane '{role}'"})
        if op == "send":
            pane.write(" ".join(str(body.get("text") or "").splitlines()))
            if body.get("enter"):
                # codex takes Enter right after fast input as a pasted newline, so it comes a moment later
                asyncio.get_running_loop().call_later(0.3, pane.write, "\r")
            return JSONResponse({"ok": True})
        if op == "close":
            await asyncio.to_thread(pane.close)
            return JSONResponse({"ok": True})
    except (ValueError, OSError) as e:
        return JSONResponse({"ok": False, "error": str(e)})
    return JSONResponse({"ok": False, "error": f"unknown op '{op}'"})


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:  # never leave a CLI running after the server
        stop_all()


app = Starlette(lifespan=lifespan, routes=[
    Route("/", lambda r: RedirectResponse("/web/index.html")),
    Route("/web/index.html", index),
    Route("/api/catalog", catalog),
    Route("/api/state", state),
    Route("/api/scan", scan, methods=["POST"]),
    Route("/api/folders", folder_list),
    Route("/api/folders/pick", folder_pick, methods=["POST"]),
    Route("/api/start", start, methods=["POST"]),
    Route("/api/stop", stop, methods=["POST"]),
    Route("/api/teams", new_team, methods=["POST"]),
    Route("/api/teams/{team}/stop", team_stop, methods=["POST"]),
    Route("/api/runs", runs),
    Route("/api/runs/{run}", run_detail),
    Route("/api/runs/{run}/resume", run_resume, methods=["POST"]),
    Route("/history/{cap}/{run}/{path:path}", history_file),
    Route("/api/say/{team}/{role}", say, methods=["POST"]),
    Route("/api/agents/{team}/{role}/stop", agent_stop, methods=["POST"]),
    Route("/api/agents/{team}/{role}/restart", agent_restart, methods=["POST"]),
    Route("/api/changes", changes),
    Route("/api/changes/{team}/{role}/diff", change_diff),
    Route("/api/changes/{team}/{role}/merge", change_merge, methods=["POST"]),
    Route("/api/changes/{team}/{role}/discard", change_discard, methods=["POST"]),
    WebSocketRoute("/api/out/{team}/{role}", out),
    Route("/api/in/{team}/{role}", inp, methods=["POST"]),
    Route("/api/resize/{team}/{role}", resize, methods=["POST"]),
    Route("/ext/{team}", ext, methods=["POST"]),
    Route("/board/{cap}/{team}/{path:path}", board_file),
    Mount("/web", StaticFiles(directory=WEB)),
])


def already_running(url: str) -> bool:
    """Is a hub-desk already serving this port? (A second double-click then only opens the page.)"""
    import urllib.request
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + "/web/index.html", timeout=2) as r:
            return b'name="hub-token"' in r.read(4096)
    except OSError:
        return False


def main() -> None:
    import uvicorn
    url = f"http://{HOST}:{PORT}"
    if already_running(url):
        print(f"hub-desk is already running: {url}", flush=True)
        if "--no-browser" not in sys.argv:
            import webbrowser
            webbrowser.open(url)
        return
    if sys.platform == "win32":
        os.system("title hub-desk  -  close this window to stop hub-desk and every agent")
    print(f"hub-desk: {url}  (pid {os.getpid()})", flush=True)
    print("Close this window to stop hub-desk. Agents started from it stop too.", flush=True)
    if "--no-browser" not in sys.argv:
        import webbrowser
        webbrowser.open(url)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
