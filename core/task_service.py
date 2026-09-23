"""Unified task domain service used by every channel and the dashboard."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from core.database import Database, utc_now
from core.time_rules import overlaps, parse_time_rule


class RemoteTodo(Protocol):
    def create(self, task: dict[str, Any]) -> dict[str, Any]: ...
    def update(self, remote_id: str, task: dict[str, Any]) -> dict[str, Any]: ...
    def delete(self, remote_id: str) -> dict[str, Any]: ...
    def list(self) -> list[dict[str, Any]]: ...


class TaskService:
    def __init__(self, database: Database, remote: RemoteTodo | None = None):
        self.db = database
        self.remote = remote

    def propose(self, message_id: int | None, proposal: dict[str, Any]) -> dict[str, Any]:
        confidence = float(proposal.get("confidence", 0))
        time_data = parse_time_rule(str(proposal.get("when") or ""))
        relevance = str(proposal.get("relevance") or "unknown")
        reasons = []
        if relevance not in {"required", "all", "personal"}:
            reasons.append("relevance_uncertain")
        if time_data.needs_confirmation:
            reasons.append(time_data.reason)
        if confidence < 0.92:
            reasons.append("low_confidence")
        normalized = {
            "title": str(proposal.get("what") or "").strip(),
            "assignee": str(proposal.get("who") or "").strip(),
            "context": str(proposal.get("context") or "").strip(),
            "original_time_text": time_data.original,
            "start_at": time_data.start_at,
            "due_at": time_data.due_at,
            "reminder_at": time_data.reminder_at,
            "recurrence": time_data.recurrence,
            "relevance": relevance,
            "quadrant": str(proposal.get("quadrant") or "important_not_urgent"),
            "source_message_id": message_id if message_id and message_id > 0 else None,
        }
        if normalized["source_message_id"]:
            with self.db.connect() as conn:
                existing = conn.execute(
                    "SELECT * FROM tasks WHERE source_message_id=? AND title=? AND status!='deleted'",
                    (normalized["source_message_id"], normalized["title"]),
                ).fetchone()
            if existing:
                return {"success": True, "state": "already_exists", "task": dict(existing)}
        if reasons or not normalized["title"]:
            synthetic_message = False
            if not message_id or message_id <= 0:
                message_id, _ = self.db.enqueue_message(
                    channel="manual",
                    external_id=str(uuid.uuid4()),
                    conversation_id="manual",
                    sender_id="manual",
                    body=json.dumps(proposal, ensure_ascii=False),
                )
                normalized["source_message_id"] = message_id
                synthetic_message = True
            item_id = self.db.create_inbox_item(
                message_id, normalized, ",".join(reasons or ["missing_title"]), confidence
            )
            if synthetic_message:
                self.db.finish_message(message_id)
            return {"success": True, "state": "needs_confirmation", "inbox_id": item_id}
        return self.create(normalized)

    def create(self, task: dict[str, Any]) -> dict[str, Any]:
        task = dict(task)
        task["id"] = str(task.get("id") or uuid.uuid4())
        if self.remote:
            try:
                remote_tasks = self.remote.list()
            except Exception as exc:
                return {"success": False, "state": "remote_check_failed", "error": str(exc)}
            for remote_task in remote_tasks:
                due = remote_task.get("dueDateTime") or {}
                if remote_task.get("title") == task.get("title") and (
                    not task.get("due_at") or due.get("dateTime") == task.get("due_at")
                ):
                    task["remote_id"] = remote_task.get("id")
                    saved = self.db.upsert_task(task, actor="remote_recovery")
                    return {"success": True, "state": "recovered", "task": saved}
            remote_result = self.remote.create(task)
            if not remote_result.get("success"):
                return {"success": False, "state": "remote_failed", "error": remote_result.get("error")}
            task["remote_id"] = remote_result.get("id")
        saved = self.db.upsert_task(task, actor="assistant")
        return {"success": True, "state": "created", "task": saved}

    def update(self, task_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.db.get_task(task_id)
        if not current:
            return {"success": False, "error": "task_not_found"}
        updated = {**current, **changes, "id": task_id}
        if "original_time_text" in changes:
            parsed = parse_time_rule(str(changes["original_time_text"]))
            updated.update(
                start_at=parsed.start_at,
                due_at=parsed.due_at,
                reminder_at=parsed.reminder_at,
                recurrence=parsed.recurrence,
            )
        if self.remote and current.get("remote_id"):
            result = self.remote.update(current["remote_id"], updated)
            if not result.get("success"):
                return {"success": False, "state": "remote_failed", "error": result.get("error")}
        saved = self.db.upsert_task(updated, actor="assistant")
        return {"success": True, "state": "updated", "task": saved}

    def complete(self, task_id: str) -> dict[str, Any]:
        return self.update(task_id, {"status": "completed"})

    def delete(self, task_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            return {"success": False, "state": "confirmation_required", "task_id": task_id}
        current = self.db.get_task(task_id)
        if not current:
            return {"success": False, "error": "task_not_found"}
        if self.remote and current.get("remote_id"):
            result = self.remote.delete(current["remote_id"])
            if not result.get("success"):
                return {"success": False, "state": "remote_failed", "error": result.get("error")}
        saved = self.db.upsert_task({**current, "status": "deleted"}, actor="confirmed_delete")
        return {"success": True, "state": "deleted", "task": saved}

    def defer(self, task_id: str, days: int = 1) -> dict[str, Any]:
        current = self.db.get_task(task_id)
        if not current or not current.get("due_at"):
            return {"success": False, "error": "task_or_due_date_missing"}
        due = datetime.fromisoformat(current["due_at"]) + timedelta(days=days)
        changes: dict[str, Any] = {"due_at": due.isoformat(timespec="seconds")}
        if current.get("start_at"):
            changes["start_at"] = (
                datetime.fromisoformat(current["start_at"]) + timedelta(days=days)
            ).isoformat(timespec="seconds")
        return self.update(task_id, changes)

    def mark_waiting(self, task_id: str, party: str) -> dict[str, Any]:
        return self.update(task_id, {"awaiting": party})

    def conflicts(self, candidate: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            task for task in self.db.list_tasks("open")
            if overlaps(task.get("start_at"), task.get("due_at"), candidate.get("start_at"), candidate.get("due_at"))
        ]

    def reconcile(self) -> dict[str, int]:
        if not self.remote:
            return {"seen": 0, "updated": 0}
        remote_tasks = self.remote.list()
        local_by_remote = {t["remote_id"]: t for t in self.db.list_tasks(None) if t.get("remote_id")}
        changed = 0
        for remote in remote_tasks:
            remote_id = remote.get("id")
            local = local_by_remote.get(remote_id)
            if not local:
                self.db.upsert_task(
                    {
                        "remote_id": remote_id,
                        "title": remote.get("title") or "Imported task",
                        "context": remote.get("body") or "",
                        "due_at": (remote.get("dueDateTime") or {}).get("dateTime"),
                        "status": remote.get("status") or "open",
                    },
                    actor="reconcile",
                )
                changed += 1
            elif remote.get("status") and remote.get("status") != local.get("status"):
                self.db.upsert_task({**local, "status": remote["status"]}, actor="reconcile")
                changed += 1
        return {"seen": len(remote_tasks), "updated": changed}

    def undo(self, task_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            events = conn.execute(
                "SELECT * FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 2", (task_id,)
            ).fetchall()
        if not events:
            return {"success": False, "error": "no_history"}
        latest = events[0]
        before = json.loads(latest["before_json"]) if latest["before_json"] else None
        if before is None:
            return self.update(task_id, {"status": "cancelled"})
        return self.update(task_id, before)

    def digest(self, now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
        ref = now or datetime.now(timezone.utc)
        today, soon, overdue = [], [], []
        for task in self.db.list_tasks("open"):
            if not task.get("due_at"):
                continue
            due = datetime.fromisoformat(task["due_at"])
            ref_cmp = ref.astimezone(due.tzinfo) if due.tzinfo else ref.replace(tzinfo=None)
            delta = due - ref_cmp
            if delta.total_seconds() < 0:
                overdue.append(task)
            elif due.date() == ref_cmp.date():
                today.append(task)
            elif delta <= timedelta(days=3):
                soon.append(task)
        return {"today": today, "soon": soon, "overdue": overdue}
