"""Microsoft To Do adapter for the unified task service."""

from __future__ import annotations

from typing import Any

from core.todo_helper import (
    add_todo_task,
    delete_todo_task,
    list_active_todo_tasks,
    list_todo_tasks_result,
    update_todo_task,
)


class MicrosoftTodoRemote:
    def create(self, task: dict[str, Any]) -> dict[str, Any]:
        return add_todo_task(
            task["title"],
            task.get("context", ""),
            task.get("original_time_text", ""),
            is_shopping=any(k in task["title"] for k in ("买", "采购", "购物", "寄", "快递")),
        )

    def update(self, remote_id: str, task: dict[str, Any]) -> dict[str, Any]:
        return update_todo_task(
            remote_id,
            title=task.get("title"),
            content=task.get("context"),
            when_text=task.get("original_time_text"),
            status=task.get("status"),
        )

    def delete(self, remote_id: str) -> dict[str, Any]:
        return delete_todo_task(remote_id)

    def list(self) -> list[dict[str, Any]]:
        result = list_todo_tasks_result()
        if not result.get("success"):
            raise RuntimeError(f"Microsoft To Do list failed: {result.get('error')}")
        return result.get("tasks", [])
