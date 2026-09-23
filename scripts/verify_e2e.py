#!/usr/bin/env python3
"""Offline end-to-end verifier with a fake external task provider."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.assistant_features import AssistantFeatures
from core.database import Database
from core.knowledge import KnowledgeService
from core.message_processor import MessageProcessor
from core.scheduler import Scheduler
from core.task_service import TaskService


class FakeRemote:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.items = []

    def list(self):
        if self.fail:
            raise RuntimeError("injected remote outage")
        return self.items

    def create(self, task):
        if self.fail:
            return {"success": False, "error": "injected remote outage"}
        item = {"success": True, "id": f"remote-{len(self.items) + 1}", **task}
        self.items.append(item)
        return item

    def update(self, remote_id, task):
        return {"success": not self.fail, "id": remote_id}

    def delete(self, remote_id):
        return {"success": not self.fail}


def main() -> int:
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
        database = Database(Path(temp) / "verify.db")
        remote = FakeRemote()
        service = TaskService(database, remote)
        processor = MessageProcessor(
            database,
            service,
            lambda _: {
                "action": "new",
                "tasks": [
                    {"what": "Submit generic form", "when": "2026-09-23 10:00", "relevance": "all", "confidence": 0.99},
                    {"what": "Optional generic event", "when": "later", "relevance": "optional", "confidence": 0.40},
                ],
            },
        )
        first = processor.ingest(channel="test", external_id="event-1", conversation_id="room", sender_id="sender", body="generic notice")
        duplicate = processor.ingest(channel="test", external_id="event-1", conversation_id="room", sender_id="sender", body="generic notice")
        outcomes = processor.run_once()
        assert first["accepted"] and duplicate["duplicate"]
        assert outcomes[0]["success"]
        assert len(database.list_tasks()) == 1
        assert len(database.list_inbox()) == 1
        knowledge = KnowledgeService(database)
        knowledge.remember("test", "source", "Generic source", "verifiable source text")
        assert knowledge.search("verifiable")
        task = database.list_tasks()[0]
        assert service.complete(task["id"])["success"]
        failed = TaskService(database, FakeRemote(fail=True)).create({"title": "Must not be saved"})
        assert not failed["success"]
        notifications = []
        Scheduler(
            AssistantFeatures(database, service), service,
            lambda kind, payload: notifications.append(kind),
            quiet_hours="23:00-06:00",
        ).tick(datetime(2026, 9, 21, 8, tzinfo=timezone.utc))
        assert "daily_digest" in notifications and "weekly_review" in notifications
        print(json.dumps({
            "idempotency": True,
            "created_tasks": 1,
            "confirmation_items": 1,
            "source_search": True,
            "remote_failure_blocked": True,
            "scheduled_features": notifications,
            "stats": database.stats(),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
