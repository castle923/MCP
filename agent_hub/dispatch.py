"""Board pane: prints the event feed and delivers inbox nudges only to idle agents.

Exactly one dispatcher runs (the 'board' pane), so nudges are never doubled.
Mail is never forced into a running turn: busy agents get it via `steer` hooks or
on their next idle check (the Stop-hook forced-continue approach is deliberately not used).
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from . import panes
from .catalog import load_catalog
from .db import Board

IDLE_S, BOOT_S, HITL_S, COOLDOWN_S = 12, 20, 60, 30
REPEAT_N, STALL_S = 5, 600
REAP_S, PANE_GRACE_S, LEASE_S = 30, 60, 15
LEVELS = ("healthy", "steering", "constrained", "stopped")
NUDGE = ("agent-hub: new inbox message(s). Call read_messages, act on them, "
         "and report with complete_task or post_message.")


def should_nudge(a: dict, has_mail: bool, now: float) -> bool:
    # a fresh claude pane sits at its prompt after SessionStart with no Stop yet: treat it as idle once
    # SessionStart arrived (session_id set) and IDLE_S passed. Startup can exceed 20 s (seen: 22 s),
    # so the spawn-time boot grace alone is not enough. codex has no SessionStart; its idle comes from notify.
    ready = a["status"] == "idle" or (a["status"] == "booting" and a.get("cli") == "claude" and a.get("session_id"))
    return (has_mail and ready and not a["paused"] and a["ended_at"] is None
            and now - (a["last_event_at"] or 0) >= IDLE_S
            and now - (a["started_at"] or 0) >= BOOT_S
            and now - (a["hitl_at"] or 0) >= HITL_S
            and now - (a["nudged_at"] or 0) >= COOLDOWN_S)


def breaker_step(level: str, unhealthy: bool, hard_stop: bool = False) -> str:
    """One level per beat: up when unhealthy, down when healthy; 'stopped' only with hard_stop."""
    i = LEVELS.index(level)
    i = i + 1 if unhealthy else max(0, i - 1)
    top = 3 if hard_stop else 2
    return LEVELS[min(i, top)]


def unhealthy(board: Board, a: dict, now: float) -> str | None:
    """Reason string if the agent looks runaway or stalled, else None."""
    pre = board.q("SELECT data FROM events WHERE role=? AND kind='PreToolUse' ORDER BY seq DESC LIMIT ?",
                  a["role"], REPEAT_N)
    if len(pre) == REPEAT_N and len({p["data"] for p in pre}) == 1:
        return f"same tool call repeated {REPEAT_N}x"
    if a["status"] == "busy" and now - (a["last_event_at"] or now) >= STALL_S:
        return f"no activity for {STALL_S // 60} min while busy"
    return None


def deliver(board: Board, a: dict) -> str:
    """Nudge one idle agent. Codex: native `codex queue` when the thread is known."""
    if a["cli"] == "codex" and a["session_id"]:
        exe = load_catalog()["cli"]["codex"]["command"]
        r = subprocess.run([exe, "queue", "--thread", a["session_id"], "--message", NUDGE],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return "codex queue"
    panes.call("send", role=a["role"], text=NUDGE, enter=True)
    return "terminal"


def beat(board: Board, now: float, hard_stop: bool = False) -> None:
    board.set_meta("dispatcher_beat", now)
    for a in board.agents(active_only=True):
        mail = board.unannounced(a["role"])
        if should_nudge(a, bool(mail), now):
            try:
                via = deliver(board, a)
                board.mark_announced(mail)
                board.update_agent(a["role"], nudged_at=now, status="busy")
                board.event(a["role"], "nudge", {"via": via, "messages": len(mail)})
            except (panes.PanesError, OSError, subprocess.SubprocessError) as e:
                board.update_agent(a["role"], nudged_at=now)  # cooldown before retry
                board.event(a["role"], "nudge_failed", {"error": str(e)})
        reason = unhealthy(board, a, now)
        new = breaker_step(a["breaker"], bool(reason), hard_stop)
        if new != a["breaker"]:
            board.update_agent(a["role"], breaker=new)
            board.event(a["role"], "breaker", {"level": new, "reason": reason})
            if new == "steering" and reason:  # only when escalating, not on the way down
                board.push_steer(a["role"], f"Circuit breaker warning: {reason}. Stop, re-plan, or ask main.")
            if new == "stopped":
                try:
                    panes.call("send", role=a["role"], text="/exit", enter=True)
                except panes.PanesError:
                    pass


def fmt(e: dict) -> str:
    data = json.loads(e["data"] or "{}")
    extra = " ".join(f"{k}={v}" for k, v in data.items() if v not in (None, "") and k != "input")
    return f"{time.strftime('%H:%M:%S', time.localtime(e['ts']))} {e['role']:<14} {e['kind']:<16} {extra}"[:200]


def reap(board: Board, now: float) -> bool:
    """Mark agents whose pane is gone as ended. False when no agent pane is left (or VS Code is gone)."""
    try:
        alive = {p["role"] for p in panes.call("list").get("panes", [])}
    except panes.PanesError:
        return False
    for a in board.agents(active_only=True):
        if a["role"] not in alive and now - (a["started_at"] or now) > PANE_GRACE_S:
            board.update_agent(a["role"], status="ended", ended_at=now)
            board.event(a["role"], "pane_closed", {})
    return bool(alive)


def claim(board: Board, me: str, now: float) -> bool:
    """Single-dispatcher lease: every hub MCP server runs a dispatcher thread, only the holder works.
    A holder that stops beating for LEASE_S loses the lease to the next process."""
    board.con.execute("BEGIN IMMEDIATE")
    try:
        owner, last = board.meta("dispatcher_owner"), board.meta("dispatcher_beat")
        ok = owner == me or not last or now - last >= LEASE_S
        if ok:
            board.set_meta("dispatcher_owner", me)
            board.set_meta("dispatcher_beat", now)
        board.con.execute("COMMIT")
    except Exception:
        board.con.execute("ROLLBACK")
        raise
    return ok


def run_background(interval: float = 2.0, stop=None) -> None:
    """Dispatcher loop for an MCP server thread (no pane, no output: stdout is the MCP channel)."""
    me = f"{os.getpid()}@{time.time():.0f}"
    board = Board()
    last_reap = time.time()
    while not (stop and stop.is_set()):
        now = time.time()
        try:
            if claim(board, me, now):
                if now - last_reap >= REAP_S:
                    last_reap = now
                    reap(board, now)
                beat(board, now)
        except Exception as e:  # never take the MCP server down
            try:
                board.event("dispatcher", "error", {"error": str(e)[:200]})
            except Exception:
                pass
        time.sleep(interval)


def watch(interval: float = 2.0, hard_stop: bool = False) -> None:
    """Optional visible event feed. Delivers nudges only while it holds the dispatcher lease."""
    board = Board()
    me = f"watch-{os.getpid()}"
    seq = (board.q("SELECT MAX(seq) AS s FROM events")[0]["s"] or 0)
    print("agent-hub event feed (Ctrl+C to stop)", flush=True)
    try:
        while True:
            now = time.time()
            if claim(board, me, now):
                beat(board, now, hard_stop)
            for e in board.events_after(seq):
                seq = e["seq"]
                if e["kind"] not in ("PostToolUse",):
                    print(fmt(e), flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        board.close()
