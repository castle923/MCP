"""Self-checks for hub-desk. Run: .venv\\Scripts\\python.exe -m pytest tests -q"""
import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub_desk import server  # noqa: E402
from hub_desk.pane import Pane  # noqa: E402


async def _pane() -> None:
    pane = Pane(["cmd", "/c", "echo", "hub-desk-ok"], 80, 24, asyncio.get_running_loop())
    out = b""
    async with asyncio.timeout(15):
        async for chunk in pane.stream():  # ends when the process exits
            out += chunk
    assert b"hub-desk-ok" in out, out
    assert pane.done and not pane.alive

    # input reaches the process, and close() ends a process that would not end by itself
    pane = Pane(["cmd", "/q"], 80, 24, asyncio.get_running_loop())
    pane.write("echo typed-%OS%\r\n")
    out = b""
    async with asyncio.timeout(15):
        async for chunk in pane.stream():
            out += chunk
            if b"typed-Windows_NT" in out:
                break
    pane.close()
    async with asyncio.timeout(15):
        async for _ in pane.stream():
            pass
    assert not pane.alive


def test_pane():
    asyncio.run(_pane())


def test_old_output_is_dropped_but_stream_continues(monkeypatch):
    async def run():
        monkeypatch.setattr("hub_desk.pane.KEEP_BYTES", 10)
        pane = Pane(["cmd", "/c", "echo", "x"], 80, 24, asyncio.get_running_loop())
        async with asyncio.timeout(15):
            async for _ in pane.stream():
                pass
        for i in range(5):
            pane._feed(b"%d2345678" % i)
        assert pane.base > 0 and pane.size <= 18
        got = [c async for c in pane.stream()]
        assert got[-1] == b"42345678"
    asyncio.run(run())


A = {"cli": "claude", "model": "claude-haiku-5-5", "effort": "low"}


def test_plan_names_roles_and_fixes_models(tmp_path):
    ws, project, teams = server.plan({"project": "  데모  프로젝트 ", "workspace": str(tmp_path), "teams": [
        {"main": {"cli": "codex", "model": "gpt-6.1-sol", "effort": "medium"},
         "subs": [{"job": "builder", **A}, {"job": "builder", **A}, {"job": "reviewer", **A}]},
        {"main": {**A, "name": "지휘자"}, "subs": [{"job": "builder", **A, "name": "프론트-1"}]}]})
    assert ws == str(tmp_path.resolve()) and project == "데모 프로젝트"
    assert [(s["role"], s["name"]) for s in teams[1]] == [("main", "지휘자"), ("프론트-1", "프론트-1")]
    assert "\"프론트-1\" (job builder, claude)" in teams[1][0]["job_text"]  # the main is told its sub's real name
    assert [s["role"] for s in teams[0]] == ["main", "builder", "builder2", "reviewer"]
    assert teams[0][0]["profile"] == "main" and "\"builder2\" (job builder, claude)" in teams[0][0]["job_text"]
    assert teams[0][1]["profile"] == "worker" and teams[0][1]["effort"] == "low"
    assert "Agent/Task tool" in teams[1][0]["job_text"]  # a Claude main is told not to use its own subagents


@pytest.mark.parametrize("body", [
    {"workspace": "Z:/no/such/folder", "teams": [{"main": A}]},
    {"teams": []},
    {"teams": [{"main": A}] * 7},
    {"teams": [{"main": A, "name": "x" * 21}]},
    {"teams": [{"main": {**A, "model": "not-in-catalog"}}]},
    {"teams": [{"main": {**A, "effort": "ultra"}}]},
    {"teams": [{"main": A, "subs": [{"job": "boss", **A}]}]},
    {"teams": [{"main": A, "subs": [{"job": "builder", **A}] * 7}]},
    {"teams": [{"main": A}], "project": ""},
    {"teams": [{"main": A}], "project": "x" * 61},
    {"teams": [{"main": A, "subs": [{"job": "builder", **A, "name": "main"}]}]},
    {"teams": [{"main": A, "subs": [{"job": "builder", **A, "name": "a b"}]}]},
    {"teams": [{"main": A, "subs": [{"job": "builder", **A, "name": "same"}, {"job": "reviewer", **A, "name": "same"}]}]},
])
def test_plan_rejects(body, tmp_path):
    body.setdefault("workspace", str(tmp_path))
    body.setdefault("project", "p")
    with pytest.raises(ValueError):
        server.plan(body)


def test_agent_hub_reaches_panes_over_http(tmp_path, monkeypatch):
    """agent_hub/panes.py posts to the url in ext.json; hub-desk answers at /ext/<team>."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from agent_hub import panes
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            reply = {"ok": True, "panes": [{"role": "builder"}]} if body["op"] == "list" else {"ok": False, "error": "nope"}
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        (tmp_path / "ext.json").write_text(json.dumps(
            {"url": f"http://127.0.0.1:{srv.server_port}/ext/team1", "token": "t"}), encoding="utf-8")
        monkeypatch.setenv("AGENT_HUB_HOME", str(tmp_path))
        assert panes.call("list")["panes"] == [{"role": "builder"}]
        assert seen[0] == {"token": "t", "op": "list"}
        with pytest.raises(panes.PanesError, match="nope"):
            panes.call("send", role="builder", text="hi", enter=True)
    finally:
        srv.shutdown()


def test_close_ends_a_grandchild_whose_parent_already_exited():
    """What `taskkill /T` missed: a CLI's MCP server started by a process that is gone."""
    import re
    from hub_desk.job import pid_alive

    async def run():
        helper = str(Path(__file__).parent / "helpers" / "spawn_chain.py")
        pane = Pane([sys.executable, helper], 80, 24, asyncio.get_running_loop())
        assert pane.job.ok
        out = b""
        async with asyncio.timeout(30):
            async for chunk in pane.stream():
                out += chunk
                if (m := re.search(rb"grandchild=(\d+)=", out)):
                    break
        grandchild = int(m.group(1))
        assert pid_alive(grandchild) and pid_alive(pane.pid)
        assert pane.procs >= 2
        ended, left = pane.close()
        assert ended >= 2 and left == 0 and pane.procs == 0  # reported as given back
        assert pane.close() == (0, 0)  # a second close has nothing more to return
        async with asyncio.timeout(15):
            while pid_alive(grandchild) or pid_alive(pane.pid):
                await asyncio.sleep(0.1)
    asyncio.run(run())


def test_an_agent_that_exits_by_itself_gives_back_what_it_started():
    """No stop pressed: the CLI ends, the process it left running ends with it."""
    import re
    from hub_desk.job import pid_alive

    async def run():
        helper = str(Path(__file__).parent / "helpers" / "spawn_chain.py")
        pane = Pane([sys.executable, helper, "b"], 80, 24, asyncio.get_running_loop())  # prints a child's pid, exits
        out = b""
        async with asyncio.timeout(30):
            async for chunk in pane.stream():  # ends at the process's exit
                out += chunk
        child = int(re.search(rb"(\d+)", out).group(1))
        async with asyncio.timeout(15):
            while pid_alive(child):
                await asyncio.sleep(0.1)
        assert pane.released is not None and pane.released[1] == 0
    asyncio.run(run())


def test_stop_reports_what_was_released_and_keeps_the_last_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LAST", tmp_path / "last.json")
    assert server.read_last() is None
    (tmp_path / "last.json").write_text('{"project": "p", "teams": []}', encoding="utf-8")
    assert server.read_last()["project"] == "p"
    monkeypatch.setitem(server.STATE, "teams", {})
    assert server.stop_all() == {"agents": 0, "processes": 0, "left": 0}


def test_folder_candidates_from_vscode_and_earlier_runs(tmp_path, monkeypatch):
    import json as j
    from hub_desk import folders
    code, state = tmp_path / "Code" / "User", tmp_path / "state"
    proj, gone = tmp_path / "proj", tmp_path / "gone"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / ".git" / "HEAD").write_text("ref: refs/heads/dev\n", encoding="utf-8")
    (proj / "AGENTS.md").write_text("rules", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    uri = lambda p: "file:///" + str(p).replace("\\", "/").replace(":", "%3A", 1)  # noqa: E731 (VS Code's form)
    (code / "globalStorage").mkdir(parents=True)
    (code / "globalStorage" / "storage.json").write_text(j.dumps({"windowsState": {
        "lastActiveWindow": {"folder": uri(proj)},
        "openedWindows": [{"folder": "vscode-remote://wsl%2Bubuntu/home/me/x"}]}}), encoding="utf-8")
    for i, u in enumerate([uri(proj), uri(gone), "file://wsl.localhost/Ubuntu/home/me/y"]):
        (code / "workspaceStorage" / str(i)).mkdir(parents=True)
        (code / "workspaceStorage" / str(i) / "workspace.json").write_text(j.dumps({"folder": u}), encoding="utf-8")
    (state / "run1").mkdir(parents=True)
    (state / "run1" / "config.json").write_text(j.dumps({"workspace": str(other), "project": "예전 일"}), encoding="utf-8")
    monkeypatch.setattr(folders, "CODE", code)
    monkeypatch.setattr(folders.tempfile, "gettempdir", lambda: "Z:\\no-temp")  # tmp_path itself is under %TEMP%
    rows = folders.candidates(state)
    by = {r["name"]: r for r in rows}
    assert rows[0]["path"].lower() == str(proj).lower() and set(rows[0]["sources"]) == {"vscode-open", "vscode"}
    assert rows[0]["git"] == "dev" and rows[0]["harness"] == "AGENTS.md"
    assert by["other"]["project"] == "예전 일" and by["other"]["sources"] == ["hub-desk"]
    assert "gone" not in by  # deleted folders are not offered
    assert [r["ok"] for r in rows if r["name"] in ("x", "y")] == [False, False] and rows[-1]["why"]
    assert folders.uri_path("file:///c%3A/A%20B/%ED%95%9C")[0] == r"C:\A B\한"


def test_team_names_ride_on_the_main(tmp_path):
    _, _, teams = server.plan({"project": "p", "workspace": str(tmp_path), "teams": [
        {"name": "  프론트   팀 ", "main": A}, {"main": A}]})
    assert teams[0][0]["team_name"] == "프론트 팀" and teams[1][0]["team_name"] == ""
    assert server.team_specs({"main": A, "subs": [{"job": "builder", **A}]})[1]["role"] == "builder"


def test_resume_arguments(tmp_path, monkeypatch):
    from agent_hub import catalog
    monkeypatch.setenv("AGENT_HUB_HOME", str(tmp_path))
    cat = catalog.load_catalog()
    c = catalog.build_command(cat, "w1", "claude", "claude-haiku-5-5", "low", "worker", resume="abc-123")
    assert c[-2:] == ["--resume", "abc-123"] or ("--resume" in c and c[c.index("--resume") + 1] == "abc-123")
    x = catalog.build_command(cat, "w2", "codex", "gpt-6-luna", "low", "worker", job="j", resume="019a-uuid")
    assert x[1:3] == ["resume", "019a-uuid"] and "-m" in x
    fresh = catalog.build_command(cat, "w3", "codex", "gpt-6-luna", "low", "worker", job="j")
    assert fresh[1] == "-m" and not fresh[-1].startswith("-")  # a fresh worker gets its first message, a resumed one not
    assert x[-1] != fresh[-1]
