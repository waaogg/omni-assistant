"""Recurring schedule with explicit date exceptions and conflict detection."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from core.database import Database, utc_now
from core.time_rules import CHINA_TZ, overlaps


class ScheduleService:
    def __init__(self, database: Database):
        self.db = database

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        weekday = int(entry["weekday"])
        if not 0 <= weekday <= 6:
            raise ValueError("weekday must be 0..6")
        for value in (entry["start_time"], entry["end_time"]):
            hour, minute = (int(part) for part in value.split(":", 1))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("time must use HH:MM")
        item = {
            "id": str(entry.get("id") or uuid.uuid4()),
            "title": str(entry["title"]),
            "weekday": weekday,
            "start_time": str(entry["start_time"]),
            "end_time": str(entry["end_time"]),
            "start_date": str(entry["start_date"]),
            "end_date": str(entry["end_date"]),
            "location": str(entry.get("location") or ""),
            "exceptions_json": json.dumps(entry.get("exceptions", []), ensure_ascii=False),
            "created_at": utc_now(),
        }
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO schedule_entries(id,title,weekday,start_time,end_time,start_date,end_date,location,exceptions_json,created_at) "
                "VALUES(:id,:title,:weekday,:start_time,:end_time,:start_date,:end_date,:location,:exceptions_json,:created_at) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,weekday=excluded.weekday,"
                "start_time=excluded.start_time,end_time=excluded.end_time,start_date=excluded.start_date,"
                "end_date=excluded.end_date,location=excluded.location,exceptions_json=excluded.exceptions_json",
                item,
            )
        item["exceptions"] = json.loads(item.pop("exceptions_json"))
        return item

    def occurrences(self, start: date, end: date) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM schedule_entries ORDER BY weekday,start_time").fetchall()
        result = []
        current = start
        while current <= end:
            for row in rows:
                if row["weekday"] != current.weekday():
                    continue
                if not (date.fromisoformat(row["start_date"]) <= current <= date.fromisoformat(row["end_date"])):
                    continue
                exceptions = json.loads(row["exceptions_json"])
                exception = next((item for item in exceptions if item.get("date") == current.isoformat()), None)
                if exception and exception.get("cancelled"):
                    continue
                start_text = exception.get("start_time", row["start_time"]) if exception else row["start_time"]
                end_text = exception.get("end_time", row["end_time"]) if exception else row["end_time"]
                start_clock = time.fromisoformat(start_text)
                end_clock = time.fromisoformat(end_text)
                start_at = datetime.combine(current, start_clock, CHINA_TZ)
                end_at = datetime.combine(current, end_clock, CHINA_TZ)
                if end_at <= start_at:
                    end_at += timedelta(days=1)
                result.append({
                    "id": row["id"], "title": row["title"],
                    "location": exception.get("location", row["location"]) if exception else row["location"],
                    "start_at": start_at.isoformat(timespec="seconds"),
                    "due_at": end_at.isoformat(timespec="seconds"),
                })
            current += timedelta(days=1)
        return result

    def conflicts_with_tasks(self, start: date, end: date) -> list[dict[str, Any]]:
        occurrences = self.occurrences(start, end)
        conflicts = []
        for occurrence in occurrences:
            for task in self.db.list_tasks("open"):
                if overlaps(occurrence["start_at"], occurrence["due_at"], task.get("start_at"), task.get("due_at")):
                    conflicts.append({"schedule": occurrence, "task": task})
        return conflicts
