"""Source-grounded local search and schedule helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.database import Database
from core.time_rules import overlaps


class KnowledgeService:
    def __init__(self, database: Database):
        self.db = database

    def remember(self, channel: str, source_key: str, title: str, body: str) -> str:
        return self.db.add_document(channel, source_key, title, body)

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        return self.db.search_documents(query, limit)

    def answer_context(self, query: str, limit: int = 5) -> str:
        results = self.search(query, limit)
        if not results:
            return "未找到可核对的本地来源。"
        blocks = []
        for item in results:
            excerpt = " ".join(item["body"].split())[:280]
            blocks.append(
                f"[{item['channel']}] {item['title']} ({item['captured_at']}): {excerpt}"
            )
        return "\n".join(blocks)


def schedule_conflicts(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts = []
    for index, left in enumerate(entries):
        for right in entries[index + 1:]:
            if overlaps(left.get("start_at"), left.get("due_at"), right.get("start_at"), right.get("due_at")):
                conflicts.append({"left": left, "right": right})
    return conflicts
