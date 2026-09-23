"""SQLite persistence for messages, tasks, inbox decisions, and audit history.

The database deliberately stores no deployment credentials.  Channel-specific
identifiers are hashed before persistence so a copied database does not expose
QQ or WeChat account identifiers.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def private_id(value: str | int | None) -> str:
    """Return a stable, non-reversible identifier suitable for local joins."""
    raw = "" if value is None else str(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class Database:
    """Small transactional repository with one connection per operation."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migration_lock = threading.Lock()
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def migrate(self) -> None:
        with self._migration_lock, self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    sender_key TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    received_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    last_error TEXT,
                    processed_at TEXT,
                    UNIQUE(channel, external_id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_status
                    ON messages(status, next_attempt_at, received_at);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    remote_id TEXT UNIQUE,
                    title TEXT NOT NULL,
                    assignee TEXT NOT NULL DEFAULT '',
                    context TEXT NOT NULL DEFAULT '',
                    original_time_text TEXT NOT NULL DEFAULT '',
                    start_at TEXT,
                    due_at TEXT,
                    reminder_at TEXT,
                    recurrence TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    relevance TEXT NOT NULL DEFAULT 'unknown',
                    quadrant TEXT NOT NULL DEFAULT 'important_not_urgent',
                    awaiting TEXT NOT NULL DEFAULT '',
                    source_message_id INTEGER REFERENCES messages(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_at);
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    actor TEXT NOT NULL DEFAULT 'system',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inbox (
                    id TEXT PRIMARY KEY,
                    message_id INTEGER NOT NULL REFERENCES messages(id),
                    proposal_json TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS identities (
                    person_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    channel_user_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(channel, channel_user_key)
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    person_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    turns_json TEXT NOT NULL DEFAULT '[]',
                    provider_conversation_id TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(person_id, channel)
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    UNIQUE(channel, source_key)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    title, body, content='documents', content_rowid='rowid'
                );
                CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                    INSERT INTO documents_fts(rowid, title, body)
                    VALUES (new.rowid, new.title, new.body);
                END;
                CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                    INSERT INTO documents_fts(documents_fts, rowid, title, body)
                    VALUES ('delete', old.rowid, old.title, old.body);
                END;
                CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                    INSERT INTO documents_fts(documents_fts, rowid, title, body)
                    VALUES ('delete', old.rowid, old.title, old.body);
                    INSERT INTO documents_fts(rowid, title, body)
                    VALUES (new.rowid, new.title, new.body);
                END;
                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                );
                CREATE TABLE IF NOT EXISTS schedule_entries (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    weekday INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    exceptions_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def enqueue_message(
        self,
        *,
        channel: str,
        external_id: str,
        conversation_id: str | int,
        sender_id: str | int,
        body: str,
        received_at: str | None = None,
    ) -> tuple[int, bool]:
        """Persist before processing and return ``(id, inserted)``."""
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO messages "
                "(channel, external_id, conversation_key, sender_key, body, received_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    channel,
                    str(external_id),
                    private_id(conversation_id),
                    private_id(sender_id),
                    body,
                    received_at or utc_now(),
                ),
            )
            row = conn.execute(
                "SELECT id FROM messages WHERE channel=? AND external_id=?",
                (channel, str(external_id)),
            ).fetchone()
            return int(row["id"]), cur.rowcount == 1

    def claim_messages(self, limit: int = 20, channel: str | None = None) -> list[dict[str, Any]]:
        now = utc_now()
        with self.transaction() as conn:
            sql = (
                "SELECT * FROM messages WHERE status IN ('pending','retry') "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
            )
            params: list[Any] = [now]
            if channel is not None:
                sql += "AND channel=? "
                params.append(channel)
            sql += "ORDER BY received_at, id LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                marks = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE messages SET status='processing', attempts=attempts+1 "
                    f"WHERE id IN ({marks})",
                    ids,
                )
            return [dict(row) for row in rows]

    def claim_message(self, message_id: int) -> bool:
        """Atomically claim one pending/retry message for immediate processing."""
        now = utc_now()
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE messages SET status='processing',attempts=attempts+1 "
                "WHERE id=? AND status IN ('pending','retry') "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?)",
                (message_id, now),
            )
            return cur.rowcount == 1

    def finish_message(self, message_id: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE messages SET status='done', processed_at=?, last_error=NULL "
                "WHERE id=?",
                (utc_now(), message_id),
            )

    def retry_message(
        self, message_id: int, error: str, next_attempt_at: str, max_attempts: int | None = None
    ) -> None:
        with self.transaction() as conn:
            row = conn.execute("SELECT attempts FROM messages WHERE id=?", (message_id,)).fetchone()
            terminal = bool(row and max_attempts is not None and int(row["attempts"]) >= max_attempts)
            conn.execute(
                "UPDATE messages SET status=?, last_error=?, next_attempt_at=? WHERE id=?",
                ("failed" if terminal else "retry", error[:1000], None if terminal else next_attempt_at, message_id),
            )

    def upsert_task(self, task: dict[str, Any], actor: str = "system") -> dict[str, Any]:
        now = utc_now()
        task_id = str(task.get("id") or uuid.uuid4())
        fields = {
            "id": task_id,
            "remote_id": task.get("remote_id"),
            "title": str(task.get("title") or "Untitled task"),
            "assignee": str(task.get("assignee") or ""),
            "context": str(task.get("context") or ""),
            "original_time_text": str(task.get("original_time_text") or ""),
            "start_at": task.get("start_at"),
            "due_at": task.get("due_at"),
            "reminder_at": task.get("reminder_at"),
            "recurrence": task.get("recurrence"),
            "status": str(task.get("status") or "open"),
            "relevance": str(task.get("relevance") or "unknown"),
            "quadrant": str(task.get("quadrant") or "important_not_urgent"),
            "awaiting": str(task.get("awaiting") or ""),
            "source_message_id": task.get("source_message_id"),
        }
        with self.transaction() as conn:
            old = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if old:
                merged = dict(old)
                merged.update({k: v for k, v in fields.items() if v is not None})
                merged["updated_at"] = now
                merged["version"] = int(old["version"]) + 1
                columns = [k for k in merged if k not in {"created_at"}]
                conn.execute(
                    "UPDATE tasks SET " + ",".join(f"{c}=?" for c in columns if c != "id") + " WHERE id=?",
                    [merged[c] for c in columns if c != "id"] + [task_id],
                )
                action = "updated"
                before = json.dumps(dict(old), ensure_ascii=False)
            else:
                merged = {**fields, "created_at": now, "updated_at": now, "version": 1}
                columns = list(merged)
                conn.execute(
                    f"INSERT INTO tasks({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                    [merged[c] for c in columns],
                )
                action, before = "created", None
            conn.execute(
                "INSERT INTO task_events(task_id,action,before_json,after_json,actor,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (task_id, action, before, json.dumps(merged, ensure_ascii=False), actor, now),
            )
            return merged

    def list_tasks(self, status: str | None = "open") -> list[dict[str, Any]]:
        with self.connect() as conn:
            if status is None:
                rows = conn.execute("SELECT * FROM tasks ORDER BY due_at IS NULL, due_at").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status=? ORDER BY due_at IS NULL, due_at",
                    (status,),
                ).fetchall()
            return [dict(row) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def set_preference(self, key: str, value: Any) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO preferences(key,value_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), utc_now()),
            )

    def get_preference(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value_json FROM preferences WHERE key=?", (key,)).fetchone()
            return json.loads(row["value_json"]) if row else default

    def bind_identity(self, person_id: str, channel: str, channel_user_id: str | int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO identities(person_id,channel,channel_user_key,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(channel,channel_user_key) DO UPDATE SET person_id=excluded.person_id",
                (person_id, channel, private_id(channel_user_id), utc_now()),
            )

    def resolve_identity(self, channel: str, channel_user_id: str | int) -> str:
        key = private_id(channel_user_id)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT person_id FROM identities WHERE channel=? AND channel_user_key=?", (channel, key)
            ).fetchone()
        return row["person_id"] if row else f"{channel}:{key}"

    def save_conversation(
        self, person_id: str, channel: str, turns: list[dict[str, Any]], provider_id: str | None = None
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO conversations(person_id,channel,turns_json,provider_conversation_id,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(person_id,channel) DO UPDATE SET "
                "turns_json=excluded.turns_json,provider_conversation_id=excluded.provider_conversation_id,"
                "updated_at=excluded.updated_at",
                (person_id, channel, json.dumps(turns[-20:], ensure_ascii=False), provider_id, utc_now()),
            )

    def load_conversation(self, person_id: str, channel: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE person_id=? AND channel=?", (person_id, channel)
            ).fetchone()
        if not row:
            return {"turns": [], "provider_id": None}
        return {"turns": json.loads(row["turns_json"]), "provider_id": row["provider_conversation_id"]}

    def load_person_turns(self, person_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT turns_json FROM conversations WHERE person_id=? ORDER BY updated_at",
                (person_id,),
            ).fetchall()
        turns: list[dict[str, Any]] = []
        for row in rows:
            turns.extend(json.loads(row["turns_json"]))
        return turns[-limit:]

    def add_document(self, channel: str, source_key: str, title: str, body: str) -> str:
        doc_id = str(uuid.uuid4())
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO documents(id,channel,source_key,title,body,captured_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(channel,source_key) DO UPDATE SET title=excluded.title,body=excluded.body",
                (doc_id, channel, private_id(source_key), title, body, utc_now()),
            )
        return doc_id

    def search_documents(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT d.id,d.channel,d.title,d.body,d.captured_at,bm25(documents_fts) AS score "
                "FROM documents_fts JOIN documents d ON d.rowid=documents_fts.rowid "
                "WHERE documents_fts MATCH ? ORDER BY score LIMIT ?",
                (query, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_inbox_item(
        self, message_id: int, proposal: dict[str, Any], reason: str, confidence: float
    ) -> str:
        item_id = str(uuid.uuid4())
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO inbox(id,message_id,proposal_json,reason,confidence,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (item_id, message_id, json.dumps(proposal, ensure_ascii=False), reason, confidence, utc_now()),
            )
        return item_id

    def list_inbox(self, state: str = "pending") -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inbox WHERE state=? ORDER BY created_at", (state,)
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["proposal"] = json.loads(item.pop("proposal_json"))
                result.append(item)
            return result

    def resolve_inbox(self, item_id: str, state: str) -> dict[str, Any] | None:
        if state not in {"accepted", "rejected"}:
            raise ValueError("inbox state must be accepted or rejected")
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE inbox SET state=?,resolved_at=? WHERE id=?",
                (state, utc_now(), item_id),
            )
            item = dict(row)
            item["proposal"] = json.loads(item.pop("proposal_json"))
            return item

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "pending_messages": conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE status IN ('pending','retry','processing')"
                ).fetchone()[0],
                "open_tasks": conn.execute("SELECT COUNT(*) FROM tasks WHERE status='open'").fetchone()[0],
                "inbox_items": conn.execute("SELECT COUNT(*) FROM inbox WHERE state='pending'").fetchone()[0],
                "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "pending_notifications": conn.execute(
                    "SELECT COUNT(*) FROM notifications WHERE state='pending'"
                ).fetchone()[0],
            }

    def add_notification(self, kind: str, payload: dict[str, Any]) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO notifications(kind,payload_json,created_at) VALUES(?,?,?)",
                (kind, json.dumps(payload, ensure_ascii=False), utc_now()),
            )
            return int(cur.lastrowid)

    def claim_notifications(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE state='pending' ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
            if rows:
                conn.executemany(
                    "UPDATE notifications SET state='processing' WHERE id=?",
                    [(row["id"],) for row in rows],
                )
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def finish_notification(self, notification_id: int, success: bool) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE notifications SET state=?,delivered_at=? WHERE id=?",
                ("delivered" if success else "pending", utc_now() if success else None, notification_id),
            )
