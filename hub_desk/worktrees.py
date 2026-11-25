"""A git worktree per builder, so builders working at once never write over each other's files.

Each builder gets its own copy of the project (a worktree on its own branch `hub/<team>-<role>`) under
state/worktrees/<repository key>/, never inside the person's repository. The path stays the same from run to run, so
a CLI asks "trust this folder?" only the first time, and a copy kept with unmerged work is picked up again. Its changes reach the person's project only when the person merges
them from the dashboard: the change is committed on the builder's branch and applied to the project's working tree
as staged changes. Nothing is committed to, or pushed from, the person's branch.
At the end of a run, a copy with no unmerged change is removed with its branch; one with work in it is kept.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

DIFF_CAP = 400_000  # characters of diff text sent to the page


def git(cwd, *args, check=True, timeout=60) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(cwd), capture_output=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[:400] or f"git {args[0]} failed")
    return r.stdout


def repo_info(workspace: str) -> dict | None:
    """The repository the workspace is in, or None when it is not a git work tree."""
    try:
        top = git(workspace, "rev-parse", "--show-toplevel", timeout=10).strip()
        head = git(workspace, "rev-parse", "HEAD", timeout=10).strip()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None  # not a repository, or one without a first commit
    branch = git(workspace, "branch", "--show-current", check=False, timeout=10).strip() or "(detached)"
    dirty = [l for l in git(workspace, "status", "--porcelain", check=False, timeout=20).splitlines() if l.strip()]
    rel = Path(workspace).resolve().relative_to(Path(top).resolve()).as_posix()
    return {"root": str(Path(top)), "head": head, "branch": branch, "dirty": len(dirty), "sub": "" if rel == "." else rel}


def repo_key(root: str) -> str:
    import hashlib
    import os
    p = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in os.path.basename(p))[:30] or "repo"
    return f"{name}-{hashlib.sha1(p.encode('utf-8')).hexdigest()[:8]}"


class Worktrees:
    """The builder copies of one repository. registry.json next to them keeps each copy's branch and merge base
    across runs; `items` holds the copies this run uses."""

    def __init__(self, base: Path, workspace: str, info: dict):
        self.base, self.workspace, self.info = base, workspace, info
        self.file = base / "registry.json"
        base.mkdir(parents=True, exist_ok=True)
        try:
            self.registry = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.registry = {}
        self.items: dict[str, dict] = {}

    def save(self) -> None:
        self.registry.update(self.items)
        self.file.write_text(json.dumps(self.registry, ensure_ascii=False, indent=1), encoding="utf-8")

    def _is_copy(self, path: Path) -> bool:
        try:
            return Path(git(path, "rev-parse", "--show-toplevel", timeout=10).strip()).resolve() == path.resolve()
        except (RuntimeError, OSError, subprocess.SubprocessError):
            return False

    def ensure(self, team: str, role: str) -> str:
        """The folder the agent works in (created on first use). Returns the path inside the copy that matches
        the workspace (the workspace may be a sub folder of the repository)."""
        key = f"{team}/{role}"
        item = self.items.get(key)
        if not item:
            path = self.base / f"{team}-{role}"
            branch = f"hub/{team}-{role}"
            old = self.registry.get(key)
            if old and self._is_copy(path):  # last run's copy: keep its unmerged work, or start it fresh
                item = {**old, "merged": [], "resumed": True}
                self.items[key] = item
                if not self.changes(key)["files"]:
                    git(path, "checkout", "-q", "-B", branch, self.info["head"])
                    item.update(base=self.info["head"], resumed=False)
            else:
                git(self.info["root"], "worktree", "prune", check=False)
                exists = git(self.info["root"], "branch", "--list", branch).strip()
                git(self.info["root"], "worktree", "add", *([str(path), branch] if exists else
                                                            ["-b", branch, str(path), self.info["head"]]))
                item = self.items[key] = {"team": team, "role": role, "path": str(path), "branch": branch,
                                          "base": (old or {}).get("base", self.info["head"]) if exists else self.info["head"],
                                          "merged": [], "resumed": bool(exists)}
            self.save()
        return str(Path(item["path"]) / self.info["sub"]) if self.info["sub"] else item["path"]

    def changes(self, key: str) -> dict:
        """Changed files since the last merge: committed on the branch, staged, unstaged and new files."""
        item = self.items[key]
        wt = item["path"]
        git(wt, "add", "-A", "--intent-to-add", check=False)  # new files show in the diff without being staged
        files = []
        for line in git(wt, "diff", "--numstat", item["base"]).splitlines():
            add, dele, path = (line.split("\t", 2) + ["", ""])[:3]
            files.append({"path": path, "add": None if add == "-" else int(add), "del": None if dele == "-" else int(dele)})
        return {**{k: item[k] for k in ("team", "role", "path", "branch", "merged")},
                "resumed": bool(item.get("resumed")), "files": files}

    def diff(self, key: str) -> str:
        item = self.items[key]
        git(item["path"], "add", "-A", "--intent-to-add", check=False)
        text = git(item["path"], "diff", item["base"])
        return text if len(text) <= DIFF_CAP else text[:DIFF_CAP] + f"\n… ({len(text) - DIFF_CAP:,}자 생략)"

    def merge(self, key: str) -> dict:
        """Commit the copy's changes on its branch and apply them to the workspace as staged changes."""
        item = self.items[key]
        wt, ws = item["path"], self.info["root"]
        git(wt, "add", "-A")
        if git(wt, "status", "--porcelain").strip():
            git(wt, "-c", "user.name=hub-desk", "-c", "user.email=hub-desk@localhost", "commit", "-q",
                "-m", f"hub-desk: {item['team']} {item['role']}")
        head = git(wt, "rev-parse", "HEAD").strip()
        # bytes, not text: a text pipe on Windows turns the patch's line ends into CRLF and it no longer applies
        patch = subprocess.run(["git", "diff", "--binary", item["base"], head], cwd=wt, capture_output=True,
                               timeout=60).stdout
        if not patch.strip():
            return {"applied": 0, "note": "합칠 변경이 없습니다"}
        files = [l.split("\t", 2)[2] for l in git(wt, "diff", "--numstat", item["base"], head).splitlines()]
        r = subprocess.run(["git", "apply", "--3way", "--whitespace=nowarn", "-"], cwd=ws, input=patch,
                           capture_output=True, timeout=60)
        if r.returncode:  # nothing is recorded as merged: the person can fix the conflict and try again
            msg = (r.stderr or r.stdout).decode("utf-8", "replace").strip()[:600]
            raise RuntimeError("프로젝트에 적용하지 못했습니다 (충돌 또는 이미 바뀐 파일): " + msg)
        item["merged"].append({"commit": head, "files": len(files)})
        item["base"] = head  # later diffs start from what was merged
        self.save()
        return {"applied": len(files), "files": files[:50]}

    def discard(self, key: str) -> None:
        """Remove the copy and its branch, with whatever is in it. The page asks the person first."""
        item = self.items.pop(key)
        self.registry.pop(key, None)
        git(self.info["root"], "worktree", "remove", "--force", item["path"], check=False)
        git(self.info["root"], "branch", "-D", item["branch"], check=False)
        self.save()

    def close(self, team: str | None = None) -> dict:
        """End of run (or of one team): drop copies that hold nothing unmerged, keep the others."""
        kept, removed = [], 0
        for key in [k for k in self.items if team is None or k.startswith(team + "/")]:
            try:
                empty = not self.changes(key)["files"]
            except (RuntimeError, OSError, subprocess.SubprocessError):
                empty = False  # cannot tell: keep it
            if empty:
                self.discard(key)
                removed += 1
            else:
                kept.append({k: self.items[key][k] for k in ("team", "role", "path", "branch")})
        git(self.info["root"], "worktree", "prune", check=False)
        return {"removed": removed, "kept": kept}
