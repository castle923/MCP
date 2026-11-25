"""Client for the agent-hub-panes VS Code extension (named pipe, one JSON line per call)."""
from __future__ import annotations

import json

from .db import home


class PanesError(RuntimeError):
    pass


def call(op: str, **args) -> dict:
    info_path = home() / "ext.json"
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except OSError:
        raise PanesError(f"no VS Code window for this folder ({info_path} missing). Open the project folder "
                         "in VS Code (Windows-local, not WSL) and run agent-hub from that folder's root.") from None
    if info.get("url"):  # hub-desk hosts the panes: same four calls over HTTP on 127.0.0.1
        import urllib.request
        req = urllib.request.Request(
            info["url"], data=json.dumps({"token": info["token"], "op": op, **args}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:  # no proxy: this never leaves the machine
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as r:
                reply = json.loads(r.read().decode("utf-8") or "{}")
        except (OSError, ValueError) as e:
            raise PanesError(f"cannot reach hub-desk at {info['url']}: {e}") from None
        if not reply.get("ok"):
            raise PanesError(reply.get("error", "unknown hub-desk error"))
        return reply
    try:
        with open(info["pipe"], "r+b", buffering=0) as pipe:
            pipe.write((json.dumps({"token": info["token"], "op": op, **args}, ensure_ascii=False) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = pipe.read(65536)
                if not chunk:
                    break
                buf += chunk
    except OSError as e:
        raise PanesError(f"cannot reach VS Code extension pipe: {e}") from None
    reply = json.loads(buf.decode("utf-8") or "{}")
    if not reply.get("ok"):
        raise PanesError(reply.get("error", "unknown extension error"))
    return reply
