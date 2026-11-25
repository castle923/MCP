import json
import time

import pytest
from conftest import CONTRACT

from agent_hub import catalog, dispatch, hook
from agent_hub.db import HOP_CAP


# ---- board ------------------------------------------------------------------
def test_request_needs_contract(hub):
    with pytest.raises(ValueError, match="OBJECTIVE"):
        hub.post("main", "w1", "request", "s", "do it")
    assert hub.post("main", "w1", "request", "s", CONTRACT)


def test_hop_cap_escalates_to_main(hub):
    mid = hub.post("a", "b", "query", "q", "?")[0]
    for i in range(HOP_CAP):
        mid = hub.post("b" if i % 2 == 0 else "a", "a" if i % 2 == 0 else "b", "query", "q", "?", in_reply_to=mid)[0]
    over = hub.post("a", "b", "query", "q", "?", in_reply_to=mid)[0]
    row = hub.q("SELECT * FROM messages WHERE id=?", over)[0]
    assert row["recipient"] == "main" and row["needs_human"] == 1 and row["hops"] == HOP_CAP + 1


def test_broadcast_human_and_processed_idempotent(hub):
    for r in ("main", "w1", "w2"):
        hub.add_agent(r, "claude", "m", "medium")
    assert len(hub.post("w1", "broadcast", "inform", "s", "b")) == 2  # everyone but sender
    hub.post("w1", "human", "query", "s", "b")
    assert hub.inbox("main")[-1]["needs_human"] == 1
    ids = [m["id"] for m in hub.inbox("w2")]
    assert hub.mark_processed("w2", ids) == 1
    assert hub.mark_processed("w2", ids) == 0 and hub.inbox("w2") == []
    assert len(hub.inbox("w2", include_processed=True)) == 1  # never deleted


def test_tasks_only_assignee(hub):
    tid = hub.create_task("w1", CONTRACT)
    with pytest.raises(ValueError, match="assigned"):
        hub.set_task(tid, "w2", "done")
    assert hub.set_task(tid, "w1", "done", "ok")["result"] == "ok"


def test_steer_merged_and_taken_once(hub):
    hub.push_steer("w1", "a")
    hub.push_steer("w1", "b")
    assert hub.take_steer("w1") == "a\n\nb"
    assert hub.take_steer("w1") is None


# ---- catalog ------------------------------------------------------------------
def test_catalog_rejects_unknown():
    cat = catalog.load_catalog()
    with pytest.raises(ValueError, match="not in the claude catalog"):
        catalog.validate(cat, "claude", "gpt-6.1-sol", None)
    with pytest.raises(ValueError, match="effort"):
        catalog.validate(cat, "codex", "gpt-6.1-sol", "ultra")  # hub-desk copy: codex takes low..max now
    assert catalog.validate(cat, "codex", "gpt-6.1-sol", None) == "medium"


def test_build_commands(hub, monkeypatch):
    cat = catalog.load_catalog()
    monkeypatch.setattr(catalog, "codex_user_notify", lambda: ["orig.exe", "turn-ended"])
    c = catalog.build_command(cat, "w1", "claude", "claude-opus-5-5", "medium", "worker")
    assert c[1:5] == ["--model", "claude-opus-5-5", "--effort", "medium"] and "--append-system-prompt" in c
    settings = json.loads(open(c[c.index("--settings") + 1], encoding="utf-8").read())
    assert set(settings["hooks"]) == set(catalog.HOOK_EVENTS)
    x = catalog.build_command(cat, "w2", "codex", "gpt-6.1-sol", "high", "worker")
    assert x[1:3] == ["-m", "gpt-6.1-sol"] and 'model_reasoning_effort="high"' in x
    notify = next(a for a in x if a.startswith("notify="))
    assert "--chain" in notify and "orig.exe" in notify
    assert 'mcp_servers.agent_hub.tools.read_messages.approval_mode="approve"' in x
    assert not any("hub_assign" in a for a in x)  # orchestration tools keep codex's approval prompt
    main = catalog.build_command(cat, "main", "claude", "claude-opus-5-5", "medium", "main")
    assert "--append-system-prompt" not in main


def test_state_dir_per_folder_matches_extension(monkeypatch, tmp_path):
    import os
    import subprocess
    from pathlib import Path
    from agent_hub import db
    monkeypatch.delenv("AGENT_HUB_HOME", raising=False)
    a, b = tmp_path / "Proj A", tmp_path / "proj-b"
    a.mkdir(), b.mkdir()
    monkeypatch.chdir(a)
    home_a = db.home()
    assert db.workspace_key(str(a).upper()) == db.workspace_key(str(a) + "\\")  # case/trailing slash
    monkeypatch.chdir(b)
    assert db.home() != home_a and "agent-hub" in str(home_a)
    ext = Path(catalog.__file__).parent.parent / "vscode-ext" / "extension.js"
    if not ext.exists():  # hub-desk copy: the VS Code extension stays in the original install
        pytest.skip("vscode-ext is not part of this copy")
    js = ("const M=require('module');const o=M._load;M._load=(r,...x)=>r==='vscode'?{}:o(r,...x);"
          f"console.log(require({json.dumps(str(ext))}).stateDirFor(process.argv[1]))")
    out = subprocess.run(["node", "-e", js, str(a)], capture_output=True, text=True, env=os.environ).stdout.strip()
    assert out == str(home_a)


def test_worker_pane_cannot_get_main_profile():
    from agent_hub.cli import serve_identity
    assert serve_identity("main", "main", "claude-impl") == ("worker", "claude-impl")
    assert serve_identity("main", "main", None) == ("main", "main")
    assert serve_identity("main", "main", "main") == ("main", "main")


def test_ps_quoting():
    assert catalog.ps_command(["a b", "it's"]).endswith("& 'a b' 'it''s'")


# ---- hooks ------------------------------------------------------------------
def test_hook_status_deny_and_steer(hub):
    hub.add_agent("w1", "claude", "m", "medium")
    hook.handle(hub, "w1", {"hook_event_name": "Notification", "notification_type": "permission_prompt"})
    assert hub.agent("w1")["status"] == "waiting_approval" and hub.agent("w1")["hitl_at"] > 0
    hook.handle(hub, "w1", {"hook_event_name": "Stop", "session_id": "s1"})
    assert hub.agent("w1")["status"] == "idle" and hub.agent("w1")["session_id"] == "s1"
    hub.update_agent("w1", paused=1)
    out = hook.handle(hub, "w1", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert hook.handle(hub, "w1", {"hook_event_name": "PreToolUse", "tool_name": "Read"}) == {}
    hub.push_steer("w1", "change course")
    out = hook.handle(hub, "w1", {"hook_event_name": "PostToolUse"})
    assert "change course" in out["hookSpecificOutput"]["additionalContext"]
    assert hook.handle(hub, "w1", {"hook_event_name": "PostToolUse"}) == {}


def test_hook_without_role_is_noop(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_HUB_ROLE", raising=False)
    assert hook.hook_main() == 0 and capsys.readouterr().out == ""


def test_codex_notify(hub):
    hub.add_agent("cx", "codex", "m", "medium")
    hook.notify_main(["--role", "cx", json.dumps({"type": "agent-turn-complete", "thread-id": "t1"})])
    a = hub.agent("cx")
    assert a["status"] == "idle" and a["session_id"] == "t1"
    hub.update_agent("cx", status="ended", ended_at=time.time())
    hook.notify_main(["--role", "cx", json.dumps({"type": "agent-turn-complete"})])
    hook.handle(hub, "cx", {"hook_event_name": "Stop"})
    assert hub.agent("cx")["status"] == "ended"  # late events after cancel


# ---- dispatcher -----------------------------------------------------------------
def agent(**kw):
    now = time.time()
    base = dict(status="idle", paused=0, ended_at=None, last_event_at=now - 100, started_at=now - 100,
                hitl_at=0, nudged_at=0)
    return {**base, **kw}


def test_should_nudge_gates():
    now = time.time()
    assert dispatch.should_nudge(agent(), True, now)
    assert not dispatch.should_nudge(agent(), False, now)
    assert not dispatch.should_nudge(agent(status="busy"), True, now)
    assert dispatch.should_nudge(agent(status="booting", cli="claude", session_id="s"), True, now)
    assert not dispatch.should_nudge(agent(status="booting", cli="claude", session_id=None), True, now)
    assert not dispatch.should_nudge(agent(status="booting", cli="codex"), True, now)
    assert not dispatch.should_nudge(agent(last_event_at=now - 5), True, now)   # not quiet 12 s
    assert not dispatch.should_nudge(agent(started_at=now - 5), True, now)      # boot grace
    assert not dispatch.should_nudge(agent(hitl_at=now - 10), True, now)        # recent approval prompt
    assert not dispatch.should_nudge(agent(nudged_at=now - 10), True, now)      # cooldown
    assert not dispatch.should_nudge(agent(paused=1), True, now)


def test_breaker_ladder():
    lvl = "healthy"
    for want in ("steering", "constrained", "constrained"):
        lvl = dispatch.breaker_step(lvl, True)
        assert lvl == want  # never 'stopped' without hard_stop
    assert dispatch.breaker_step("constrained", True, hard_stop=True) == "stopped"
    assert dispatch.breaker_step("constrained", False) == "steering"


def test_beat_delivers_once_and_breaks_loops(hub, monkeypatch):
    sent = []
    monkeypatch.setattr(dispatch.panes, "call", lambda op, **kw: sent.append((op, kw)) or {"ok": True})
    hub.add_agent("w1", "claude", "m", "medium")
    hub.update_agent("w1", status="idle", started_at=0, last_event_at=0)
    hub.post("main", "w1", "inform", "s", "b")
    dispatch.beat(hub, time.time())
    assert sent[0][0] == "send" and hub.agent("w1")["status"] == "busy"
    hub.update_agent("w1", status="idle", nudged_at=0)
    dispatch.beat(hub, time.time())
    assert len(sent) == 1  # same mail is not re-announced
    for _ in range(dispatch.REPEAT_N):
        hook.handle(hub, "w1", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"c": 1}})
    dispatch.beat(hub, time.time())
    assert hub.agent("w1")["breaker"] == "steering" and hub.take_steer("w1")
    hub.update_agent("w1", breaker="constrained")
    hub.event("w1", "PreToolUse", {"tool": "Read"})  # break the repeat streak -> healthy beat
    dispatch.beat(hub, time.time())
    assert hub.agent("w1")["breaker"] == "steering" and hub.take_steer("w1") is None  # no warning going down


# ---- team roster / context usage ------------------------------------------------
def test_roster_main_codex_builder_reviewer():
    team = catalog.load_team()
    r = catalog.roster(team)
    assert [(p["role"], p["job"], p["cli"]) for p in r] == [
        ("main", "main", "codex"), ("builder", "builder", "claude"), ("reviewer", "reviewer", "claude")]
    four = catalog.roster(team, 4)[1:]
    assert [(p["role"], p["job"]) for p in four] == [
        ("builder", "builder"), ("builder2", "builder"), ("builder3", "builder"), ("reviewer", "reviewer")]
    assert [p["job"] for p in catalog.roster(team, 1)] == ["main", "builder"]
    assert [p["role"] for p in catalog.roster(team, 2, ["impl", ""])][1:] == ["impl", "reviewer"]
    assert catalog.roster(team, 0) == r[:1]
    for bad in (["main"], ["a b"], ["x", "x"]):
        with pytest.raises(ValueError):
            catalog.roster(team, 2, bad)
    with pytest.raises(ValueError, match="0..6"):
        catalog.roster(team, 7)
    assert "reviewer" in catalog.job_text(team, "reviewer")


def test_job_text_reaches_worker_prompt(hub):
    cat = catalog.load_catalog()
    c = catalog.build_command(cat, "rv", "claude", "claude-opus-5-5", "medium", "worker", "Your job: reviewer.")
    assert c[c.index("--append-system-prompt") + 1].endswith("Your job: reviewer.")


def test_claude_context_from_transcript(hub, tmp_path):
    from agent_hub import context
    t = tmp_path / "t.jsonl"
    rows = [{"type": "assistant", "message": {"usage": {"input_tokens": 1, "cache_read_input_tokens": 9}}},
            {"type": "assistant", "isSidechain": True, "message": {"usage": {"input_tokens": 999}}},
            {"type": "assistant", "message": {"usage": {"input_tokens": 2, "cache_creation_input_tokens": 100,
                                                        "cache_read_input_tokens": 50000, "output_tokens": 898}}},
            {"type": "user", "message": {"content": "x"}}]
    t.write_text("\n".join(json.dumps(r) for r in rows[:1] + rows[2:3] + rows[1:2] + rows[3:]), encoding="utf-8")
    assert context.claude_used(t) == 51000
    hub.add_agent("b", "claude", "m", "medium", ctx_window=200000)
    hook.handle(hub, "b", {"hook_event_name": "Stop", "transcript_path": str(t)})
    assert hub.agent("b")["ctx_used"] == 51000
    assert context.ctx_text(51000, 200000) == "25% (51.0k/200k)"


def test_codex_context_from_rollout(hub, tmp_path, monkeypatch):
    day = tmp_path / "codex" / "sessions" / "2026" / "11" / "14"
    day.mkdir(parents=True)
    tok = {"type": "event_msg", "payload": {"type": "token_count", "info": {
        "last_token_usage": {"total_tokens": 85552}, "model_context_window": 475000}}}
    (day / "rollout-2026-11-14T10-00-00-th1.jsonl").write_text(json.dumps(tok) + "\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    hub.add_agent("main", "codex", "gpt-6.1-sol", "medium")
    hook.notify_main(["--role", "main", json.dumps({"type": "agent-turn-complete", "thread-id": "th1"})])
    a = hub.agent("main")
    assert (a["ctx_used"], a["ctx_window"]) == (85552, 475000) and a["ctx_src"].endswith("th1.jsonl")


def test_old_board_gets_new_columns(tmp_path):
    import sqlite3
    from agent_hub.db import Board
    con = sqlite3.connect(tmp_path / "old.sqlite3")
    con.execute("CREATE TABLE agents(role TEXT PRIMARY KEY, cli TEXT, model TEXT, effort TEXT, choice_source TEXT,"
                " status TEXT, session_id TEXT, pid INTEGER, started_at REAL, last_event_at REAL, hitl_at REAL,"
                " nudged_at REAL, paused INTEGER, breaker TEXT, ended_at REAL)")
    con.close()
    b = Board(tmp_path / "old.sqlite3")
    b.add_agent("x", "claude", "m", "low", job="builder")
    assert b.agent("x")["job"] == "builder"
    b.close()


def test_configuring_pane_is_not_nudged():
    assert not dispatch.should_nudge(agent(status="configuring", cli="claude", session_id=None), True, time.time())


def test_dispatcher_lease_single_owner(hub):
    t = time.time()
    assert dispatch.claim(hub, "a", t) and not dispatch.claim(hub, "b", t + 1)
    assert dispatch.claim(hub, "a", t + 2)                       # holder keeps it
    assert dispatch.claim(hub, "b", t + 2 + dispatch.LEASE_S)    # stale holder loses it


def test_codex_main_auto_approves_dispatch_tools(hub, monkeypatch):
    monkeypatch.setattr(catalog, "codex_user_notify", lambda: None)
    x = catalog.build_command(catalog.load_catalog(), "main", "codex", "gpt-6.1-sol", "medium", "main")
    assert 'mcp_servers.agent_hub.tools.hub_wait_all.approval_mode="approve"' in x
    assert not any("hub_cancel" in a for a in x)


def test_main_cannot_message_itself_and_session_start_is_ready(hub):
    for to in ("main", "human"):
        with pytest.raises(ValueError, match="itself"):
            hub.post("main", to, "inform", "s", "b")
    hub.add_agent("rv", "claude", "m", "medium")
    hook.handle(hub, "rv", {"hook_event_name": "SessionStart", "source": "startup", "session_id": "s"})
    assert hub.agent("rv")["status"] == "idle"
    hook.handle(hub, "rv", {"hook_event_name": "UserPromptSubmit"})
    hook.handle(hub, "rv", {"hook_event_name": "SessionStart", "source": "compact"})
    assert hub.agent("rv")["status"] == "busy"


def test_new_pane_drops_stale_mail(hub, monkeypatch):
    from agent_hub import server
    hub.post("w", "builder", "inform", "old", "from yesterday")
    monkeypatch.setattr(server.panes, "call", lambda op, **kw: {"pid": 1})
    server.open_pane(hub, "builder", "claude", "claude-opus-5-5", "medium", "worker", "menu", "builder")
    assert hub.inbox("builder") == []


def test_codex_main_gets_hub_instructions_and_no_native_subagents(hub, monkeypatch):
    monkeypatch.setattr(catalog, "codex_user_notify", lambda: None)
    cat = catalog.load_catalog()
    m = catalog.build_command(cat, "main", "codex", "gpt-6.1-sol", "medium", "main", catalog.MAIN_GUIDE)
    assert m[m.index("--disable") + 1] == "multi_agent"
    di = next(a for a in m if a.startswith("developer_instructions="))
    assert json.loads(di.split("=", 1)[1]) == catalog.MAIN_GUIDE  # JSON string literal is valid TOML here
    w = catalog.build_command(cat, "cx", "codex", "gpt-6.1-sol", "medium", "worker")
    assert "multi_agent" in w and not any(a.startswith("developer_instructions") for a in w)
    c = catalog.build_command(cat, "main", "claude", "claude-opus-5-5", "medium", "main", "GUIDE")
    assert c[c.index("--append-system-prompt") + 1] == "GUIDE"


def test_reap_marks_closed_panes_and_reports_empty(hub, monkeypatch):
    hub.add_agent("main", "codex", "m", "medium")
    hub.add_agent("builder", "claude", "m", "medium")
    later = time.time() + dispatch.PANE_GRACE_S + 1
    monkeypatch.setattr(dispatch.panes, "call", lambda op, **kw: {"panes": [{"role": "main"}]})
    assert dispatch.reap(hub, later) and hub.agent("builder")["status"] == "ended"
    monkeypatch.setattr(dispatch.panes, "call", lambda op, **kw: {"panes": []})
    assert not dispatch.reap(hub, later) and hub.agent("main")["ended_at"]


def test_injected_activate_line_detection():
    from agent_hub import cli
    assert cli.injected(r"C:\Users\u\anaconda3\Scripts\conda.exe activate eyetoarm")
    assert cli.injected("& c:/proj/.venv/Scripts/Activate.ps1") and not cli.injected("codex")


def test_mods_uses_fixed_models(monkeypatch, tmp_path):
    import json as _j
    from agent_hub import cli, panes
    monkeypatch.setenv("AGENT_HUB_HOME", str(tmp_path))
    monkeypatch.setattr(panes, "call", lambda op, **kw: {"pid": 1})
    assert cli.main(["mods", "3"]) == 0
    got = {r: _j.loads((tmp_path / "agents" / r / "pane.json").read_text(encoding="utf-8"))
           for r in ("main", "builder", "builder2", "reviewer")}
    assert [(got[r]["cli"], got[r]["model"]) for r in got] == [
        ("codex", "gpt-6.1-sol"), ("claude", "claude-opus-5-5"), ("claude", "claude-opus-5-5"),
        ("claude", "claude-opus-5-5")]
    assert "ask" not in got["builder"]
