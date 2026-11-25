"""Context-window usage per agent, read from each CLI's own session log.

claude: hook payloads carry transcript_path; the last main-thread assistant entry's usage
        (input + cache creation + cache read + output) is the current context size.
codex : ~/.codex/sessions/YYYY/MM/DD/rollout-*-<thread-id>.jsonl has token_count events with
        last_token_usage.total_tokens and model_context_window.
Only the file tail is read, so this stays cheap enough to run from hooks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

TAIL_BYTES = 512 * 1024


def tail_lines(path: str | Path, nbytes: int = TAIL_BYTES) -> list[str]:
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        lines = f.read().decode("utf-8", "replace").splitlines()
    return lines[1:] if size > nbytes else lines  # first line may be cut


def claude_used(transcript: str | Path) -> int | None:
    for line in reversed(tail_lines(transcript)):
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        u = (d.get("message") or {}).get("usage")
        if d.get("type") == "assistant" and u and not d.get("isSidechain"):
            return sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                               "cache_read_input_tokens", "output_tokens"))
    return None


def codex_rollout(thread_id: str, sessions: Path | None = None) -> Path | None:
    root = sessions or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    pat = f"rollout-*{thread_id}.jsonl"
    for day in sorted(root.glob("*/*/*"), reverse=True)[:3]:  # recent days first, then everything
        hit = next(day.glob(pat), None)
        if hit:
            return hit
    return next(root.rglob(pat), None)


def codex_used(rollout: str | Path) -> tuple[int, int | None] | None:
    for line in reversed(tail_lines(rollout)):
        if '"token_count"' not in line:
            continue
        try:
            info = json.loads(line)["payload"].get("info")
        except (ValueError, KeyError, AttributeError):
            continue
        if info and info.get("last_token_usage"):
            return info["last_token_usage"]["total_tokens"], info.get("model_context_window")
    return None


def ctx_text(used: int | None, window: int | None) -> str:
    if not used:
        return "-"
    if not window:
        return f"{used / 1000:.1f}k"
    return f"{100 * used // window}% ({used / 1000:.1f}k/{window // 1000}k)"
