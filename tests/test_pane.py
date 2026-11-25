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


def test_terminal_output_over_websocket(monkeypatch):
    """Terminal output is a WebSocket: a browser holds only 6 HTTP connections per server, and streamed responses
    for a 7th terminal (a second team) left none for typing. Eight sockets at once must all get the output."""
    import socket
    import time
    from contextlib import ExitStack
    import uvicorn
    from websockets.sync.client import connect
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(server, "PORT", port)
    loop = asyncio.new_event_loop()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    threading.Thread(target=loop.run_until_complete, args=(srv.serve(),), daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    pane = Pane(["cmd", "/q"], 80, 24, loop)

    class Team:
        panes = {"r": pane}
    monkeypatch.setitem(server.STATE, "teams", {"t": Team()})
    url, origin = f"ws://127.0.0.1:{port}/api/out/t/r", f"http://127.0.0.1:{port}"
    try:
        pane.write("echo ws-%OS%\r\n")
        with ExitStack() as stack:
            socks = [stack.enter_context(connect(f"{url}?t={server.TOKEN}", origin=origin, proxy=None)) for _ in range(8)]
            for ws in socks:
                int(ws.recv(timeout=10))  # backlog size comes first
                out = b""
                while b"ws-Windows_NT" not in out:
                    out += ws.recv(timeout=10)
        for bad in (f"{url}?t=wrong", f"ws://127.0.0.1:{port}/api/out/t/nope?t={server.TOKEN}"):
            with pytest.raises(Exception):
                with connect(bad, origin=origin, proxy=None) as ws:
                    ws.recv(timeout=5)
        with pytest.raises(Exception):  # another site's page
            with connect(f"{url}?t={server.TOKEN}", origin="http://evil.test", proxy=None) as ws:
                ws.recv(timeout=5)
    finally:
        pane.close()
        srv.should_exit = True


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
    from agent_hub import wsl
    monkeypatch.setattr(wsl, "_distros", [])
    monkeypatch.setattr(folders.tempfile, "gettempdir", lambda: "Z:\\no-temp")  # tmp_path itself is under %TEMP%
    rows = folders.candidates(state)
    by = {r["name"]: r for r in rows}
    assert rows[0]["path"].lower() == str(proj).lower() and set(rows[0]["sources"]) == {"vscode-open", "vscode"}
    assert rows[0]["git"] == "dev" and rows[0]["harness"] == "AGENTS.md"
    assert by["other"]["project"] == "예전 일" and by["other"]["sources"] == ["hub-desk"]
    assert "gone" not in by  # deleted folders are not offered
    # no such distro registered here in the test: listed, not usable
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


def test_wsl_paths_and_launch():
    from agent_hub import wsl
    assert wsl.parse_unc(r"\\wsl.localhost\Ubuntu-24.04\home\user\proj") == ("Ubuntu-24.04", "/home/user/proj")
    assert wsl.parse_unc(r"\\wsl$\Ubuntu\home") == ("Ubuntu", "/home") and wsl.parse_unc(r"C:\x") is None
    assert wsl.to_linux(r"C:\Tools\hub-desk\.venv\Scripts\python.exe") == "/mnt/c/Tools/hub-desk/.venv/Scripts/python.exe"
    assert wsl.to_linux(r"\\wsl.localhost\Ubuntu\home\me\a.txt") == "/home/me/a.txt"
    assert wsl.to_windows("/mnt/d/data/x.csv") == "D:\\data\\x.csv"
    assert wsl.to_windows("/home/me/out.html", "Ubuntu") == r"\\wsl.localhost\Ubuntu\home\me\out.html"
    assert wsl.launch("Ubuntu", "/home/me", ["claude", "-m", "x"])[:6] == ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/me", "--exec"]


def test_build_command_for_wsl(tmp_path, monkeypatch):
    import json as j
    from agent_hub import catalog
    monkeypatch.setenv("AGENT_HUB_HOME", str(tmp_path))
    cat = catalog.load_catalog()
    w = {"distro": "Ubuntu-24.04", "cwd": "/home/user/proj"}
    c = catalog.build_command(cat, "b1", "claude", "claude-haiku-5-5", "low", "worker", job="j",
                              plugin_dirs=[r"C:\plug\x"], claude_args=["--no-chrome", "--add-dir", r"C:\copies"], wsl=w)
    assert c[:5] == ["wsl.exe", "-d", "Ubuntu-24.04", "--cd", "/home/user/proj"] and c[10] == "claude"
    assert c[c.index("--mcp-config") + 1].startswith("/mnt/") and c[c.index("--plugin-dir") + 1] == "/mnt/c/plug/x"
    assert c[c.index("--add-dir") + 1] == "/mnt/c/copies"
    mcp = j.loads((tmp_path / "agents" / "b1" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["agent_hub"]
    assert mcp["command"].startswith("/mnt/") and mcp["command"].endswith("python.exe")
    assert mcp["env"]["AGENT_HUB_WSL"] == "Ubuntu-24.04" and "AGENT_HUB_HOME" in mcp["env"]["WSLENV"]
    hooks = j.loads((tmp_path / "agents" / "b1" / "claude-settings.json").read_text(encoding="utf-8"))
    assert hooks["hooks"]["Stop"][0]["hooks"][0]["command"].startswith('"/mnt/')
    x = catalog.build_command(cat, "c1", "codex", "gpt-6-luna", "low", "worker", job="j", wsl=w)
    assert x[10] == "codex" and any(a.startswith('notify=["/mnt/') for a in x)
