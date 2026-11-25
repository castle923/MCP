"""agent-hub command line: mods (= up) | pane | models | status | watch | serve | hook | notify."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .catalog import build_command, check_role, context_window, label, load_catalog, load_team, roster, validate
from .context import ctx_text


def drain_typeahead(quiet: float = 0.8, settle: float = 6.0, max_wait: float = 10.0) -> str:
    """VS Code's Python/conda extensions type an 'activate' line into every new terminal, at an unpredictable
    moment. Without this it lands in the agent's input box. Throw away typed keys until the console is quiet
    and either such a line has arrived or `settle` seconds passed. Returns what was dropped."""
    # hub-desk panes are not VS Code terminals: nothing types an activation line into them
    if os.name != "nt" or not sys.stdin.isatty() or os.environ.get("AGENT_HUB_NO_DRAIN"):
        return ""
    import msvcrt
    start = last = time.time()
    buf = ""
    while time.time() - start < max_wait:
        if msvcrt.kbhit():
            buf += msvcrt.getwch()
            last = time.time()
        elif time.time() - last >= quiet and (injected(buf) or time.time() - start >= settle):
            break
        else:
            time.sleep(0.05)
    return buf


def injected(line: str) -> bool:
    """A line typed by an editor extension (conda/venv activation), not by the user."""
    low = line.lower()
    return "activate" in low and ("conda" in low or "scripts" in low or "/bin/" in low)


JOB_KO = {"main": "메인(지휘)", "builder": "빌더(구현)", "reviewer": "리뷰어(검토)"}


def cmd_pane(args) -> int:
    """Runs inside a pane: drop injected keys, then the agent CLI in this console."""
    import subprocess
    from .db import Board
    spec = json.loads(open(args.spec_file, encoding="utf-8").read())
    cat = load_catalog()
    print(f"[{spec['role']}] {JOB_KO.get(spec.get('job'), spec.get('job') or '')} · {spec['cli']}\n", flush=True)
    drain_typeahead()  # before the CLI's own input box
    effort = validate(cat, spec["cli"], spec["model"], spec.get("effort"))
    argv = build_command(cat, spec["role"], spec["cli"], spec["model"], effort, spec["profile"], spec.get("job_text"),
                         spec.get("plugin_dirs"), spec.get("mcp_extra"), spec.get("codex_args"),
                         spec.get("claude_args"), spec.get("resume"))
    board = Board()
    board.add_agent(spec["role"], spec["cli"], spec["model"], effort, spec["source"], spec.get("job"),
                    context_window(cat, spec["cli"], spec["model"]))
    board.close()
    print(f"→ {spec['role']}: {spec['cli']} {label(cat, spec['cli'], spec['model'])} · {effort}\n", flush=True)
    try:
        return subprocess.call(argv)  # inherits the pane console, so the TUI stays interactive
    except KeyboardInterrupt:
        return 130


def cmd_up(args) -> int:
    from .server import open_pane
    from .db import Board
    from . import panes

    cat, team = load_catalog(), load_team()
    n = args.count  # `mods 3`; omitted -> team.toml default_subs (2). No interactive questions here.
    plan = roster(team, n, args.names.split(",") if args.names else [])
    board = Board()
    team_line = ", ".join(f"{p['role']} ({p['job']}, {p['cli']})" for p in plan[1:]) or "no sub agents"
    for p in plan:  # models/efforts are fixed by team.toml
        profile = "main" if p["job"] == "main" else "worker"
        info = open_pane(board, p["role"], p["cli"], p["model"], p.get("effort"), profile, "team.toml",
                         p["job"], team=team_line)
        print(f"opened {info['title']} · {label(cat, p['cli'], p['model'])} · {p.get('effort')}")
    print("dispatcher: 각 에이전트의 MCP 서버 안에서 자동 실행 (별도 창 없음)")
    return 0


def cmd_models(args) -> int:
    cat = load_catalog()
    for cli, spec in cat["cli"].items():
        print(f"{cli}: efforts={spec['efforts']}")
        for m in spec["models"]:
            print(f"  {label(cat, cli, m['id']):<12} {m['id']:<20} {'verified' if m.get('verified') else '미검증'}")
    return 0


def cmd_status(args) -> int:
    from .db import Board
    import time
    board = Board()
    now = time.time()
    for a in board.agents(active_only=not args.all):
        print(f"{a['role']:<12} {(a['job'] or '-'):<9} {a['cli']:<6} {a['model']:<18} {a['effort']:<6}"
              f" {a['status']:<16} ctx={ctx_text(a['ctx_used'], a['ctx_window']):<20} breaker={a['breaker']}"
              f" unread={len(board.inbox(a['role']))} last={round(now - (a['last_event_at'] or now))}s")
    beat = board.meta("dispatcher_beat")
    print("dispatcher:", "alive" if beat and now - beat < 15 else "NOT RUNNING")
    return 0


def serve_identity(profile: str, role: str, pane_role: str | None) -> tuple[str, str]:
    """The user-scope 'main' server also loads inside worker panes; a pane's AGENT_HUB_ROLE wins
    so workers never get orchestration tools (recursion depth 1)."""
    if pane_role and pane_role != "main":
        return "worker", pane_role
    return profile, role


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "notify":  # codex appends a raw JSON payload: bypass argparse
        from .hook import notify_main
        return notify_main(argv[1:])
    ap = argparse.ArgumentParser(prog="agent-hub")
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("mods", aliases=["up"], help="open main + N sub panes in VS Code")
    up.add_argument("count", nargs="?", type=int, help="number of sub agents (default: team.toml default_subs)")
    up.add_argument("--names", help="comma-separated sub agent names, in order")
    pn = sub.add_parser("pane", help="(used by panes) model menu, then the agent CLI")
    pn.add_argument("spec_file")
    r = sub.add_parser("run", help="exec an argv JSON file (panes opened by older agent-hub versions)")
    r.add_argument("argv_file")
    sub.add_parser("models")
    st = sub.add_parser("status")
    st.add_argument("--all", action="store_true", help="include ended agents")
    w = sub.add_parser("watch")
    w.add_argument("--hard-stop", action="store_true")
    s = sub.add_parser("serve")
    s.add_argument("--profile", choices=["main", "worker"], default="worker")
    s.add_argument("--role", required=True)
    sub.add_parser("hook")
    a = ap.parse_args(argv)
    if a.cmd == "run":  # kept so MCP servers started before 2026-11-14 can still open panes
        import subprocess
        try:
            return subprocess.call(json.loads(open(a.argv_file, encoding="utf-8").read()))
        except KeyboardInterrupt:
            return 130
    if a.cmd in ("mods", "up"):
        return cmd_up(a)
    if a.cmd == "pane":
        return cmd_pane(a)
    if a.cmd == "models":
        return cmd_models(a)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "watch":
        from .dispatch import watch
        watch(hard_stop=a.hard_stop)
        return 0
    if a.cmd == "serve":
        import os
        from .server import serve
        profile, role = serve_identity(a.profile, a.role, os.environ.get("AGENT_HUB_ROLE"))
        serve(profile, role)
        return 0
    if a.cmd == "hook":
        from .hook import hook_main
        return hook_main()
    return 1
