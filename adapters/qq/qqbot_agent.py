#!/usr/bin/env python3
"""
QQ Bot 通道适配器（基于 NapCat OneBot v11 协议）
支持：
1. 班级/工作群通知智能监听与语义查重更新
2. 管理员私聊专属 ReAct 闭环智能体
3. 纯净排版过滤与严格群内静默
4. 完全参数化配置，支持按需启用/禁用
"""

import asyncio
import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from core import config
from core import ai_provider
from core.todo_manager import (
    evaluate_notice_against_existing_todos,
    apply_notice_evaluation,
    get_all_active_tasks,
    delete_task_with_memory,
    add_task_with_memory,
    update_task_with_memory,
    load_memory,
    evaluate_notice_batch,
)
from core.cleaner import clean_qq_markdown, strip_tool_leak
from core.storage import load_json, save_json
from core.database import Database
from core.remote_todo import MicrosoftTodoRemote
from core.task_service import TaskService
from core.channel_agent import ChannelAgent

logger = logging.getLogger("QQAdapter")

last_seen_message_ids: Dict[int, int] = {}
ADMIN_CHAT_HISTORY: List[Dict[str, str]] = []
DATABASE = Database(config.DATABASE_FILE)
TASK_SERVICE = TaskService(DATABASE, MicrosoftTodoRemote() if config.ENABLE_MS_TODO else None)
CHANNEL_AGENT = ChannelAgent(DATABASE, TASK_SERVICE)

def save_group_message(record: dict):
    hist_file = config.HISTORY_FILE
    records = load_json(hist_file, [])
    if not isinstance(records, list):
        records = []
    records.append(record)
    if len(records) > 300:
        records = records[-300:]
    try:
        save_json(hist_file, records)
    except Exception as exc:
        logger.error("保存群消息历史失败: %s", type(exc).__name__)

def load_local_group_messages(limit: int = 80) -> list:
    hist_file = config.HISTORY_FILE
    records = load_json(hist_file, [])
    if not isinstance(records, list):
        return []
    return [f"{r.get('sender')}: {r.get('text')}" for r in records[-limit:]]

def call_napcat_api(action: str, params: dict) -> dict:
    url = f"{config.NAPCAT_HTTP_URL}/{action}"
    headers = {"Content-Type": "application/json"}
    if config.NAPCAT_TOKEN:
        headers["Authorization"] = f"Bearer {config.NAPCAT_TOKEN}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("调用 NapCat API %s 失败: %s", action, type(exc).__name__)
        return {}

def send_private_msg(user_id: int, text: str):
    """私聊发送前进行纯净排版过滤，彻底杜绝 Markdown 标识外露"""
    cleaned_text = clean_qq_markdown(text)
    if not cleaned_text.strip():
        return
    call_napcat_api("send_private_msg", {"user_id": user_id, "message": cleaned_text})

def get_all_group_materials(group_id: int, limit: int = 60) -> list:
    materials = []
    notice_res = call_napcat_api("_get_group_notice", {"group_id": group_id})
    notices = notice_res.get("data", [])
    if notices:
        materials.append("【群官方公告】:")
        for n in notices:
            msg_obj = n.get("message", {})
            text = msg_obj.get("text", "") if isinstance(msg_obj, dict) else str(msg_obj)
            if text:
                materials.append(f"- 公告: {text}")

    essence_res = call_napcat_api("get_essence_msg_list", {"group_id": group_id})
    essences = essence_res.get("data", [])
    if essences:
        materials.append("【群精华通知消息】:")
        for e in essences:
            nick = e.get("sender_nick", "通知人")
            for c in e.get("content", []):
                t = c.get("data", {}).get("text", "")
                if t:
                    materials.append(f"- [{nick}]: {t}")

    local_msgs = load_local_group_messages(limit)
    if local_msgs:
        materials.append("【群内近期讨论记录】:")
        materials.extend(local_msgs)

    return materials

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_todos",
            "description": "实时获取 Microsoft To Do 中的所有当前未完成待办事项，包括事项ID、标题、具体时间、涉及对象及详细备注。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_todos",
            "description": "从 Microsoft To Do 和本地记忆中删除一个或多个指定的待办事项。用于清理重复条目、失效条目或按管理员指令删除待办。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要删除的待办事项 ID 列表"
                    },
                    "reason": {
                        "type": "string",
                        "description": "删除原因或说明"
                    }
                },
                "required": ["task_ids"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": "向 Microsoft To Do 添加一条新待办事项。系统会自动内嵌原生截止时间并默认提前15分钟设置提醒闹钟。",
            "parameters": {
                "type": "object",
                "properties": {
                    "what": {"type": "string", "description": "待办事项核心动作（干什么）"},
                    "when": {"type": "string", "description": "时间节点或起止时间（如：9月13日 18:00 - 20:00）"},
                    "who": {"type": "string", "description": "涉及谁（默认为'我'或具体人员）"},
                    "context": {"type": "string", "description": "地点、要求或补充信息"}
                },
                "required": ["what"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_todo",
            "description": "更新 Microsoft To Do 中已有的某个待办事项（修改时间、地点、标题、涉及人员或要求）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "待办事项 ID"},
                    "what": {"type": "string", "description": "修改后的事项内容"},
                    "when": {"type": "string", "description": "修改后的时间节点"},
                    "who": {"type": "string", "description": "修改后的涉及对象"},
                    "context": {"type": "string", "description": "修改后的地点或补充信息"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_group_materials",
            "description": "获取群聊最新的通知公告、精华消息及讨论记录，用于核实群内通知细节或做群动态总结。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "拉取的聊天条数，默认 50"}
                }
            }
        }
    }
]

def execute_agent_tool(name: str, args: dict) -> str:
    logger.info("QQ Agent 调用工具: %s (参数字段=%s)", name, sorted(args))
    if name == "list_todos":
        tasks = get_all_active_tasks()
        return json.dumps(tasks, ensure_ascii=False)
    elif name == "delete_todos":
        task_ids = args.get("task_ids", [])
        deleted = []
        for tid in task_ids:
            r = delete_task_with_memory(tid)
            if r.get("success"):
                deleted.append(tid)
        return json.dumps({"success": True, "deleted_count": len(deleted), "deleted_ids": deleted}, ensure_ascii=False)
    elif name == "add_todo":
        r = add_task_with_memory(
            args.get("who", "我"),
            args.get("what", ""),
            args.get("when", ""),
            args.get("context", "")
        )
        return json.dumps(r, ensure_ascii=False)
    elif name == "update_todo":
        r = update_task_with_memory(
            args.get("task_id"),
            args.get("who"),
            args.get("what"),
            args.get("when"),
            args.get("context")
        )
        return json.dumps(r, ensure_ascii=False)
    elif name == "get_group_materials":
        limit = args.get("limit", 50)
        gid = config.TARGET_GROUP_IDS[0] if config.TARGET_GROUP_IDS else 0
        m = get_all_group_materials(gid, limit)
        return json.dumps(m, ensure_ascii=False)
    return json.dumps({"error": f"Unknown tool {name}"})

def run_admin_agent(user_text: str) -> str:
    """Run the shared, persistent task and knowledge agent for the administrator."""
    try:
        reply = CHANNEL_AGENT.run("qq", config.ADMIN_QQ, user_text)
        return clean_qq_markdown(strip_tool_leak(reply))
    except Exception as exc:
        logger.error("统一智能体交互失败: %s", type(exc).__name__)
        return "处理请求失败，操作没有被报告为成功；请稍后重试或检查运行面板。"

def generate_natural_notice_report(action_type: str, item_info: dict, raw_group_msg: str) -> str:
    action_desc = "【更新/修正了已有待办】" if action_type == "updated" else "【新增了待办事项】"
    prompt = f"""你是管理员的专属全天候智能管家助理。
你在通知群内捕捉并处理了一则事项通知，处理结果为：{action_desc}，并已实时同步到 Microsoft To Do。
【事项详情】：
- 核心内容：{item_info.get('what')}
- 时间节点：{item_info.get('when')}
- 涉及人员：{item_info.get('who')}
- 补充说明/地点：{item_info.get('context')}
【群通知原文】：
"{raw_group_msg}"

请以专属私人管家助理的口吻，给管理员组织一条自然清晰、得体的私聊汇报提醒。
要求：
1. 语言自然生动、清晰明确，说明你在群里看到了什么通知，帮他进行了什么更新或录入，核心时间地点交代明白。
2. 自主组织语言与排版，无需使用刻板的套话或机械化挖坑填词。
3. 严禁使用任何在 QQ 渲染异常的 Markdown 语法（严禁**加粗、严禁#标题、严禁反引号）。"""

    res = ai_provider.call_ai(prompt, temperature=0.3)
    return clean_qq_markdown(strip_tool_leak(res))


def process_queued_notice(queued_ids: int | list[int], text: str) -> None:
    ids = [queued_ids] if isinstance(queued_ids, int) else queued_ids
    primary_id = ids[0]
    logger.info("处理已持久化的群通知批次 (count=%s)", len(ids))
    eval_res = evaluate_notice_batch(
        text,
        TASK_SERVICE.db.list_tasks("open"),
        DATABASE.get_preference("identity_profile", {}),
    )
    logger.info(
        "群通知语义裁决完成: action=%s, tasks=%s",
        eval_res.get("action"), len(eval_res.get("tasks", [])),
    )
    if eval_res.get("error"):
        for queued_id in ids:
            DATABASE.retry_message(
                queued_id, "notice_analysis_failed",
                (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(timespec="seconds"),
                config.MAX_MESSAGE_ATTEMPTS,
            )
        return
    decision = eval_res.get("action", "ignore")
    if decision in {"ignore", "duplicate"}:
        for queued_id in ids:
            DATABASE.finish_message(queued_id)
        return
    results = []
    for task_info in eval_res.get("tasks", []):
        if decision == "update" and task_info.get("task_id"):
            changes = {
                "title": task_info.get("what"),
                "assignee": task_info.get("who"),
                "original_time_text": task_info.get("when"),
                "context": task_info.get("context"),
                "relevance": task_info.get("relevance"),
                "quadrant": task_info.get("quadrant"),
            }
            result = TASK_SERVICE.update(
                str(task_info["task_id"]),
                {key: value for key, value in changes.items() if value is not None},
            )
        else:
            result = TASK_SERVICE.propose(primary_id, task_info)
        results.append(result)
        if result.get("success"):
            state = result.get("state")
            if state in {"created", "updated", "recovered"}:
                try:
                    notice_msg = generate_natural_notice_report(state, task_info, text)
                except Exception:
                    notice_msg = "待办已按实际执行结果完成同步，请在任务列表中查看详情。"
            elif state == "needs_confirmation":
                notice_msg = "检测到一条信息不完整的候选事项，已放入待确认收件箱。"
            else:
                continue
            send_private_msg(config.ADMIN_QQ, notice_msg)
    if results and all(result.get("success") for result in results):
        for queued_id in ids:
            DATABASE.finish_message(queued_id)
    elif not results:
        for queued_id in ids:
            DATABASE.finish_message(queued_id)
    else:
        for queued_id in ids:
            DATABASE.retry_message(
                queued_id, "one_or_more_task_operations_failed",
                (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(timespec="seconds"),
                config.MAX_MESSAGE_ATTEMPTS,
            )
        send_private_msg(config.ADMIN_QQ, "待办同步失败，系统稍后会重试；本次未报告为成功。")

def process_message(user_id: int, group_id: int, raw_text: str, is_group: bool, message_id: int, sender_name: str = ""):
    text = raw_text.strip()

    # 1. 目标群消息处理：语义查重、修正合并与动态提醒
    if is_group and group_id in config.TARGET_GROUP_IDS:
        queued_id, inserted = DATABASE.enqueue_message(
            channel="qq",
            external_id=str(message_id),
            conversation_id=group_id,
            sender_id=user_id,
            body=text,
        )
        if not inserted:
            logger.info("QQ 消息已入队或已处理: %s", message_id)
            return
        last_seen = last_seen_message_ids.get(group_id, 0)
        if message_id > last_seen:
            last_seen_message_ids[group_id] = message_id

        logger.info("QQ 消息已进入碎片合并窗口 (message_id=%s)", message_id)
        return

    # 2. 管理员私聊消息处理：ReAct 智能体多轮闭环
    if not is_group and user_id == config.ADMIN_QQ:
        logger.info("收到管理员私聊消息，进入智能体处理 (message_id=%s)", message_id)
        reply = run_admin_agent(text)
        send_private_msg(config.ADMIN_QQ, reply)

async def periodic_check_task():
    logger.info("QQ 适配器：30 分钟定时断流防漏巡检任务已启动...")
    last_history_check = 0.0
    while True:
        try:
            pending_rows = DATABASE.claim_messages(limit=50, channel="qq")
            batches: dict[str, list[dict]] = {}
            for pending in pending_rows:
                batches.setdefault(pending["conversation_key"], []).append(pending)
            for batch in batches.values():
                combined = "\n".join(row["body"] for row in batch if row["body"].strip())
                batch_ids = [int(row["id"]) for row in batch]
                try:
                    await asyncio.to_thread(process_queued_notice, batch_ids, combined)
                except Exception as exc:
                    retry_at = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(timespec="seconds")
                    for queued_id in batch_ids:
                        DATABASE.retry_message(
                            queued_id, type(exc).__name__, retry_at, config.MAX_MESSAGE_ATTEMPTS
                        )
                    logger.error("通知批次处理失败，将重试: %s", type(exc).__name__)
            for notification in DATABASE.claim_notifications():
                payload = notification["payload"]
                if notification["kind"] == "daily_digest":
                    text = (
                        f"今日任务 {len(payload.get('today', []))} 项，"
                        f"未来三天 {len(payload.get('soon', []))} 项，"
                        f"逾期 {len(payload.get('overdue', []))} 项。"
                    )
                elif notification["kind"] == "weekly_review":
                    text = (
                        f"本周完成 {len(payload.get('completed', []))} 项，"
                        f"仍有 {len(payload.get('remaining', []))} 项待处理。"
                    )
                else:
                    text = f"有 {len(payload.get('tasks', []))} 项任务临近截止，请及时查看。"
                result = call_napcat_api("send_private_msg", {"user_id": config.ADMIN_QQ, "message": text})
                delivered = result.get("status") == "ok" or result.get("retcode") == 0
                DATABASE.finish_notification(notification["id"], delivered)
            now = asyncio.get_running_loop().time()
            if now - last_history_check < 1800:
                await asyncio.sleep(max(1, config.MESSAGE_BATCH_WINDOW_SECONDS))
                continue
            last_history_check = now
            if not config.TARGET_GROUP_IDS:
                continue
            for gid in config.TARGET_GROUP_IDS:
                res = call_napcat_api("get_group_msg_history", {"group_id": gid, "count": 20})
                messages = res.get("data", {}).get("messages", [])
                if not messages:
                    continue

                last_seen = last_seen_message_ids.get(gid, 0)
                current_max_id = max([m.get("message_id", 0) for m in messages])
                if last_seen == 0:
                    last_seen_message_ids[gid] = current_max_id
                    continue

                new_messages = [m for m in messages if m.get("message_id", 0) > last_seen]
                if not new_messages:
                    continue
                last_seen_message_ids[gid] = current_max_id

                for m in new_messages:
                    c = m.get("raw_message", "").strip()
                    s = m.get("sender", {}).get("card") or m.get("sender", {}).get("nickname") or m.get("user_id")
                    if c:
                        await asyncio.to_thread(
                            process_message,
                            m.get("user_id", 0),
                            gid,
                            c,
                            True,
                            m.get("message_id", 0),
                            str(s),
                        )
        except Exception as exc:
            logger.error("QQ 适配器定时巡检异常: %s", type(exc).__name__)

async def ws_listener():
    import websockets
    ws_url = f"{config.NAPCAT_WS_URL}?access_token={config.NAPCAT_TOKEN}" if config.NAPCAT_TOKEN else config.NAPCAT_WS_URL
    logger.info("正在连接已配置的 NapCat WebSocket 服务")

    while True:
        try:
            async with websockets.connect(ws_url) as websocket:
                logger.info("QQ 适配器 WebSocket 连接成功！开始实时监听...")
                async for message in websocket:
                    data = json.loads(message)
                    post_type = data.get("post_type")

                    if post_type == "message":
                        message_type = data.get("message_type")
                        user_id = data.get("user_id")
                        group_id = data.get("group_id")
                        raw_message = data.get("raw_message", "")
                        message_id = data.get("message_id", 0)
                        is_group = (message_type == "group")
                        sender_name = data.get("sender", {}).get("card") or data.get("sender", {}).get("nickname") or ""

                        asyncio.create_task(
                            asyncio.to_thread(
                                process_message,
                                user_id,
                                group_id,
                                raw_message,
                                is_group,
                                message_id,
                                sender_name
                            )
                        )
        except (websockets.ConnectionClosed, OSError):
            logger.warning("QQ 适配器 WebSocket 断开连接，5 秒后尝试重连...")
            await asyncio.sleep(5)
        except Exception as exc:
            logger.error("QQ 适配器发生未捕获异常: %s", type(exc).__name__)
            await asyncio.sleep(5)

async def run_qq_adapter():
    """QQ 适配器总入口（支持开关自省）"""
    if not config.ENABLE_QQ:
        logger.info("QQ 通道已禁用 (ENABLE_QQ=false)，跳过启动。")
        return

    logger.info("🚀 QQ 通道适配器正在启动...")
    logger.info("管理员账号已配置: %s", bool(config.ADMIN_QQ))
    logger.info("监听群组数量: %s", len(config.TARGET_GROUP_IDS))
    logger.info("NapCat API 已配置: %s", bool(config.NAPCAT_HTTP_URL))

    await asyncio.gather(
        ws_listener(),
        periodic_check_task()
    )

if __name__ == "__main__":
    config.setup_logging()
    asyncio.run(run_qq_adapter())
