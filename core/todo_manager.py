import json
import os
import hashlib
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from core import config
from core import ai_provider
from core.todo_helper import add_todo_task, update_todo_task, delete_todo_task, list_active_todo_tasks, parse_iso_times
from core.storage import load_json, save_json

logger = logging.getLogger("TodoManager")

TZ_GMT8 = timezone(timedelta(hours=8))

def get_now_gmt8_str() -> str:
    now = datetime.now(TZ_GMT8)
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return now.strftime(f"%Y年%m月%d日 {weekdays[now.weekday()]} %H:%M:%S (GMT+8)")

def normalize_key(s: str) -> str:
    """去除非字母数字汉字，并将时间常用同义词归一"""
    if not s:
        return ""
    s = s.lower().replace("号", "日").replace("：", ":")
    return re.sub(r'[\s\W_]+', '', s)

def get_task_signature(what: str, when: str) -> str:
    """以核心事件与时间节点生成哈希指纹"""
    norm = f"{normalize_key(what)}|{normalize_key(when)}"
    return hashlib.md5(norm.encode('utf-8')).hexdigest()

def load_memory() -> dict:
    mem_file = config.MEMORY_FILE
    data = load_json(mem_file, {})
    return data if isinstance(data, dict) else {}

def save_memory(mem: dict):
    mem_file = config.MEMORY_FILE
    try:
        save_json(mem_file, mem)
    except Exception as e:
        logger.error(f"保存永久记忆库失败: {e}")

def parse_json_object(text: str) -> dict:
    if not text:
        return {}
    text = text.strip()
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    text = re.sub(r'\[Assistant\]:?', '', text, flags=re.IGNORECASE).strip()

    m_code = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m_code:
        try:
            return json.loads(m_code.group(1))
        except Exception:
            pass

    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict) and obj:
                return obj
        except Exception:
            pass
        start = text.find("{", start + 1)
    return {}

def get_all_active_tasks() -> list:
    """获取当前所有有效未完成待办（结合本地记忆库与 Microsoft To Do）"""
    mem = load_memory()
    tasks = []
    for k, v in mem.items():
        tasks.append({
            "id": v.get("todo_id") or k,
            "sig": k,
            "title": f"📌 {v.get('what', '')}",
            "who": v.get("who", ""),
            "what": v.get("what", ""),
            "when": v.get("when", ""),
            "context": v.get("context", ""),
            "due_iso": v.get("due_iso"),
            "reminder_iso": v.get("reminder_iso")
        })
    return tasks

def add_task_with_memory(who: str, what: str, when: str, raw_context: str = "") -> dict:
    who = (who or "相关人员").strip()
    what = (what or "通知待办").strip()
    when = (when or "见详情").strip()

    mem = load_memory()
    # 检查是否已存在一模一样未完成的任务
    for k, item in mem.items():
        if item.get("what") == what and item.get("when") == when:
            logger.info(f"待办完全相同，跳过重复添加: {what}")
            return {"success": True, "already_exists": True, "who": who, "what": what, "when": when, "todo_id": item.get("todo_id")}

    is_shopping = any(kw in what for kw in ["买", "采购", "购物", "寄", "快递"])
    due_iso, reminder_iso = parse_iso_times(when)
    title = f"📌 {what}"
    content_lines = [
        f"• 涉及谁：{who}",
        f"• 干什么：{what}",
        f"• 起止时间：{when}"
    ]
    if raw_context:
        content_lines.append(f"• 补充信息：{raw_context.strip()}")
    content = "\n".join(content_lines)

    res = add_todo_task(title, content, when, is_shopping=is_shopping)
    if res.get("success"):
        sig = get_task_signature(what, when)
        todo_id = res.get("id")
        mem[sig] = {
            "who": who,
            "what": what,
            "when": when,
            "context": raw_context.strip(),
            "due_iso": res.get("dueDateTime", {}).get("dateTime") if isinstance(res.get("dueDateTime"), dict) else due_iso,
            "reminder_iso": res.get("reminderDateTime", {}).get("dateTime") if isinstance(res.get("reminderDateTime"), dict) else reminder_iso,
            "todo_id": todo_id,
            "time_added": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_memory(mem)
        logger.info(f"成功写入待办并录入记忆: {what} (ID: {todo_id})")
        return {
            "success": True,
            "already_exists": False,
            "who": who,
            "what": what,
            "when": when,
            "due_iso": due_iso,
            "reminder_iso": reminder_iso,
            "id": todo_id
        }
    else:
        logger.error(f"写入 Microsoft To Do 失败: {res.get('error')}")
        return {"success": False, "already_exists": False, "error": res.get("error")}

def update_task_with_memory(todo_id_or_sig: str, who: str = None, what: str = None, when: str = None, raw_context: str = None) -> dict:
    mem = load_memory()
    target_key = None
    target_item = None

    for k, v in mem.items():
        if k == todo_id_or_sig or v.get("todo_id") == todo_id_or_sig:
            target_key = k
            target_item = v
            break

    if not target_item:
        return {"success": False, "error": f"未找到待办: {todo_id_or_sig}"}

    todo_id = target_item.get("todo_id")
    new_who = who if who is not None else target_item.get("who", "")
    new_what = what if what is not None else target_item.get("what", "")
    new_when = when if when is not None else target_item.get("when", "")
    new_context = raw_context if raw_context is not None else target_item.get("context", "")

    is_shopping = any(kw in new_what for kw in ["买", "采购", "购物", "寄", "快递"])
    title = f"📌 {new_what}"
    content_lines = [
        f"• 涉及谁：{new_who}",
        f"• 干什么：{new_what}",
        f"• 起止时间：{new_when}"
    ]
    if new_context:
        content_lines.append(f"• 补充信息：{new_context.strip()}")
    content = "\n".join(content_lines)

    res = update_todo_task(todo_id, title=title, content=content, when_text=new_when, is_shopping=is_shopping)
    if res.get("success"):
        target_item["who"] = new_who
        target_item["what"] = new_what
        target_item["when"] = new_when
        target_item["context"] = new_context
        target_item["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if res.get("dueDateTime"):
            target_item["due_iso"] = res.get("dueDateTime", {}).get("dateTime")
        if res.get("reminderDateTime"):
            target_item["reminder_iso"] = res.get("reminderDateTime", {}).get("dateTime")
        save_memory(mem)
        logger.info(f"成功更新待办: {new_what} (ID: {todo_id})")
        return {"success": True, "id": todo_id, "who": new_who, "what": new_what, "when": new_when}
    else:
        logger.error(f"更新 Microsoft To Do 失败: {res.get('error')}")
        return {"success": False, "error": res.get("error")}

def delete_task_with_memory(todo_id_or_sig: str) -> dict:
    mem = load_memory()
    target_key = None
    target_item = None

    for k, v in mem.items():
        if k == todo_id_or_sig or v.get("todo_id") == todo_id_or_sig:
            target_key = k
            target_item = v
            break

    if not target_item:
        return {"success": False, "error": f"未在记忆中找到对应待办: {todo_id_or_sig}"}

    todo_id = target_item.get("todo_id")
    res = delete_todo_task(todo_id)
    if res.get("success"):
        del mem[target_key]
        save_memory(mem)
        logger.info(f"成功删除待办并清除记忆: {target_item.get('what')} (ID: {todo_id})")
        return {"success": True, "deleted_task": target_item}
    else:
        logger.error(f"删除 Microsoft To Do 任务失败: {res.get('error')}")
        return {"success": False, "error": res.get("error")}

def evaluate_notice_against_existing_todos(notice_text: str, current_todos: list) -> dict:
    """利用 AI 对群通知与当前所有未完成待办进行比对与裁决"""
    now_str = get_now_gmt8_str()
    todos_summary = []
    for t in current_todos:
        todos_summary.append(
            f"- [ID: {t['id']}] 事项: {t.get('what')}, 起止时间: {t.get('when')}, 涉及对象: {t.get('who')}, 详情: {t.get('context')}"
        )
    todos_block = "\n".join(todos_summary) if todos_summary else "（暂无未完成待办）"

    prompt = f"""你是一名极其严谨的个人智能管家日程决策大脑。
当前标准北京时间：{now_str}

【当前正在执行的所有未完成待办清单】：
{todos_block}

【最新捕获的通知文本】：
\"\"\"{notice_text}\"\"\"

【你的任务与判定法则】：
1. 裁决新通知是否是一条【通知/待办事项】（日常闲聊、无实质任务内容的设 is_notice=false）。
2. 若是通知，仔细比对当前已有的未完成待办：
   - 如果通知是现有某个待办的更新、补充、时间改动、地点变更、推迟等，判定为 "update"，并在 updated_fields 中提供最新的 who, what, when, context，target_todo_id 指向被更新的待办 ID。
   - 如果通知与现有待办核心事件及起止时间完全一致，无任何新增信息，判定为 "duplicate"。
   - 如果是完全独立的全新事项，判定为 "new"。
3. 【起止时间完整性规范】：提取时间字段（when）时，必须尽可能完整写出开始与结束时间，如“2026年9月15日 14:00 - 16:00”或“即日起至2026年9月18日 12:00（截止）”。

【输出格式要求】：
请务必直接输出标准合法的纯 JSON 字典，严禁任何额外解释文字或 Markdown 标记：
{{
  "is_notice": true,
  "decision": "new" | "update" | "duplicate" | "ignore",
  "target_todo_id": "被更新或重复的待办ID，若是new或ignore则设为null",
  "task": {{
    "who": "涉及人员/主体",
    "what": "核心要做的事（简练扼要）",
    "when": "规范起止时间",
    "context": "地点、要求、链接等补充信息"
  }},
  "reason": "简短判定依据"
}}"""

    try:
        raw_res = ai_provider.call_ai(prompt, temperature=0.1)
        res = parse_json_object(raw_res)
        if not res or "decision" not in res:
            logger.warning(f"AI 决策返回非标准 JSON: {raw_res}")
            return {"is_notice": False, "decision": "ignore", "raw": raw_res}
        return res
    except Exception as e:
        logger.error(f"AI 通知裁决失败: {e}")
        return {"is_notice": False, "decision": "ignore", "error": str(e)}

def apply_notice_evaluation(eval_res: dict) -> dict:
    """根据 AI 裁决结果执行写入、更新或忽略操作"""
    decision = eval_res.get("decision", "ignore")
    task_info = eval_res.get("task") or {}

    if decision == "new":
        add_res = add_task_with_memory(
            who=task_info.get("who", ""),
            what=task_info.get("what", ""),
            when=task_info.get("when", ""),
            raw_context=task_info.get("context", "")
        )
        return {"action_taken": "created", "details": add_res}
    elif decision == "update":
        target_id = eval_res.get("target_todo_id")
        up_res = update_task_with_memory(
            todo_id_or_sig=target_id,
            who=task_info.get("who"),
            what=task_info.get("what"),
            when=task_info.get("when"),
            raw_context=task_info.get("context")
        )
        return {"action_taken": "updated", "details": up_res}
    elif decision == "duplicate":
        return {"action_taken": "ignored_duplicate"}
    else:
        return {"action_taken": "ignored_non_notice"}
