"""git worktree per builder: create, see changes, merge into the project, discard, end-of-run cleanup."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub_desk import worktrees as W  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    (r / "app").mkdir(parents=True)
    (r / "app" / "main.py").write_text("print('v1')\n", encoding="utf-8")
    W.git(r, "init", "-q", "-b", "main")
    W.git(r, "add", "-A")
    W.git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return r


def test_not_a_repo(tmp_path):
    assert W.repo_info(str(tmp_path)) is None


def test_builder_copy_merge_and_cleanup(repo, tmp_path):
    info = W.repo_info(str(repo / "app"))  # the workspace may be a sub folder of the repository
    assert info["branch"] == "main" and info["sub"] == "app" and info["dirty"] == 0
    run = tmp_path / "state" / "20261125T130000-p"
    run.mkdir(parents=True)
    wt = W.Worktrees(run / "copies", str(repo / "app"), info)
    a = Path(wt.ensure("team1", "builder"))
    b = Path(wt.ensure("team1", "builder2"))
    assert a.name == "app" and (a / "main.py").is_file() and a.parent != b.parent
    assert wt.ensure("team1", "builder") == str(a)  # same copy the second time

    (a / "main.py").write_text("print('v2')\n", encoding="utf-8")  # changed
    (a / "new.txt").write_text("hello\n", encoding="utf-8")       # new, untracked
    ch = wt.changes("team1/builder")
    assert {f["path"] for f in ch["files"]} == {"app/main.py", "app/new.txt"}
    assert "+print('v2')" in wt.diff("team1/builder") and "+hello" in wt.diff("team1/builder")
    assert (repo / "app" / "main.py").read_text(encoding="utf-8") == "print('v1')\n"  # the project is untouched

    r = wt.merge("team1/builder")
    assert r["applied"] == 2
    assert (repo / "app" / "main.py").read_text(encoding="utf-8") == "print('v2')\n"
    assert (repo / "app" / "new.txt").is_file()
    staged = W.git(repo, "diff", "--cached", "--name-only").split()
    assert set(staged) == {"app/main.py", "app/new.txt"}  # staged in the project, not committed
    assert W.git(repo, "rev-list", "--count", "HEAD").strip() == "1"
    assert wt.changes("team1/builder")["files"] == []  # nothing left to merge
    assert wt.merge("team1/builder")["applied"] == 0

    (b / "other.txt").write_text("keep me\n", encoding="utf-8")
    out = wt.close()  # builder: merged, nothing left -> removed; builder2: unmerged work -> kept
    assert out["removed"] == 1 and [k["role"] for k in out["kept"]] == ["builder2"]
    assert not Path(a).exists() and (b / "other.txt").is_file()
    assert "hub/" in W.git(repo, "branch", "--list", "hub/*")  # builder2's branch is still there

    wt.discard("team1/builder2")
    assert not b.exists() and not W.git(repo, "branch", "--list", "hub/*").strip()


def test_merge_conflict_is_reported_and_nothing_recorded(repo, tmp_path):
    info = W.repo_info(str(repo))
    run = tmp_path / "state" / "r"
    run.mkdir(parents=True)
    wt = W.Worktrees(run / "copies", str(repo), info)
    a = Path(wt.ensure("team1", "builder"))
    (a / "app" / "main.py").write_text("print('from builder')\n", encoding="utf-8")
    (repo / "app" / "main.py").write_text("print('person edited this')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="적용하지 못했습니다"):
        wt.merge("team1/builder")
    assert (repo / "app" / "main.py").read_text(encoding="utf-8") == "print('person edited this')\n"
    assert wt.items["team1/builder"]["merged"] == []
    wt.discard("team1/builder")


def test_same_folder_next_run_and_unmerged_work_carries_over(repo, tmp_path):
    base = tmp_path / "copies"
    wt = W.Worktrees(base, str(repo), W.repo_info(str(repo)))
    a = Path(wt.ensure("team1", "builder"))
    (a / "work.txt").write_text("half done\n", encoding="utf-8")
    assert [k["role"] for k in wt.close()["kept"]] == ["builder"]

    wt2 = W.Worktrees(base, str(repo), W.repo_info(str(repo)))  # next run, same repository
    assert Path(wt2.ensure("team1", "builder")) == a  # same path: a CLI trusts it once
    assert wt2.items["team1/builder"]["resumed"] and (a / "work.txt").is_file()
    assert [f["path"] for f in wt2.changes("team1/builder")["files"]] == ["work.txt"]
    wt2.discard("team1/builder")

    wt3 = W.Worktrees(base, str(repo), W.repo_info(str(repo)))
    b = Path(wt3.ensure("team1", "builder"))  # discarded: a fresh copy at the same path
    assert b == a and not (b / "work.txt").exists() and not wt3.items["team1/builder"]["resumed"]
    assert wt3.close()["removed"] == 1
