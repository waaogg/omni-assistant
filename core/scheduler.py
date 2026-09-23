"""Deterministic schedule planner for digests, reviews, reconciliation and follow-ups."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from core.assistant_features import AssistantFeatures
from core.task_service import TaskService

logger = logging.getLogger("Scheduler")


class Scheduler:
    def __init__(
        self,
        features: AssistantFeatures,
        tasks: TaskService,
        notify: Callable[[str, dict[str, Any]], None] | None = None,
        daily_time: str = "08:00",
        weekly_day: int = 0,
        quiet_hours: str = "22:00-07:00",
    ):
        self.features = features
        self.tasks = tasks
        self.notify = notify or (lambda kind, payload: logger.info("scheduled notification ready: %s", kind))
        self._last_daily: str | None = None
        self._last_weekly: str | None = None
        self._last_followup: str | None = None
        self._last_reconcile: str | None = None
        self.daily_hour, self.daily_minute = self._parse_clock(daily_time)
        self.weekly_day = weekly_day
        quiet_start, quiet_end = quiet_hours.split("-", 1)
        self.quiet_start = self._parse_clock(quiet_start)
        self.quiet_end = self._parse_clock(quiet_end)

    @staticmethod
    def _parse_clock(value: str) -> tuple[int, int]:
        hour, minute = (int(part) for part in value.split(":", 1))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("invalid scheduler time")
        return hour, minute

    def _quiet(self, ref: datetime) -> bool:
        value = (ref.hour, ref.minute)
        if self.quiet_start <= self.quiet_end:
            return self.quiet_start <= value < self.quiet_end
        return value >= self.quiet_start or value < self.quiet_end

    def tick(self, now: datetime | None = None) -> list[str]:
        ref = now or datetime.now(timezone.utc)
        emitted = []
        day_key = ref.date().isoformat()
        hour_key = ref.strftime("%Y-%m-%dT%H")
        if self._last_reconcile != hour_key:
            try:
                self.tasks.reconcile()
            except Exception:
                logger.exception("Microsoft To Do reconciliation failed")
            self._last_reconcile = hour_key
        if self._quiet(ref):
            return emitted
        if (ref.hour, ref.minute) >= (self.daily_hour, self.daily_minute) and self._last_daily != day_key:
            self.notify("daily_digest", self.features.daily_digest(ref))
            self._last_daily = day_key
            emitted.append("daily_digest")
        week_key = f"{ref.isocalendar().year}-{ref.isocalendar().week}"
        if ref.weekday() == self.weekly_day and (ref.hour, ref.minute) >= (self.daily_hour, self.daily_minute) and self._last_weekly != week_key:
            self.notify("weekly_review", self.features.weekly_review(ref))
            self._last_weekly = week_key
            emitted.append("weekly_review")
        followups = self.features.due_followups(ref)
        if followups and self._last_followup != day_key:
            self.notify("followups", {"tasks": followups})
            self._last_followup = day_key
            emitted.append("followups")
        return emitted

    async def run(self, stop_event: asyncio.Event, interval_seconds: int = 60) -> None:
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("scheduled maintenance failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass
