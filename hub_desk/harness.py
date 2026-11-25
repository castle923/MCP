"""The harness file and the local skills / plugins a run applies to its agents.

Harness: the AGENTS.md the agents follow (the workspace's own, else the global Codex one).
Base: what each CLI loads by itself (its skill folders, the enabled Claude plugins). Nothing to do for those.
Extras: skills only the other CLI has, and installed Claude plugins that are switched off. A run names the
ones it wants; an agent then gets the plugin loaded (`claude --plugin-dir`) or is told where the SKILL.md is.
Nothing here writes outside this project's state folder: the CLIs' own settings are only read.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HOME = Path.home()
MAX_EXTRAS = 12
CLIS = ("claude", "codex")
# (source key, folder, label, the CLIs that load it by themselves)
# ~/.agents/skills counts as Codex's own: Codex documents it as a user skill folder. Not checked on this machine.
USER_ROOTS = (
    ("claude", HOME / ".claude" / "skills", "Claude 스킬", ("claude",)),
    ("codex", HOME / ".codex" / "skills", "Codex 스킬", ("codex",)),
    ("agents", HOME / ".agents" / "skills", "공용 스킬 (.agents)", ("codex",)),
)


def _roots(workspace: Path):
    return USER_ROOTS + (
        ("project-claude", workspace / ".claude" / "skills", "프로젝트 스킬 (.claude)", ("claude",)),
        ("project-agents", workspace / ".agents" / "skills", "프로젝트 스킬 (.agents)", ("codex",)),
    )


def read_skill(folder: Path) -> dict | None:
    """name / description from the SKILL.md front matter; None when the folder is not a skill."""
    try:
        head = (folder / "SKILL.md").read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return None
    meta = {}
    m = re.match(r"\s*---\r?\n(.*?)\r?\n---", head, re.S)
    key = None
    for line in (m.group(1) if m else "").splitlines():
        if key and line[:1] in (" ", "\t"):  # `description: >` continues on indented lines
            meta[key] += " " + line.strip()
            continue
        k, sep, v = line.partition(":")
        key = k.strip() if sep and k.strip() in ("name", "description") else None
        if key:
            v = v.strip().strip("\"'")
            meta[key] = "" if v in (">", "|", ">-", "|-") else v
    return {"name": folder.name, "description": " ".join(meta.get("description", "").split())[:300]}


def harness_auto(workspace: Path) -> dict | None:
    try:
        for p in sorted(workspace.iterdir()):
            if p.is_file() and p.name.lower() in ("agents.md", "agent.md"):
                return {"path": str(p), "scope": "workspace"}
    except OSError:
        pass
    g = HOME / ".codex" / "AGENTS.md"
    return {"path": str(g), "scope": "global"} if g.is_file() else None


def harness_how(path: str, cli: str, workspace: Path) -> str:
    """'native': this CLI reads the file by itself. 'mention': the agent is told to read it at start."""
    p = Path(path).resolve()
    glob = (HOME / ".codex" / "AGENTS.md").resolve()
    if cli == "codex":
        return "native" if p == glob or (p.parent == workspace.resolve() and p.name.lower() == "agents.md") else "mention"
    own = [(HOME / ".claude" / "CLAUDE.md").resolve(), (workspace / "CLAUDE.md").resolve()]
    # this user's ~/.claude/CLAUDE.md is their Claude copy of the global AGENTS.md: reading both says it twice
    if p in own or (p == glob and own[0].is_file()):
        return "native"
    return "mention"


def _plugins() -> list[dict]:
    try:
        installed = json.loads((HOME / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    try:
        enabled = json.loads((HOME / ".claude" / "settings.json").read_text(encoding="utf-8")).get("enabledPlugins") or {}
    except (OSError, ValueError):
        enabled = {}
    out = []
    for pid, entries in (installed.get("plugins") or {}).items():
        e = entries[0] if isinstance(entries, list) and entries else entries
        if not isinstance(e, dict) or not e.get("installPath") or not Path(e["installPath"]).is_dir():
            continue
        desc = ""
        try:
            desc = json.loads((Path(e["installPath"]) / ".claude-plugin" / "plugin.json")
                              .read_text(encoding="utf-8")).get("description") or ""
        except (OSError, ValueError, AttributeError):
            pass
        out.append({"id": pid, "name": pid.split("@")[0], "path": e["installPath"], "on": enabled.get(pid) is True,
                    "description": (" ".join(str(desc).split()) or f"v{e.get('version', '?')}")[:300]})
    return out


def scan(workspace: str, known_file: Path | None = None) -> dict:
    ws = Path(workspace)
    native = {c: {} for c in CLIS}   # cli -> {skill name: path}
    found: dict[str, dict] = {}      # skill name -> first place it was seen
    for src, root, label_, clis in _roots(ws):
        try:
            folders = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for folder in folders:
            s = read_skill(folder)
            if not s:
                continue
            found.setdefault(s["name"], {**s, "src": src, "source": label_, "path": str(folder / "SKILL.md")})
            for c in clis:
                native[c].setdefault(s["name"], str(folder))
    plugins = _plugins()
    for p in plugins:  # skills of an enabled plugin are Claude's own too
        if p["on"]:
            try:
                for folder in (Path(p["path"]) / "skills").iterdir():
                    if (folder / "SKILL.md").is_file():
                        native["claude"].setdefault(folder.name, str(folder))
            except OSError:
                pass
    extras = []
    for name, s in found.items():
        how = {c: "native" if name in native[c] else "mention" for c in CLIS}
        if "mention" in how.values():
            extras.append({"id": f"skill:{s['src']}:{name}", "kind": "skill", "name": name, "path": s["path"],
                           "description": s["description"], "source": s["source"], "how": how})
    for p in plugins:
        if not p["on"]:
            extras.append({"id": f"plugin:{p['id']}", "kind": "plugin", "name": p["name"], "path": p["path"],
                           "description": p["description"], "source": "Claude 플러그인 (꺼져 있음)",
                           "how": {"claude": "plugin-dir", "codex": "mention"}})
    known = None
    if known_file:
        try:
            known = set(json.loads(known_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            remember(known_file, extras)  # first scan: everything here was already installed
    for e in extras:
        e["new"] = known is not None and e["id"] not in known
    extras.sort(key=lambda e: (not e["new"], e["kind"] != "plugin", e["name"].lower()))
    auto = harness_auto(ws)
    return {
        "harness": {"auto": auto,
                    "native": {c: harness_how(auto["path"], c, ws) for c in CLIS} if auto else None},
        "base": {"claude": {"skills": sorted(native["claude"]), "plugins": sorted(p["name"] for p in plugins if p["on"])},
                 "codex": {"skills": sorted(native["codex"]), "plugins": []}},
        "extras": extras, "limits": {"max_extras": MAX_EXTRAS}}


def remember(known_file: Path, extras: list[dict]) -> None:
    """Everything seen so far stops being 'new'."""
    try:
        old = set(json.loads(known_file.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        old = set()
    known_file.parent.mkdir(parents=True, exist_ok=True)
    known_file.write_text(json.dumps(sorted(old | {e["id"] for e in extras})), encoding="utf-8")


def resolve(body: dict, workspace: str, known_file: Path | None = None) -> dict:
    """The harness file and extras a start request fixes. Raises ValueError for anything not found here."""
    found = scan(workspace, known_file)
    h = body.get("harness") or {"mode": "auto"}
    mode = h.get("mode", "auto")
    if mode == "off":
        path = None
    elif mode == "path":
        path = str(h.get("path") or "").strip()
        if not path or not Path(path).is_file():
            raise ValueError(f"하네스 파일이 없습니다: {path or '(비어 있음)'}")
        path = str(Path(path).resolve())
    elif mode == "auto":
        path = found["harness"]["auto"]["path"] if found["harness"]["auto"] else None
    else:
        raise ValueError(f"알 수 없는 하네스 설정: {mode}")
    ids = body.get("extras") or []
    if not isinstance(ids, list) or len(ids) > MAX_EXTRAS:
        raise ValueError(f"추가 스킬·플러그인은 최대 {MAX_EXTRAS}개입니다")
    by_id = {e["id"]: e for e in found["extras"]}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise ValueError(f"설치되어 있지 않은 항목: {', '.join(map(str, missing))}")
    return {"harness": path, "extras": [by_id[i] for i in dict.fromkeys(ids)], "all": found["extras"]}


def addon(cfg: dict, cli: str, workspace: str) -> tuple[str, list[str]]:
    """What one agent gets on top of its job text: (instructions, plugin folders for `claude --plugin-dir`)."""
    lines, dirs = [], []
    if cfg.get("harness"):
        own = harness_how(cfg["harness"], cli, Path(workspace)) == "native"
        lines.append(f"Project harness: {cfg['harness']}. "
                     + ("Your CLI loads this file by itself. " if own else "Read it now, before your first task. ")
                     + "Follow it for all work in this project. The agent-hub team rules decide how work is delegated.")
    told = []
    for e in cfg.get("extras") or []:
        how = e["how"].get(cli)
        if how == "plugin-dir":
            dirs.append(e["path"])
        elif how == "mention":
            what = "plugin folder, see its skills/ and README" if e["kind"] == "plugin" else "skill"
            told.append(f"- {e['name']} ({what}): {e['description'][:140]} -> {e['path']}")
    if told:
        lines.append("Extra skills enabled for this project (your CLI does not load them by itself). "
                     "When a task matches one, read the file first and follow it:\n" + "\n".join(told))
    if dirs:
        lines.append("Plugins loaded for this project: " + ", ".join(Path(d).parent.name for d in dirs) + ".")
    return "\n".join(lines), dirs
