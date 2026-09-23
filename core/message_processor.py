"""Reliable, channel-independent message ingestion and decision execution."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from core.database import Database
from core.knowledge import KnowledgeService
from core.task_service import TaskService

logger = logging.getLogger("MessageProcessor")


DecisionFn = Callable[[str], dict[str, Any]]


class MessageProcessor:
    def __init__(
        self,
        database: Database,
        tasks: TaskService,
        decide: DecisionFn,
        *,
        max_attempts: int = 5,
    ):
        self.db = database
        self.tasks = tasks
        self.decide = decide
        self.knowledge = KnowledgeService(database)
        self.max_attempts = max_attempts

    def ingest(
        self,
        *,
        channel: str,
        external_id: str,
        conversation_id: str | int,
        sender_id: str | int,
        body: str,
    ) -> dict[str, Any]:
        message_id, inserted = self.db.enqueue_message(
            channel=channel,
            external_id=external_id,
            conversation_id=conversation_id,
            sender_id=sender_id,
            body=body,
        )
        if inserted and body.strip():
            self.knowledge.remember(channel, f"message:{external_id}", "Message", body)
        return {"message_id": message_id, "accepted": inserted, "duplicate": not inserted}

    def run_once(self, limit: int = 20) -> list[dict[str, Any]]:
        outcomes = []
        for message in self.db.claim_messages(limit):
            try:
                decision = self.decide(message["body"])
                outcome = self._apply(int(message["id"]), decision)
                self.db.finish_message(int(message["id"]))
                outcomes.append({"message_id": message["id"], **outcome})
            except Exception as exc:
                attempts = int(message.get("attempts") or 0) + 1
                if attempts >= self.max_attempts:
                    with self.db.transaction() as conn:
                        conn.execute(
                            "UPDATE messages SET status='failed',last_error=? WHERE id=?",
                            (str(exc)[:1000], message["id"]),
                        )
                else:
                    delay = min(3600, 2 ** attempts * 10)
                    retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    self.db.retry_message(int(message["id"]), str(exc), retry_at.isoformat(timespec="seconds"))
                outcomes.append({"message_id": message["id"], "success": False, "error": str(exc)})
        return outcomes

    def _apply(self, message_id: int, decision: dict[str, Any]) -> dict[str, Any]:
        action = decision.get("action", "ignore")
        if action in {"ignore", "duplicate"}:
            return {"success": True, "state": action}
        proposals = decision.get("tasks")
        if not isinstance(proposals, list):
            single = decision.get("task") or decision
            proposals = [single]
        results = []
        for proposal in proposals:
            if action == "update" and proposal.get("task_id"):
                results.append(self.tasks.update(str(proposal["task_id"]), proposal))
            else:
                results.append(self.tasks.propose(message_id, proposal))
        return {"success": all(r.get("success") for r in results), "state": action, "results": results}


def parse_decision_json(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("decision must be a JSON object")
    return value
