"""Personal-assistant features derived exclusively from explicit local state."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from core.database import Database
from core.task_service import TaskService


class AssistantFeatures:
    def __init__(self, database: Database, tasks: TaskService):
        self.db = database
        self.tasks = tasks

    def daily_digest(self, now: datetime | None = None) -> dict[str, Any]:
        return self.tasks.digest(now)

    def weekly_review(self, now: datetime | None = None) -> dict[str, Any]:
        ref = now or datetime.now(timezone.utc)
        start = ref - timedelta(days=7)
        all_tasks = self.db.list_tasks(None)
        completed = [
            task for task in all_tasks
            if task["status"] == "completed" and datetime.fromisoformat(task["updated_at"]) >= start
        ]
        remaining = [task for task in all_tasks if task["status"] == "open"]
        return {
            "period_start": start.isoformat(timespec="seconds"),
            "completed": completed,
            "remaining": remaining,
            "quadrants": dict(Counter(t["quadrant"] for t in remaining)),
        }

    def due_followups(self, now: datetime | None = None) -> list[dict[str, Any]]:
        ref = now or datetime.now(timezone.utc)
        items = []
        for task in self.db.list_tasks("open"):
            if not task.get("due_at"):
                continue
            due = datetime.fromisoformat(task["due_at"])
            ref_cmp = ref.astimezone(due.tzinfo) if due.tzinfo else ref.replace(tzinfo=None)
            if due - timedelta(hours=24) <= ref_cmp <= due:
                items.append(task)
        return items

    def set_preference(self, key: str, value: Any) -> None:
        allowed = {
            "default_reminder_minutes", "quiet_hours", "daily_digest_time",
            "weekly_review_day", "notification_categories", "identity_profile",
        }
        if key not in allowed:
            raise ValueError("unsupported preference")
        self.db.set_preference(key, value)

    def get_preferences(self) -> dict[str, Any]:
        keys = (
            "default_reminder_minutes", "quiet_hours", "daily_digest_time",
            "weekly_review_day", "notification_categories", "identity_profile",
        )
        return {key: self.db.get_preference(key) for key in keys if self.db.get_preference(key) is not None}
