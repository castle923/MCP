"""Build dist/hub-desk-<date>.zip for another PC: the program only, without this PC's data.

Left out: .venv (bound to this PC), state/ (run records, session ids, git copies, this PC's folder paths), sandbox
folders, caches, dist/ itself. The zip is checked for anything that should not be in it before it is kept.
    .venv\\Scripts\\python.exe tools\\make_package.py
"""
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["hub_desk", "agent_hub", "tools", "tests", "models.toml", "team.toml", "requirements.txt",
           "install.cmd", "install.ps1", "hub-desk.cmd", "INSTALL.md"]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", "state", "dist", "sandbox", "sandbox-git", ".git", ".claude"}
SKIP_FILES = {".pyc", ".pyo"}


def files():
    for name in INCLUDE:
        p = ROOT / name
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and not (set(f.relative_to(ROOT).parts) & SKIP_DIRS) and f.suffix not in SKIP_FILES:
                    yield f
        else:
            sys.exit(f"missing: {name}")


def main() -> int:
    out = ROOT / "dist" / f"hub-desk-{time.strftime('%Y%m%d')}.zip"
    out.parent.mkdir(exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files():
            z.write(f, Path("hub-desk") / f.relative_to(ROOT))
            count += 1
    with zipfile.ZipFile(out) as z:  # nothing of this PC's data
        bad = [n for n in z.namelist() if any(part in SKIP_DIRS for part in Path(n).parts)]
        texts = [n for n in z.namelist() if n.endswith((".py", ".toml", ".cmd", ".ps1", ".md", ".js", ".html", ".css"))]
        home = Path.home().as_posix().encode()  # this PC's own folders (mock.js has made-up sample paths)
        needles = [home, home.replace(b"/", b"\\"), home.replace(b"/", b"\\\\")]
        leaks = [n for n in texts if "mock.js" not in n and any(x.lower() in z.read(n).lower() for x in needles)]
    if bad or leaks:
        out.unlink()
        sys.exit(f"refused: {bad or leaks}")
    print(f"{out}  ({count} files, {out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
