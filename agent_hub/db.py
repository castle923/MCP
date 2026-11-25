"""SQLite board shared by the MCP servers, hook commands and the dispatcher.

SQLite (WAL) is the only IPC between processes: hooks write events straight into
it, so no long-running hub daemon is needed for status tracking.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent  # agent-hub/
HOP_CAP = 6
ACTS = ("request", "inform", "propose", "query", "agree", "refuse", "done")
REPLY_ACTS = ("request", "query", "propose")
CONTRACT = ("OBJECTIVE", "OUTPUT", "TOOLS", "BOUNDARIES")

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents(
  role TEXT PRIMARY KEY, cli TEXT, model TEXT, effort TEXT, choice_source TEXT,
  status TEXT DEFAULT 'booting', session_id TEXT, pid INTEGER,
  started_at REAL, last_event_at REAL, hitl_at REAL DEFAULT 0, nudged_at REAL DEFAULT 0,
  paused INTEGER DEFAULT 0, breaker TEXT DEFAULT 'healthy', ended_at REAL,
  job TEXT, ctx_used INTEGER, ctx_window INTEGER, ctx_src TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id TEXT PRIMARY KEY, conversation TEXT, in_reply_to TEXT, sender TEXT, recipient TEXT,
  act TEXT, subject TEXT, body TEXT, hops INTEGER, requires_reply INTEGER, needs_human INTEGER,
  created_at REAL, announced_at REAL, processed_at REAL);
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, assignee TEXT, spec TEXT, status TEXT, result TEXT,
  created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, role TEXT, kind TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS steer(
  id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, text TEXT, created_at REAL, taken_at REAL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
# columns added after the first release; older boards get them via ALTER TABLE
LATE_AGENT_COLS = {"job": "TEXT", "ctx_used": "INTEGER", "ctx_window": "INTEGER", "ctx_src": "TEXT"}


def workspace_key(folder: str) -> str:
    """Stable id for a workspace folder. vscode-ext/extension.js stateDirFor() must match this."""
    p = os.path.normcase(os.path.abspath(folder)).rstrip("\\/")
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in os.path.basename(p))[:40] or "root"
    return f"{name}-{hashlib.sha1(p.encode('utf-8')).hexdigest()[:10]}"


def home() -> Path:
    """State dir: AGENT_HUB_HOME (set for panes) or one dir per workspace (= cwd) under LOCALAPPDATA,
    so no hub files are written into the user's project folders."""
    if os.environ.get("AGENT_HUB_HOME"):
        return Path(os.environ["AGENT_HUB_HOME"])
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "agent-hub"
    return base / workspace_key(os.getcwd())


def new_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def check_contract(body: str) -> None:
    missing = [k for k in CONTRACT if k not in (body or "").upper()]
    if missing:
        raise ValueError("request body must contain sections: " + ", ".join(missing))


class Board:
    def __init__(self, path: Path | None = None):
        path = Path(path or home() / "hub.sqlite3")
        path.parent.mkdir(parents=True, exist_ok=True)
        # MCP runs sync tools in worker threads; sqlite3 (serialized build) allows sharing the connection
        self.con = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        # two Boards opening a fresh file at once (MCP server + its dispatcher thread) can hit "database is
        # locked" while switching to WAL: the busy timeout does not cover that, so retry briefly.
        for attempt in range(20):
            try:
                if self.con.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                    self.con.execute("PRAGMA journal_mode=WAL")
                self.con.executescript(SCHEMA)
                have = {r["name"] for r in self.q("PRAGMA table_info(agents)")}
                for col, typ in LATE_AGENT_COLS.items():
                    if col not in have:
                        self.con.execute(f"ALTER TABLE agents ADD COLUMN {col} {typ}")
                break
            except sqlite3.OperationalError as e:
                if not ("locked" in str(e) or "duplicate column" in str(e)) or attempt == 19:
                    raise
                time.sleep(0.25)

    def close(self) -> None:
        self.con.close()

    def q(self, sql: str, *args) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    # ---- agents -------------------------------------------------------------
    def add_agent(self, role, cli, model, effort, choice_source="menu", job=None, ctx_window=None,
                  status="booting") -> None:
        now = time.time()
        self.con.execute(
            "INSERT INTO agents(role,cli,model,effort,choice_source,status,started_at,last_event_at,job,ctx_window)"
            " VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(role) DO UPDATE SET cli=excluded.cli,"
            " model=excluded.model, effort=excluded.effort, choice_source=excluded.choice_source,"
            " status=excluded.status, started_at=excluded.started_at, last_event_at=excluded.last_event_at,"
            " job=excluded.job, ctx_window=excluded.ctx_window, ctx_used=NULL, ctx_src=NULL,"
            " ended_at=NULL, paused=0, breaker='healthy', session_id=NULL",
            (role, cli, model, effort, choice_source, status, now, now, job, ctx_window))
        self.event(role, "spawn" if status == "booting" else status,
                   {"cli": cli, "model": model, "effort": effort, "job": job, "source": choice_source})

    def agent(self, role) -> dict | None:
        rows = self.q("SELECT * FROM agents WHERE role=?", role)
        return rows[0] if rows else None

    def agents(self, active_only=False) -> list[dict]:
        return self.q("SELECT * FROM agents" + (" WHERE ended_at IS NULL" if active_only else "") + " ORDER BY started_at")

    def update_agent(self, role, **fields) -> None:
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            self.con.execute(f"UPDATE agents SET {cols} WHERE role=?", (*fields.values(), role))

    # ---- messages -----------------------------------------------------------
    def post(self, sender, recipient, act, subject, body, in_reply_to=None, needs_human=False) -> list[str]:
        if act not in ACTS:
            raise ValueError(f"act must be one of {ACTS}")
        if act == "request":
            check_contract(body)
        hops, conversation = 0, None
        if in_reply_to:
            parent = self.q("SELECT hops, conversation FROM messages WHERE id=?", in_reply_to)
            if not parent:
                raise ValueError(f"unknown in_reply_to {in_reply_to}")
            hops, conversation = parent[0]["hops"] + 1, parent[0]["conversation"]
        if hops > HOP_CAP:  # ping-pong guard: escalate to main instead
            recipient, subject, needs_human = "main", f"[hop cap {HOP_CAP}] {subject}", True
        if recipient == "human":
            recipient, needs_human = "main", True
        if recipient == "broadcast":
            targets = [a["role"] for a in self.agents(active_only=True) if a["role"] != sender]
        else:
            targets = [recipient]
        if sender in targets:  # main once reported to itself and got nudged by its own mail in a loop
            raise ValueError(f"'{sender}' cannot message itself" + ("; tell the user in chat instead"
                                                                    if sender == "main" else ""))
        ids = []
        for to in targets:
            mid = new_id()
            self.con.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                (mid, conversation or mid, in_reply_to, sender, to, act, subject, body,
                 hops, int(act in REPLY_ACTS), int(needs_human), time.time()))
            ids.append(mid)
            self.event(sender, "message", {"id": mid, "to": to, "act": act, "subject": subject})
        return ids

    def inbox(self, role, include_processed=False) -> list[dict]:
        sql = "SELECT * FROM messages WHERE recipient=?" + ("" if include_processed else " AND processed_at IS NULL")
        return self.q(sql + " ORDER BY created_at", role)

    def mark_processed(self, role, ids) -> int:
        cur = self.con.execute(
            f"UPDATE messages SET processed_at=? WHERE recipient=? AND processed_at IS NULL"
            f" AND id IN ({','.join('?' * len(ids))})", (time.time(), role, *ids)) if ids else None
        return cur.rowcount if cur else 0

    def unannounced(self, role) -> list[str]:
        return [r["id"] for r in self.q(
            "SELECT id FROM messages WHERE recipient=? AND processed_at IS NULL AND announced_at IS NULL", role)]

    def mark_announced(self, ids) -> None:
        if ids:
            self.con.execute(f"UPDATE messages SET announced_at=? WHERE id IN ({','.join('?' * len(ids))})",
                             (time.time(), *ids))

    # ---- tasks --------------------------------------------------------------
    def create_task(self, assignee, spec) -> str:
        check_contract(spec)
        tid, now = new_id(), time.time()
        self.con.execute("INSERT INTO tasks VALUES(?,?,?,'open',NULL,?,?)", (tid, assignee, spec, now, now))
        self.event("main", "task", {"id": tid, "assignee": assignee})
        return tid

    def task(self, tid) -> dict | None:
        rows = self.q("SELECT * FROM tasks WHERE id=?", tid)
        return rows[0] if rows else None

    def set_task(self, tid, role, status, result=None) -> dict:
        t = self.task(tid)
        if not t:
            raise ValueError(f"unknown task {tid}")
        if t["assignee"] != role:
            raise ValueError(f"task {tid} is assigned to {t['assignee']}, not {role}")
        self.con.execute("UPDATE tasks SET status=?, result=COALESCE(?, result), updated_at=? WHERE id=?",
                         (status, result, time.time(), tid))
        self.event(role, "task_" + status, {"id": tid})
        return self.task(tid)

    # ---- events / steer / meta ---------------------------------------------
    def event(self, role, kind, data=None) -> None:
        self.con.execute("INSERT INTO events(ts,role,kind,data) VALUES(?,?,?,?)",
                         (time.time(), role, kind, json.dumps(data or {}, ensure_ascii=False)))

    def events_after(self, seq) -> list[dict]:
        return self.q("SELECT * FROM events WHERE seq>? ORDER BY seq", seq)

    def push_steer(self, role, text) -> None:
        self.con.execute("INSERT INTO steer(role,text,created_at) VALUES(?,?,?)", (role, text, time.time()))

    def take_steer(self, role) -> str | None:
        rows = self.q("SELECT id, text FROM steer WHERE role=? AND taken_at IS NULL ORDER BY id", role)
        if not rows:
            return None
        self.con.execute(f"UPDATE steer SET taken_at=? WHERE id IN ({','.join('?' * len(rows))})",
                         (time.time(), *[r["id"] for r in rows]))
        return "\n\n".join(r["text"] for r in rows)  # one additionalContext per hook: merge

    def set_meta(self, key, value) -> None:
        self.con.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps(value)))

    def meta(self, key, default=None):
        rows = self.q("SELECT value FROM meta WHERE key=?", key)
        return json.loads(rows[0]["value"]) if rows else default
