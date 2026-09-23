from datetime import datetime, timezone
from pathlib import Path

from core.assistant_features import AssistantFeatures
from core.database import Database
from core.knowledge import KnowledgeService, schedule_conflicts
from core.message_processor import MessageProcessor
from core.scheduler import Scheduler
from core.task_service import TaskService
from core.time_rules import parse_time_rule
from core.schedule_service import ScheduleService
from core.channel_agent import ChannelAgent
from unittest.mock import patch
import os
import subprocess
import sys


class FakeRemote:
    def __init__(self, fail=False):
        self.fail = fail
        self.items = []

    def create(self, task):
        if self.fail:
            return {"success": False, "error": "injected remote failure"}
        result = {"success": True, "id": f"remote-{len(self.items) + 1}", **task}
        self.items.append(result)
        return result

    def update(self, remote_id, task):
        return {"success": not self.fail, "id": remote_id, "error": "injected remote failure" if self.fail else None}

    def delete(self, remote_id):
        return {"success": not self.fail}

    def list(self):
        return list(self.items)


def test_idempotent_ingestion_and_multi_task_extraction(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    service = TaskService(db)
    processor = MessageProcessor(
        db,
        service,
        lambda _: {
            "action": "new",
            "tasks": [
                {"what": "Submit form", "when": "2026-09-23 10:00", "relevance": "all", "confidence": 0.99},
                {"what": "Attend meeting", "when": "2026-09-23 14:00", "relevance": "required", "confidence": 0.99},
            ],
        },
    )
    first = processor.ingest(channel="qq", external_id="message-1", conversation_id="private-group", sender_id="private-user", body="notice")
    second = processor.ingest(channel="qq", external_id="message-1", conversation_id="private-group", sender_id="private-user", body="notice")
    assert first["accepted"] is True
    assert second["duplicate"] is True
    result = processor.run_once()
    assert result[0]["success"] is True
    assert len(db.list_tasks()) == 2
    with db.connect() as conn:
        stored = conn.execute("SELECT conversation_key,sender_key FROM messages").fetchone()
    assert "private-group" not in stored["conversation_key"]
    assert "private-user" not in stored["sender_key"]


def test_uncertain_item_enters_inbox(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    message_id, _ = db.enqueue_message(channel="test", external_id="1", conversation_id="c", sender_id="s", body="x")
    result = TaskService(db).propose(message_id, {"what": "Maybe join", "when": "later", "relevance": "optional", "confidence": 0.4})
    assert result["state"] == "needs_confirmation"
    assert len(db.list_inbox()) == 1


def test_remote_failure_never_reports_success_or_saves_task(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    result = TaskService(db, FakeRemote(fail=True)).create({"title": "Critical task"})
    assert result["success"] is False
    assert db.list_tasks() == []


def test_processor_retries_after_injected_failure(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    processor = MessageProcessor(db, TaskService(db), lambda _: (_ for _ in ()).throw(RuntimeError("injected")))
    processor.ingest(channel="test", external_id="1", conversation_id="c", sender_id="s", body="x")
    result = processor.run_once()
    assert result[0]["success"] is False
    with db.connect() as conn:
        row = conn.execute("SELECT status,last_error FROM messages").fetchone()
    assert row["status"] == "retry"
    assert "injected" in row["last_error"]


def test_time_parsing_conflicts_and_rollover():
    now = datetime(2026, 12, 31, 12, tzinfo=timezone.utc)
    parsed = parse_time_rule("1月2日 09:00-10:30，提前30分钟", now=now)
    assert parsed.start_at.startswith("2027-01-02T09:00")
    assert parsed.due_at.startswith("2027-01-02T10:30")
    assert parsed.reminder_at.startswith("2027-01-02T08:30")
    entries = [
        {"start_at": parsed.start_at, "due_at": parsed.due_at},
        {"start_at": "2027-01-02T10:00:00+08:00", "due_at": "2027-01-02T11:00:00+08:00"},
    ]
    assert len(schedule_conflicts(entries)) == 1
    assert parse_time_rule("some day").needs_confirmation is True


def test_search_digest_review_and_scheduler(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    service = TaskService(db)
    knowledge = KnowledgeService(db)
    knowledge.remember("qq", "source-one", "Registration", "Registration closes Friday")
    assert knowledge.search("Registration")[0]["title"] == "Registration"
    service.create({"title": "Today", "due_at": "2026-09-22T20:00:00+00:00"})
    features = AssistantFeatures(db, service)
    assert len(features.daily_digest(datetime(2026, 9, 22, 10, tzinfo=timezone.utc))["today"]) == 1
    emitted = []
    scheduler = Scheduler(features, service, lambda kind, payload: emitted.append(kind))
    kinds = scheduler.tick(datetime(2026, 9, 21, 8, tzinfo=timezone.utc))
    assert "daily_digest" in kinds and "weekly_review" in kinds
    assert emitted == kinds


def test_task_update_history_and_undo(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    service = TaskService(db)
    created = service.create({"title": "Original"})["task"]
    service.update(created["id"], {"title": "Changed"})
    result = service.undo(created["id"])
    assert result["success"] is True
    assert db.get_task(created["id"])["title"] == "Original"


def test_channel_agent_uses_shared_tools_and_persistent_conversation(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    service = TaskService(db)
    agent = ChannelAgent(db, service)
    db.bind_identity("primary", "qq", "user-one")
    db.bind_identity("primary", "wechat", "user-two")
    with patch("core.channel_agent.ai_provider.call_agent") as call:
        call.return_value = "done"
        assert agent.run("qq", "user-one", "list my tasks") == "done"
        assert agent.run("wechat", "user-two", "show sources") == "done"
        second_messages = call.call_args.args[0]
        assert any(message.get("content") == "list my tasks" for message in second_messages)
    assert len(db.load_conversation("primary", "qq")["turns"]) == 2


def test_confirmed_delete_requires_explicit_command(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    service = TaskService(db)
    task = service.create({"title": "Delete me"})["task"]
    assert service.delete(task["id"])["state"] == "confirmation_required"
    response = ChannelAgent(db, service).run("qq", "owner", f"/confirm-delete {task['id']}")
    assert '"success": true' in response
    assert db.get_task(task["id"])["status"] == "deleted"


def test_encrypted_backup_round_trip(tmp_path: Path):
    database_path = tmp_path / "state.db"
    db = Database(database_path)
    TaskService(db).create({"title": "Backup task"})
    backup = tmp_path / "state.omnibak"
    restored = tmp_path / "restored.db"
    env = {**os.environ, "OMNI_BACKUP_PASSWORD": "test-password-long-enough"}
    script = Path(__file__).resolve().parent.parent / "scripts" / "secure_backup.py"
    subprocess.run([sys.executable, str(script), "create", str(database_path), str(backup)], check=True, env=env)
    assert backup.read_bytes().startswith(b"OMNIBAK1")
    assert b"Backup task" not in backup.read_bytes()
    subprocess.run([sys.executable, str(script), "restore", str(backup), str(restored)], check=True, env=env)
    assert Database(restored).list_tasks()[0]["title"] == "Backup task"


def test_recurring_schedule_exceptions_and_task_conflicts(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    schedules = ScheduleService(db)
    schedules.add({
        "title": "Generic course",
        "weekday": 0,
        "start_time": "09:00",
        "end_time": "10:30",
        "start_date": "2026-09-01",
        "end_date": "2026-10-31",
        "exceptions": [
            {"date": "2026-09-21", "cancelled": True},
            {"date": "2026-09-28", "start_time": "10:00", "end_time": "11:00"},
        ],
    })
    assert schedules.occurrences(datetime(2026, 9, 21).date(), datetime(2026, 9, 21).date()) == []
    moved = schedules.occurrences(datetime(2026, 9, 28).date(), datetime(2026, 9, 28).date())
    assert moved[0]["start_at"].endswith("10:00:00+08:00")
    TaskService(db).create({
        "title": "Conflicting task",
        "start_at": "2026-09-28T10:30:00+08:00",
        "due_at": "2026-09-28T11:30:00+08:00",
    })
    assert len(schedules.conflicts_with_tasks(datetime(2026, 9, 28).date(), datetime(2026, 9, 28).date())) == 1
