"""Protocol test: real stdio MCP server process, real client."""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def run(tmp, profile, role, calls):
    params = StdioServerParameters(command=sys.executable, args=["-m", "agent_hub", "serve", "--profile", profile,
                                                                 "--role", role],
                                   env={**os.environ, "AGENT_HUB_HOME": str(tmp), "PYTHONPATH": str(ROOT)})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = sorted(t.name for t in (await s.list_tools()).tools)
        results = [await s.call_tool(n, a) for n, a in calls]
        return tools, results


def text(res):
    return json.loads(res.content[0].text) if res.content else None


def test_worker_profile_has_board_tools_only(tmp_path):
    tools, _ = asyncio.run(run(tmp_path, "worker", "w1", []))
    assert tools == ["board_post", "claim_task", "complete_task", "post_message", "read_messages"]  # hub-desk adds board_post


def test_main_and_worker_roundtrip(tmp_path):
    tools, res = asyncio.run(run(tmp_path, "main", "main", [
        ("post_message", {"to": "w1", "act": "inform", "subject": "hi", "body": "hello"}),
        ("hub_status", {}),
        ("hub_models", {})]))
    assert {"hub_assign", "hub_wait", "hub_cancel", "hub_pause"} <= set(tools)
    assert not res[0].is_error
    _, res = asyncio.run(run(tmp_path, "worker", "w1", [("read_messages", {}), ("read_messages", {})]))
    first = res[0].structured_content or text(res[0])
    assert "hello" in json.dumps(first)
    assert "hello" not in json.dumps(res[1].structured_content or text(res[1]) or [])


def test_spawn_reuses_running_role(tmp_path):
    os.environ.pop("AGENT_HUB_ROLE", None)
    sys.path.insert(0, str(ROOT))
    from agent_hub.db import Board
    b = Board(tmp_path / "hub.sqlite3")
    b.add_agent("builder", "claude", "claude-opus-5-5", "medium", job="builder")
    b.update_agent("builder", status="idle")
    b.close()
    _, res = asyncio.run(run(tmp_path, "main", "main", [
        ("hub_assign", {"role": "builder", "objective": "o", "output": "p", "tools": "t", "boundaries": "b"}),
        ("hub_status", {"target": "builder"})]))
    out = res[0].structured_content or text(res[0])
    assert out["reused_pane"] and out["task_id"]
    st = res[1].structured_content or text(res[1])
    assert st["agents"][0]["job"] == "builder" and st["agents"][0]["context"] == "-"


def test_parallel_tasks_and_dispatcher_thread(tmp_path):
    from agent_hub.db import Board
    b = Board(tmp_path / "hub.sqlite3")
    for r in ("builder", "builder2"):
        b.add_agent(r, "claude", "claude-opus-5-5", "medium", job="builder")
        b.update_agent(r, status="idle")
    b.close()
    spec = {"objective": "o", "output": "p", "tools": "t", "boundaries": "b"}
    _, res = asyncio.run(run(tmp_path, "main", "main", [
        ("hub_assign", {"role": "builder", **spec}), ("hub_assign", {"role": "builder2", **spec})]))
    ids = [(r.structured_content or text(r))["task_id"] for r in res]
    b = Board(tmp_path / "hub.sqlite3")
    b.set_task(ids[0], "builder", "done", "ok")
    b.close()
    _, res = asyncio.run(run(tmp_path, "main", "main", [("hub_wait_all", {"task_ids": ids, "timeout_s": 3})]))
    out = res[0].structured_content or text(res[0])
    assert not out["all_done"] and [t["status"] for t in out["tasks"]] == ["done", "running"]
    b = Board(tmp_path / "hub.sqlite3")
    assert b.meta("dispatcher_owner")  # the server's dispatcher thread took the lease
    b.close()
