"""Deterministic time parsing and scheduling helpers.

The original text is always preserved.  If a phrase cannot be parsed without
guessing, the caller receives ``needs_confirmation=True`` instead of a made-up
date.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any


CHINA_TZ = timezone(timedelta(hours=8))


@dataclass
class ParsedTime:
    original: str
    start_at: str | None = None
    due_at: str | None = None
    reminder_at: str | None = None
    recurrence: str | None = None
    needs_confirmation: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def parse_time_rule(text: str, now: datetime | None = None, default_advance: int = 15) -> ParsedTime:
    raw = (text or "").strip()
    if not raw:
        return ParsedTime(raw, needs_confirmation=True, reason="missing_time")
    base = now.astimezone(CHINA_TZ) if now else datetime.now(CHINA_TZ)
    recurrence = None
    if "每天" in raw:
        recurrence = "daily"
    elif "每周" in raw:
        recurrence = "weekly"
    elif "每月" in raw:
        recurrence = "monthly"

    date_match = re.search(r"(?:(\d{4})[年/.-])?(\d{1,2})[月/.-](\d{1,2})[日号]?", raw)
    relative_days = 0 if "今天" in raw else 1 if "明天" in raw else 2 if "后天" in raw else None
    if date_match:
        year = int(date_match.group(1) or base.year)
        month, day = int(date_match.group(2)), int(date_match.group(3))
        try:
            date_value = datetime(year, month, day, tzinfo=CHINA_TZ)
            if not date_match.group(1) and date_value.date() < base.date():
                date_value = date_value.replace(year=year + 1)
        except ValueError:
            return ParsedTime(raw, needs_confirmation=True, reason="invalid_date", recurrence=recurrence)
    elif relative_days is not None:
        date_value = datetime.combine(base.date() + timedelta(days=relative_days), time(), CHINA_TZ)
    else:
        return ParsedTime(raw, recurrence=recurrence, needs_confirmation=True, reason="unparsed_date")

    clock_matches = list(re.finditer(r"(\d{1,2})[:：](\d{2})", raw))
    if clock_matches:
        first = clock_matches[0]
        hour, minute = int(first.group(1)), int(first.group(2))
        if hour > 23 or minute > 59:
            return ParsedTime(raw, recurrence=recurrence, needs_confirmation=True, reason="invalid_time")
        start = date_value.replace(hour=hour, minute=minute)
        due = start
        if len(clock_matches) > 1:
            second = clock_matches[1]
            end_hour, end_minute = int(second.group(1)), int(second.group(2))
            if end_hour > 23 or end_minute > 59:
                return ParsedTime(raw, recurrence=recurrence, needs_confirmation=True, reason="invalid_end_time")
            due = date_value.replace(hour=end_hour, minute=end_minute)
            if due < start:
                due += timedelta(days=1)
    else:
        start = None
        due = date_value.replace(hour=23, minute=59, second=59)

    advance = re.search(r"提前\s*(\d+)\s*分钟", raw)
    advance_minutes = int(advance.group(1)) if advance else default_advance
    reminder = (start or due) - timedelta(minutes=advance_minutes)
    return ParsedTime(raw, _iso(start), _iso(due), _iso(reminder), recurrence)


def overlaps(left_start: str | None, left_due: str | None, right_start: str | None, right_due: str | None) -> bool:
    if not all((left_start, left_due, right_start, right_due)):
        return False
    left_a, left_b = datetime.fromisoformat(left_start), datetime.fromisoformat(left_due)
    right_a, right_b = datetime.fromisoformat(right_start), datetime.fromisoformat(right_due)
    return max(left_a, right_a) < min(left_b, right_b)
