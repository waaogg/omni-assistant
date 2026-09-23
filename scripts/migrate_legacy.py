#!/usr/bin/env python3
"""Import legacy task/history JSON into the v2 database without copying IDs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import Database


def load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--tasks", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    task_rows = load(args.tasks, {}) if args.tasks else {}
    history_rows = load(args.history, []) if args.history else []
    report = {"tasks": len(task_rows) if isinstance(task_rows, dict) else 0,
              "messages": len(history_rows) if isinstance(history_rows, list) else 0}
    if args.dry_run:
        print(json.dumps(report))
        return 0
    database = Database(args.database)
    for item in task_rows.values() if isinstance(task_rows, dict) else []:
        database.upsert_task({
            "remote_id": item.get("todo_id"),
            "title": item.get("what") or "Imported task",
            "assignee": item.get("who", ""),
            "context": item.get("context", ""),
            "original_time_text": item.get("when", ""),
            "due_at": item.get("due_iso"),
            "reminder_at": item.get("reminder_iso"),
        }, actor="legacy_import")
    for index, item in enumerate(history_rows if isinstance(history_rows, list) else []):
        external_id = str(item.get("message_id") or f"legacy-{index}")
        database.enqueue_message(
            channel="legacy",
            external_id=external_id,
            conversation_id=item.get("group_id", "unknown"),
            sender_id=item.get("sender", "unknown"),
            body=item.get("text", ""),
            received_at=item.get("received_at"),
        )
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
