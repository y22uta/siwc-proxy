"""SQLite persistence; atomic rate reservations and durable session history."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import secrets
import os
from contextlib import contextmanager
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, mode TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS requests (
                    session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    result TEXT NOT NULL,
                    PRIMARY KEY (session_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS usage_recent ON usage_events(created_at);
            """)
        # The database contains private prompts and answers; owner-only by default.
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def new_session(self, mode: str, label: str) -> dict:
        sid = secrets.token_urlsafe(18)
        now = utcnow()
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (sid, label[:120], mode, now, now))
        return {"session_id": sid, "mode": mode, "label": label[:120], "created_at": now}

    def session(self, sid: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 30) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def history(self, sid: str, limit: int = 24) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT role, content, model, created_at FROM (SELECT id, role, content, model, created_at FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id ASC", (sid, limit)).fetchall()
        return [dict(r) for r in rows]

    def save_turn(self, sid: str, prompt: str, answer: str, model: str, request_id: str | None, result: dict) -> None:
        with self.connect() as db:
            if request_id is not None:
                db.execute("INSERT INTO requests VALUES(?,?,?)", (sid, request_id, json.dumps(result, ensure_ascii=False)))
            now = utcnow()
            db.execute("INSERT INTO messages(session_id, role, content, model, created_at) VALUES(?,?,?,?,?)", (sid, "user", prompt, model, now))
            db.execute("INSERT INTO messages(session_id, role, content, model, created_at) VALUES(?,?,?,?,?)", (sid, "assistant", answer, model, now))
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))

    def previous_result(self, sid: str, request_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT result FROM requests WHERE session_id=? AND request_id=?", (sid, request_id)).fetchone()
        return json.loads(row["result"]) if row else None

    def delete_session(self, sid: str) -> bool:
        with self.connect() as db:
            db.execute("DELETE FROM requests WHERE session_id=?", (sid,))
            db.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            cur = db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        return cur.rowcount > 0

    def reserve_call(self, mode: str, limit: int) -> int:
        """Atomic reservation: no extra model call once the local hourly limit is exhausted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM usage_events WHERE created_at >= ?", (cutoff,)).fetchone()[0]
            if count >= limit:
                raise ValueError("Local hourly limit reached; no model call made")
            cur = db.execute("INSERT INTO usage_events(created_at,mode,status,duration_ms) VALUES(?,?,?,0)", (utcnow(), mode, "pending"))
            return int(cur.lastrowid)

    def finish_call(self, event_id: int, status: str, duration_ms: int) -> None:
        with self.connect() as db:
            db.execute("UPDATE usage_events SET status=?, duration_ms=? WHERE id=?", (status, duration_ms, event_id))

    def stats(self) -> dict:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.connect() as db:
            recent = db.execute("SELECT COUNT(*) FROM usage_events WHERE created_at >= ?", (cutoff,)).fetchone()[0]
            sessions = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            rows = db.execute("SELECT status, COUNT(*) AS count FROM usage_events WHERE created_at >= ? GROUP BY status", (cutoff,)).fetchall()
        return {"calls_last_hour": recent, "sessions": sessions, "status_counts": {r["status"]: r["count"] for r in rows}}
