"""Existing project folders to work in: VS Code's open and recent folders, earlier hub-desk runs, a folder dialog.

Only read: VS Code's own files are opened read-only and nothing is written outside this project.
WSL and remote folders are listed but marked unsupported: the agents run as Windows processes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

CODE = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Code" / "User"
MAX = 40
_dialog = threading.Lock()  # one folder dialog at a time


def uri_path(uri: str) -> tuple[str | None, str]:
    """A VS Code folder URI as a local path, or (None, why) for WSL / remote folders."""
    u = urlparse(uri)
    if u.scheme != "file":
        return None, "원격 폴더 (WSL·SSH·컨테이너)"
    if u.netloc:  # file://wsl.localhost/..., file://server/share
        return None, "WSL·네트워크 폴더"
    p = unquote(u.path)
    if len(p) > 2 and p[0] == "/" and p[2] == ":":
        p = p[1].upper() + p[2:]  # VS Code writes the drive in lower case
    return str(Path(p)), ""


def _code_open() -> list[str]:
    try:
        w = json.loads((CODE / "globalStorage" / "storage.json").read_text(encoding="utf-8")).get("windowsState") or {}
    except (OSError, ValueError):
        return []
    wins = [w.get("lastActiveWindow") or {}] + list(w.get("openedWindows") or [])
    return [x["folder"] for x in wins if x.get("folder")]


def _code_recent() -> list[tuple[float, str]]:
    out = []
    try:
        dirs = list((CODE / "workspaceStorage").iterdir())
    except OSError:
        return out
    for d in dirs:
        try:
            uri = json.loads((d / "workspace.json").read_text(encoding="utf-8")).get("folder")
            st = d / "state.vscdb"
            out.append(((st if st.exists() else d / "workspace.json").stat().st_mtime, uri))
        except (OSError, ValueError):
            continue
    return [(t, u) for t, u in out if u]


def _hub_runs(state: Path) -> list[tuple[float, str, str]]:
    out = []
    for f in list(state.glob("*/config.json")) + [state / "last.json"]:
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
            out.append((f.stat().st_mtime, c["workspace"], c.get("project") or ""))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def describe(path: str) -> dict:
    """What a person needs to recognise a project folder."""
    p = Path(path)
    info = {"git": None, "harness": None, "files": None}
    head = p / ".git" / "HEAD"
    try:
        ref = head.read_text(encoding="utf-8").strip()
        info["git"] = ref.rsplit("/", 1)[-1] if ref.startswith("ref:") else ref[:8]
    except OSError:
        if (p / ".git").exists():
            info["git"] = "?"  # a worktree or submodule: .git is a file
    for name in ("AGENTS.md", "agent.md", "CLAUDE.md"):
        if (p / name).is_file():
            info["harness"] = name
            break
    try:
        info["files"] = sum(1 for _ in zip(p.iterdir(), range(500)))
    except OSError:
        pass
    return info


def candidates(state: Path) -> list[dict]:
    """Folders worth offering, newest first: VS Code's open windows, its recent folders, earlier runs."""
    temp = os.path.normcase(tempfile.gettempdir())
    seen: dict[str, dict] = {}

    def add(path: str | None, why: str, source: str, ts: float, project: str = "", uri: str = "") -> None:
        key = os.path.normcase(path or uri)
        row = seen.get(key)
        if row:
            row["sources"] = sorted(set(row["sources"]) | {source})
            row["ts"] = max(row["ts"], ts)
            row["project"] = row["project"] or project
            return
        if path and (os.path.normcase(path).startswith(temp) or not Path(path).is_dir()):
            return  # gone, or a scratch folder
        seen[key] = {"path": path or uri, "name": Path(path).name if path else uri.rsplit("/", 1)[-1],
                     "sources": [source], "ts": ts, "project": project, "ok": bool(path), "why": why}

    import time
    now = time.time()
    for uri in _code_open():
        add(*uri_path(uri), "vscode-open", now, uri=uri)
    for ts, uri in _code_recent():
        if not uri.endswith(".code-workspace"):
            add(*uri_path(uri), "vscode", ts, uri=uri)
    for ts, ws, project in _hub_runs(state):
        add(ws, "", "hub-desk", ts, project)
    rows = sorted(seen.values(), key=lambda r: (not r["ok"], "vscode-open" not in r["sources"], -r["ts"]))[:MAX]
    for r in rows:
        if r["ok"]:
            r.update(describe(r["path"]))
    return rows


PICK = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
p = filedialog.askdirectory(parent=root, initialdir=sys.argv[1] or None, mustexist=True,
                            title="hub-desk: 에이전트가 일할 폴더를 고르세요")
sys.stdout.buffer.write((p or "").encode("utf-8"))
"""


def pick(initial: str = "", timeout: int = 600) -> str | None:
    """Windows' folder dialog, in front of other windows. The chosen path, or None when cancelled.
    Raises RuntimeError when a dialog is already open."""
    if not _dialog.acquire(blocking=False):
        raise RuntimeError("폴더 선택 창이 이미 열려 있습니다. 바탕 화면이나 작업 표시줄에서 확인하세요.")
    try:
        start = initial if initial and Path(initial).is_dir() else ""
        # its own process: Tk wants the main thread, and a stuck dialog must not hold the server
        r = subprocess.run([sys.executable, "-I", "-c", PICK, start], capture_output=True, timeout=timeout)
        out = r.stdout.decode("utf-8", "replace").strip()
        return str(Path(out)) if out else None
    except subprocess.TimeoutExpired:
        return None
    finally:
        _dialog.release()
