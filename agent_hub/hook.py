"""`agent-hub hook` (Claude hooks) and `agent-hub notify` (Codex notify).

Both write straight into the SQLite board. A process without AGENT_HUB_ROLE is not
a hub agent: the hook prints nothing and exits 0 so normal sessions are untouched.
Any internal error also exits 0 with empty stdout (= allow); that fail-open is
surfaced by `hub_status` through each agent's last_event_at.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from . import context
from .db import Board

# Tools blocked while an agent is paused or its breaker is 'constrained'.
WRITE_TOOLS = ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "PowerShell")


def classify_notification(p: dict) -> str:
    text = f"{p.get('notification_type', '')} {p.get('message', '')}".lower()
    return "waiting_approval" if ("permission" in text or "approv" in text) else "idle"


def handle(board: Board, role: str, p: dict) -> dict:
    """Apply one Claude hook payload; return the JSON to print (may be {})."""
    ev = p.get("hook_event_name", "")
    now = time.time()
    agent = board.agent(role) or {}
    fields = {"last_event_at": now}
    if p.get("session_id"):
        fields["session_id"] = p["session_id"]
    if ev == "SessionStart":
        # a new/resumed session sits at its prompt: report it ready (it stayed 'booting' until its first turn,
        # which main read as "not connected"). After a compact the turn may still be running: leave status.
        if p.get("source", "startup") in ("startup", "resume", "clear"):
            fields["status"] = "idle"
    elif ev in ("UserPromptSubmit", "PreToolUse", "PostToolUse"):
        fields["status"] = "busy"
    elif ev == "Notification":
        fields["status"] = classify_notification(p)
        if fields["status"] == "waiting_approval":
            fields["hitl_at"] = now
    elif ev == "Stop":
        fields["status"] = "idle"
    if agent.get("ended_at"):  # a late hook after cancel must not revive the agent
        fields.pop("status", None)
    data = {"tool": p.get("tool_name")}
    if ev in ("Stop", "PostToolUse") and p.get("transcript_path"):
        try:
            used = context.claude_used(p["transcript_path"])
        except OSError:
            used = None
        if used:
            fields["ctx_used"] = used
            if ev == "Stop":
                data["ctx"] = context.ctx_text(used, agent.get("ctx_window"))
    board.update_agent(role, **fields)
    if ev == "PreToolUse":
        data["input"] = json.dumps(p.get("tool_input"), sort_keys=True, ensure_ascii=False)[:500]
    board.event(role, ev, data)

    if ev == "PreToolUse":
        blocked = agent.get("paused") or agent.get("breaker") in ("constrained", "stopped")
        if blocked and p.get("tool_name") in WRITE_TOOLS:
            why = "paused by main" if agent.get("paused") else f"circuit breaker: {agent.get('breaker')}"
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": f"agent-hub: {why}"}}
    if ev in ("UserPromptSubmit", "PostToolUse"):
        steer = board.take_steer(role)
        if steer:
            return {"hookSpecificOutput": {"hookEventName": ev, "additionalContext": f"[agent-hub steer]\n{steer}"}}
    return {}


def hook_main() -> int:
    role = os.environ.get("AGENT_HUB_ROLE")
    if not role:
        return 0
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        board = Board()
        out = handle(board, role, payload)
        board.close()
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception as e:  # never break the agent's session
        print(f"agent-hub hook error: {e}", file=sys.stderr)
    return 0


def notify_main(argv: list[str]) -> int:
    """Codex calls: <notify argv...> <json payload>. Record, then chain the user's notify."""
    role, chain, payload = None, None, argv[-1] if argv else "{}"
    if "--role" in argv:
        role = argv[argv.index("--role") + 1]
    if "--chain" in argv:
        chain = json.loads(argv[argv.index("--chain") + 1])
    try:
        p = json.loads(payload)
        if role:
            board = Board()
            fields = {"last_event_at": time.time()}
            if p.get("type") == "agent-turn-complete" and not (board.agent(role) or {}).get("ended_at"):
                fields["status"] = "idle"
            data = {}
            if p.get("thread-id"):
                fields["session_id"] = p["thread-id"]
                agent = board.agent(role) or {}
                src = agent.get("ctx_src") or context.codex_rollout(p["thread-id"])
                usage = context.codex_used(src) if src else None
                if usage:
                    fields.update(ctx_src=str(src), ctx_used=usage[0], ctx_window=usage[1] or agent.get("ctx_window"))
                    data["ctx"] = context.ctx_text(fields["ctx_used"], fields["ctx_window"])
            board.update_agent(role, **fields)
            board.event(role, "codex:" + str(p.get("type")), data)
            board.close()
    except Exception as e:
        print(f"agent-hub notify error: {e}", file=sys.stderr)
    if chain:
        subprocess.Popen(chain + [payload], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0
