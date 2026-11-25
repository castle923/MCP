"""WSL support: an agent runs inside a WSL distro with that distro's own CLI, the hub stays on Windows.

The agent CLI (Linux claude / codex) is started with `wsl.exe -d <distro> --cd <linux path>`. The hub's MCP server,
hooks and notify run as the Windows Python through WSL interop, so the board and its SQLite stay on Windows. Paths
cross the boundary in both directions:  C:\\x  <->  /mnt/c/x   and   \\\\wsl.localhost\\<distro>\\y  <->  /y.
Environment variables reach the other side only when named in WSLENV.
"""
from __future__ import annotations

import os
import re
import subprocess

ENV = ("AGENT_HUB_HOME", "PYTHONPATH", "AGENT_HUB_ROLE", "AGENT_HUB_WSL")  # what crosses into WSL and back
UNC = re.compile(r"^[\\/]{2}(?:wsl\.localhost|wsl\$)[\\/]([^\\/]+)(.*)$", re.I)
_distros: list[str] | None = None


def parse_unc(path: str) -> tuple[str, str] | None:
    """\\\\wsl.localhost\\Ubuntu\\home\\me -> ("Ubuntu", "/home/me"); None for a Windows path."""
    m = UNC.match(str(path or ""))
    if not m:
        return None
    rest = m.group(2).replace("\\", "/").rstrip("/") or "/"
    return m.group(1), rest if rest.startswith("/") else "/" + rest


def unc(distro: str, linux_path: str) -> str:
    return "\\\\wsl.localhost\\" + distro + linux_path.replace("/", "\\")


def to_linux(path: str) -> str:
    """A Windows path as the WSL side sees it (C:\\x -> /mnt/c/x, a \\\\wsl.localhost path -> its Linux path)."""
    s = str(path)
    p = parse_unc(s)
    if p:
        return p[1]
    m = re.match(r"^([A-Za-z]):[\\/]?(.*)$", s)
    if m:
        return "/mnt/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")
    return s


def to_windows(path: str, distro: str | None = None) -> str:
    """A path written by an agent inside WSL, as Windows reads it."""
    s = str(path)
    m = re.match(r"^/mnt/([a-z])(?:/(.*))?$", s)
    if m:
        return m.group(1).upper() + ":\\" + (m.group(2) or "").replace("/", "\\")
    distro = distro or os.environ.get("AGENT_HUB_WSL")
    if s.startswith("/") and distro:
        return unc(distro, s)
    return s


def distros() -> list[str]:
    """Registered WSL distros (`wsl -l -q`, whose output is UTF-16)."""
    global _distros
    if _distros is None:
        try:
            out = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=15).stdout
            _distros = [d.strip() for d in out.decode("utf-16-le", "replace").splitlines() if d.strip()]
        except (OSError, subprocess.SubprocessError):
            _distros = []
    return _distros


def distro_name(name: str) -> str | None:
    """The registered spelling of a distro name (VS Code writes it in lower case)."""
    return next((d for d in distros() if d.lower() == name.lower()), None)


def launch(distro: str, cwd: str, argv: list[str]) -> list[str]:
    """argv that runs `argv` inside the distro, in `cwd`, through a login shell (so ~/.local/bin is on PATH)."""
    # --exec, not --: with -- wsl.exe runs the line through a shell first, which expanded "$@" to nothing
    return ["wsl.exe", "-d", distro, "--cd", cwd, "--exec", "bash", "-lc", 'exec "$@"', "hub-desk", *argv]


def wslenv() -> str:
    """WSLENV that carries the hub's variables across, keeping the person's own entries."""
    have = [v for v in os.environ.get("WSLENV", "").split(":") if v]
    names = {v.split("/")[0] for v in have}
    return ":".join(have + [v for v in ENV if v not in names])
