import subprocess
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List

from core import config

logger = logging.getLogger("TodoHelper")

NODE_TASK_SCRIPT = """
const authPath = process.argv[1];
const action = process.argv[2]; // 'create', 'update', 'delete', 'list'
const payloadRaw = process.argv[3] || '{}';

import(authPath).then(async (m) => {
  const token = await m.getAccessToken();
  const payload = JSON.parse(payloadRaw);
  let listId = payload.listId;

  // If listId is not specified, query user's default list
  if (!listId) {
    try {
      const lRes = await fetch("https://graph.microsoft.com/v1.0/me/todo/lists", {
        headers: { Authorization: `Bearer ${token}` }
      });
      const lData = await lRes.json();
      if (lData && lData.value && lData.value.length > 0) {
        listId = lData.value[0].id;
      }
    } catch (e) {
      console.log(JSON.stringify({ success: false, error: "Failed to resolve default list: " + e.message }));
      return;
    }
  }

  if (action === "create") {
    const body = {
      title: payload.title,
      body: { content: payload.content || "", contentType: "text" }
    };
    if (payload.dueIso) {
      body.dueDateTime = { dateTime: payload.dueIso, timeZone: "Asia/Shanghai" };
    }
    if (payload.reminderIso) {
      body.reminderDateTime = { dateTime: payload.reminderIso, timeZone: "Asia/Shanghai" };
      body.isReminderOn = payload.isReminderOn !== false;
    }

    const res = await fetch(`https://graph.microsoft.com/v1.0/me/todo/lists/${listId}/tasks`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (data && data.id) {
      console.log(JSON.stringify({
        success: true,
        id: data.id,
        title: data.title,
        dueDateTime: data.dueDateTime,
        reminderDateTime: data.reminderDateTime
      }));
    } else {
      console.log(JSON.stringify({ success: false, error: data }));
    }
  } else if (action === "update") {
    const taskId = payload.taskId;
    const body = {};
    if (payload.title) body.title = payload.title;
    if (payload.content !== undefined) body.body = { content: payload.content, contentType: "text" };
    if (payload.dueIso) {
      body.dueDateTime = { dateTime: payload.dueIso, timeZone: "Asia/Shanghai" };
    }
    if (payload.reminderIso) {
      body.reminderDateTime = { dateTime: payload.reminderIso, timeZone: "Asia/Shanghai" };
      body.isReminderOn = payload.isReminderOn !== false;
    }

    const res = await fetch(`https://graph.microsoft.com/v1.0/me/todo/lists/${listId}/tasks/${taskId}`, {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (data && data.id) {
      console.log(JSON.stringify({
        success: true,
        id: data.id,
        title: data.title,
        dueDateTime: data.dueDateTime,
        reminderDateTime: data.reminderDateTime
      }));
    } else {
      console.log(JSON.stringify({ success: false, error: data }));
    }
  } else if (action === "delete") {
    const taskId = payload.taskId;
    const res = await fetch(`https://graph.microsoft.com/v1.0/me/todo/lists/${listId}/tasks/${taskId}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` }
    });
    if (res.status === 204 || res.status === 200 || res.status === 404) {
      console.log(JSON.stringify({ success: true, taskId: taskId }));
    } else {
      const data = await res.text();
      console.log(JSON.stringify({ success: false, error: data }));
    }
  } else if (action === "list") {
    const res = await fetch(`https://graph.microsoft.com/v1.0/me/todo/lists/${listId}/tasks?$filter=status ne 'completed'`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    if (data && data.value) {
      console.log(JSON.stringify({
        success: true,
        tasks: data.value.map(t => ({
          id: t.id,
          title: t.title,
          body: t.body ? t.body.content : "",
          dueDateTime: t.dueDateTime,
          reminderDateTime: t.reminderDateTime
        }))
      }));
    } else {
      console.log(JSON.stringify({ success: false, error: data }));
    }
  }
}).catch((err) => {
  console.log(JSON.stringify({ success: false, error: err.message }));
});
"""

def _run_node_todo(action: str, payload: dict) -> dict:
    if not config.ENABLE_MS_TODO:
        return {"success": False, "error": "Microsoft To Do integration is disabled"}
    
    auth_module = config.MS_TODO_AUTH_MODULE_PATH
    if "listId" not in payload and config.MS_TODO_DEFAULT_LIST_ID:
        payload["listId"] = config.MS_TODO_DEFAULT_LIST_ID

    try:
        res = subprocess.run(
            ["node", "-e", NODE_TASK_SCRIPT, auth_module, action, json.dumps(payload, ensure_ascii=False)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15
        )
        stdout = res.stdout.strip()
        lines = [line.strip() for line in stdout.split("\n") if line.strip().startswith("{") and line.strip().endswith("}")]
        if lines:
            return json.loads(lines[-1])
        return {"success": False, "error": res.stderr.strip() or stdout}
    except Exception as e:
        logger.error(f"Failed to execute node todo script ({action}): {e}")
        return {"success": False, "error": str(e)}

def parse_iso_times(when_text: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse human readable time into ISO 8601 due_iso and reminder_iso."""
    if not when_text:
        return None, None

    now = datetime.now()
    cur_year = now.year

    m = re.search(r'(?:(\d{4})[年\-\./])?\s*(\d{1,2})[月\-\./](\d{1,2})[日号]?(?:\s*(\d{1,2})[:：](\d{1,2}))?', when_text)
    if not m:
        return None, None

    year = int(m.group(1)) if m.group(1) else cur_year
    month = int(m.group(2))
    day = int(m.group(3))
    hour = int(m.group(4)) if m.group(4) is not None else None
    minute = int(m.group(5)) if m.group(5) is not None else None

    try:
        if hour is not None and minute is not None:
            due_dt = datetime(year, month, day, hour, minute, 0)
            advance_m = re.search(r'提前\s*(\d+)\s*分钟', when_text)
            adv_min = int(advance_m.group(1)) if advance_m else config.DEFAULT_REMINDER_ADVANCE_MINUTES
            reminder_dt = due_dt - timedelta(minutes=adv_min)
            return due_dt.strftime("%Y-%m-%dT%H:%M:%S"), reminder_dt.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            due_dt = datetime(year, month, day, 23, 59, 59)
            return due_dt.strftime("%Y-%m-%dT%H:%M:%S"), None
    except Exception:
        return None, None

def add_todo_task(title: str, content: str = "", when_text: str = "", is_shopping: bool = False, list_id: Optional[str] = None) -> dict:
    due_iso, reminder_iso = parse_iso_times(when_text)
    payload = {
        "title": title,
        "content": content,
        "dueIso": due_iso,
        "reminderIso": reminder_iso if not is_shopping else None,
        "isReminderOn": not is_shopping
    }
    if list_id:
        payload["listId"] = list_id
    return _run_node_todo("create", payload)

def update_todo_task(task_id: str, title: Optional[str] = None, content: Optional[str] = None, when_text: Optional[str] = None, is_shopping: bool = False, list_id: Optional[str] = None) -> dict:
    payload = {"taskId": task_id}
    if title:
        payload["title"] = title
    if content is not None:
        payload["content"] = content
    if when_text:
        due_iso, reminder_iso = parse_iso_times(when_text)
        if due_iso:
            payload["dueIso"] = due_iso
        if reminder_iso and not is_shopping:
            payload["reminderIso"] = reminder_iso
            payload["isReminderOn"] = True
    if is_shopping:
        payload["isReminderOn"] = False
    if list_id:
        payload["listId"] = list_id
    return _run_node_todo("update", payload)

def delete_todo_task(task_id: str, list_id: Optional[str] = None) -> dict:
    payload = {"taskId": task_id}
    if list_id:
        payload["listId"] = list_id
    return _run_node_todo("delete", payload)

def list_active_todo_tasks(list_id: Optional[str] = None) -> list:
    payload = {}
    if list_id:
        payload["listId"] = list_id
    res = _run_node_todo("list", payload)
    if res.get("success"):
        return res.get("tasks", [])
    return []
