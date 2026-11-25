"""agent-hub MCP server (stdio).

--profile main   : orchestration tools + board tools (the main agent)
--profile worker : board tools only, so workers cannot spawn agents (recursion depth 1)
Never print to stdout here: stdout is the MCP channel.
"""
from __future__ import annotations

import asyncio
import json
import time

from mcp.server.mcpserver import MCPServer

from . import panes
from .catalog import MAIN_GUIDE, PY, check_role, job_text, load_catalog, load_team, ps_command, validate
from .context import ctx_text
from .db import ACTS, PKG_ROOT, Board
from .db import home as hub_home

WAIT_MAX_S = 60
LIVE = ("configuring", "booting", "busy", "idle", "waiting_approval")


def open_pane(board: Board, role: str, cli: str, model: str, effort: str, profile: str, source: str,
              job: str | None = None, team: str | None = None) -> dict:
    """Open a VS Code terminal pane that runs `agent-hub pane <spec>`, which starts the agent CLI."""
    cat = load_catalog()
    check_role(role)
    effort = validate(cat, cli, model, effort)
    spec = {"role": role, "cli": cli, "model": model, "effort": effort, "profile": profile, "source": source,
            "job": job,
            "job_text": (MAIN_GUIDE + (f"\nYour team in this session: {team}." if team else "")) if profile == "main"
            else job_text(load_team(), job)}
    adir = hub_home() / "agents" / role
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "pane.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    # mail left for this role by an earlier team is stale: the new agent was nudged with 17 old messages
    board.mark_processed(role, [m["id"] for m in board.inbox(role)])
    board.add_agent(role, cli, model, effort, source, job, status="configuring")
    title = f"{role} · {job or profile}"
    env = {"AGENT_HUB_ROLE": role, "AGENT_HUB_HOME": str(hub_home()), "PYTHONPATH": str(PKG_ROOT)}
    try:
        r = panes.call("open", role=role, title=title,
                       command=ps_command([PY, "-m", "agent_hub", "pane", str(adir / "pane.json")]), env=env)
    except panes.PanesError:
        board.update_agent(role, status="failed", ended_at=time.time())
        raise
    board.update_agent(role, pid=r.get("pid"))
    return {"role": role, "title": title, "terminal_pid": r.get("pid")}


def build(profile: str, role: str) -> MCPServer:
    app = MCPServer("agent-hub", instructions=MAIN_GUIDE if profile == "main" else (
        "Coordinate CLI agents running in VS Code terminal panes. Messages and tasks live on a shared board. "
        "Results come back through complete_task; never expect screen output."))
    board = Board()

    # ---- board tools (all profiles) ----------------------------------------
    @app.tool()
    def post_message(to: str, act: str, subject: str, body: str, in_reply_to: str | None = None,
                     needs_human: bool = False) -> dict:
        """Send a message to a role ('main', a worker role, 'broadcast', or 'human').
        act: request|inform|propose|query|agree|refuse|done. act=request must contain
        OBJECTIVE, OUTPUT, TOOLS and BOUNDARIES sections. Reference paths/ids instead of pasting content."""
        return {"ids": board.post(role, to, act, subject, body, in_reply_to, needs_human)}

    @app.tool()
    def read_messages(mark_processed: bool = True, include_processed: bool = False) -> list[dict]:
        """Read this role's inbox (unprocessed by default) and mark the returned messages processed."""
        msgs = board.inbox(role, include_processed)
        if mark_processed:
            board.mark_processed(role, [m["id"] for m in msgs if not m["processed_at"]])
        return [{k: m[k] for k in ("id", "sender", "act", "subject", "body", "in_reply_to",
                                   "requires_reply", "needs_human", "created_at")} for m in msgs]

    @app.tool()
    def claim_task(task_id: str) -> dict:
        """Mark one of your assigned tasks as in progress and return its spec."""
        return board.set_task(task_id, role, "in_progress")

    @app.tool()
    def complete_task(task_id: str, result: str, failed: bool = False) -> dict:
        """Report a task result (short summary + file paths). This is how main receives your work."""
        t = board.set_task(task_id, role, "failed" if failed else "done", result)
        board.post(role, "main", "done", f"task {task_id} {t['status']}", result)
        return t

    @app.tool()
    def board_post(title: str, path: str, note: str = "") -> dict:
        """Show a result to the user on the hub-desk board (a window over the dashboard): a report, a result
        summary, a chart, an HTML page or dashboard. Save it as a file inside the project folder first, then
        pass its path (.html .md .txt .csv .json .png .jpg .svg). Call again after updating the file."""
        from pathlib import Path
        from .wsl import to_windows
        p = Path(to_windows(path))  # an agent inside WSL gives a Linux path
        p = (p if p.is_absolute() else Path.cwd() / p).resolve()
        if not p.is_file():
            raise ValueError(f"no such file: {p}. Save the file first, then post its path.")
        board.event(role, "board_post", {"title": title[:120], "path": str(p), "note": note[:300]})
        return {"posted": True, "path": str(p)}

    if profile != "main":
        return app

    # ---- orchestration tools (main only) -----------------------------------
    @app.tool()
    def hub_models() -> dict:
        """Catalog of selectable CLIs, model ids and effort levels (verified flag per model)."""
        cat = load_catalog()
        return {cli: {"models": s["models"], "efforts": s["efforts"]} for cli, s in cat["cli"].items()}

    @app.tool()
    def hub_assign(role: str, objective: str, output: str, tools: str, boundaries: str, cli: str | None = None,
                    job: str | None = None, model: str | None = None, effort: str | None = None) -> dict:
        """Assign a task to a worker. If `role` is already running (e.g. 'builder', 'reviewer' opened by
        `agent-hub mods`), the task goes to that pane. Otherwise a new pane is opened: cli/job/model/effort
        default to the team.toml sub whose role or job matches, then to the catalog; job is builder|reviewer|
        any key of [jobs]. ids outside models.toml are rejected."""
        spec = f"OBJECTIVE: {objective}\nOUTPUT: {output}\nTOOLS: {tools}\nBOUNDARIES: {boundaries}"
        if role == "main":
            raise ValueError("main cannot assign a task to itself")
        cur = board.agent(role)
        if cur and cur["ended_at"] is None and cur["status"] in LIVE:
            tid = board.create_task(role, spec)
            board.post("main", role, "request", f"task {tid}", f"TASK_ID: {tid}\n{spec}")
            return {"role": role, "reused_pane": True, "task_id": tid}
        team = load_team()
        live = [a for a in board.agents(active_only=True) if a["role"] not in ("main", "board") and a["status"] in LIVE]
        if len(live) >= team.get("max_workers", 6):
            raise ValueError(f"max_workers={team.get('max_workers', 6)} reached: {[a['role'] for a in live]}")
        subs = [{**team[j], "job": j} for j in ("builder", "reviewer") if j in team]
        default = next((p for p in subs if p["role"] == role), None) \
            or next((p for p in subs if p["job"] == (job or "builder")), {})
        cli = cli or default.get("cli") or "claude"
        if default.get("cli") != cli:  # template model ids belong to another cli
            default = {"job": default.get("job")}
        model = model or default.get("model") or load_catalog()["cli"][cli]["models"][0]["id"]
        info = open_pane(board, role, cli, model, effort or default.get("effort"), "worker", "mcp",
                         job or default.get("job"))
        tid = board.create_task(role, spec)
        board.post("main", role, "request", f"task {tid}", f"TASK_ID: {tid}\n{spec}")
        return {**info, "task_id": tid}

    @app.tool()
    def hub_send(to: str, body: str, subject: str = "", act: str = "inform", mode: str = "idle",
                     in_reply_to: str | None = None) -> dict:
        """Message a worker. mode=idle: delivered when the pane is idle (dispatcher nudge).
        mode=steer: also injected into a busy agent on its next hook as additional context."""
        if act not in ACTS or mode not in ("idle", "steer"):
            raise ValueError("bad act or mode")
        ids = board.post("main", to, act, subject or body[:60], body, in_reply_to)
        if mode == "steer":
            board.push_steer(to, f"Message from main ({ids[0]}): {body}")
        return {"ids": ids}

    @app.tool()
    def hub_status(target: str | None = None) -> dict:
        """Agents (status, model, breaker, unread mail, seconds since last hook) and dispatcher health."""
        now = time.time()
        rows = [board.agent(target)] if target else board.agents()
        out = []
        for a in filter(None, rows):
            out.append({**{k: a[k] for k in ("role", "job", "cli", "model", "effort", "status", "paused",
                                              "breaker", "session_id", "ended_at")},
                        "context": ctx_text(a["ctx_used"], a["ctx_window"]),
                        "unread": len(board.inbox(a["role"])),
                        "since_last_event_s": round(now - (a["last_event_at"] or now))})
        beat = board.meta("dispatcher_beat")
        return {"agents": out, "dispatcher_alive": bool(beat and now - beat < 15),
                "note": "a live agent with no hook events for a long time may have lost its hook (fail-open)"}

    @app.tool()
    async def hub_wait(task_id: str, timeout_s: int = 30) -> dict:
        """Wait up to 60 s for a task to finish. Returns status 'running' if not done yet - call again."""
        end = time.time() + min(max(timeout_s, 1), WAIT_MAX_S)
        while True:
            t = board.task(task_id)
            if not t:
                raise ValueError(f"unknown task {task_id}")
            a = board.agent(t["assignee"]) or {}
            if t["status"] in ("done", "failed"):
                return t
            if a.get("status") == "waiting_approval":
                return {**t, "status": "blocked_waiting_user"}
            if time.time() >= end:
                return {**t, "status": "running", "agent_status": a.get("status")}
            await asyncio.sleep(1)

    @app.tool()
    async def hub_wait_all(task_ids: list[str], timeout_s: int = 30) -> dict:
        """Wait up to 60 s for several parallel tasks. Returns as soon as all are done (or failed),
        or at the timeout with each task's current status - call again for the ones still running."""
        end = time.time() + min(max(timeout_s, 1), WAIT_MAX_S)
        while True:
            out, pending = [], 0
            for tid in task_ids:
                t = board.task(tid)
                if not t:
                    raise ValueError(f"unknown task {tid}")
                a = board.agent(t["assignee"]) or {}
                st = t["status"]
                if st not in ("done", "failed"):
                    st = "blocked_waiting_user" if a.get("status") == "waiting_approval" else "running"
                    pending += 1
                out.append({"task_id": tid, "assignee": t["assignee"], "status": st, "result": t["result"],
                            "agent_status": a.get("status")})
            if not pending or time.time() >= end:
                return {"all_done": not pending, "tasks": out}
            await asyncio.sleep(1)

    @app.tool()
    def hub_pause(target: str, paused: bool = True) -> dict:
        """Pause (deny write tools via PreToolUse hook) or resume a worker."""
        board.update_agent(target, paused=int(paused))
        board.event("main", "pause" if paused else "resume", {"target": target})
        return {"role": target, "paused": paused}

    @app.tool()
    def hub_cancel(target: str, dispose_terminal: bool = False) -> dict:
        """Ask a worker to exit by typing /exit. dispose_terminal=True closes the pane outright:
        only use it after the user confirmed in chat."""
        panes.call("close" if dispose_terminal else "send", role=target, text="/exit", enter=True)
        board.update_agent(target, status="ended", ended_at=time.time())
        board.event("main", "cancel", {"target": target, "dispose": dispose_terminal})
        return {"role": target, "ended": True}

    return app


def serve(profile: str, role: str) -> None:
    import threading
    from .dispatch import run_background
    # every hub MCP server (main and workers) carries a dispatcher thread; a SQLite lease keeps one active,
    # so mail delivery needs no extra pane or process and survives any single agent exiting
    threading.Thread(target=run_background, name="agent-hub-dispatcher", daemon=True).start()
    build(profile, role).run("stdio")
