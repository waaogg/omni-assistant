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
from datetime import datetime
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
    load_memory
)
from core.cleaner import clean_qq_markdown, strip_tool_leak
from core.storage import load_json, save_json

logger = logging.getLogger("QQAdapter")

last_seen_message_ids: Dict[int, int] = {}
ADMIN_CHAT_HISTORY: List[Dict[str, str]] = []

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
    except Exception as e:
        logger.error(f"保存群消息历史失败: {e}")

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
    except Exception as e:
        logger.error(f"调用 NapCat API {action} 失败: {e}")
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
    logger.info(f"QQ Agent 调用工具: {name}, 参数: {args}")
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
    """针对管理员私聊消息的 ReAct 闭环智能体"""
    global ADMIN_CHAT_HISTORY
    now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")

    system_prompt = f"""你是管理员的专属全天候智能管家助理（贴心、敏锐、具备多种系统管理工具）。
当前系统时间基准：{now_str}。

【核心行为准则】：
1. 【真实工具执行，严禁空口臆测】：
   - 你拥有管理 Microsoft To Do 待办清单与查阅通知群消息的各项真实工具。
   - 涉及查看待办、检查重复、删除待办、修改待办、新增待办，或查看群最新动态，必须首先调用相应的真实工具！
   - 严禁凭空编造待办列表或虚构执行结果。
2. 【表达自由自然，彻底摒弃死板模板】：
   - 不要使用任何刻板僵化的固定套话。根据对话场景，以贴身私人秘书的口吻，用流畅清晰、自然得体的人类语言回答。
3. 【纯文本排版】：
   - QQ 不支持 Markdown 富文本渲染，严禁输出任何加粗（**）、标题（#）、行内反引号（`）或代码块。请使用纯文本配合 emoji 或清晰标点排版。"""

    messages = [{"role": "system", "content": system_prompt}]
    for turn in ADMIN_CHAT_HISTORY[-10:]:
        messages.append(turn)
    messages.append({"role": "user", "content": user_text})

    if config.AI_PROVIDER in ("openai", "deepseek", "default"):
        # Standard OpenAI format with function calling
        for _ in range(3):
            base_url = config.LLM_BASE_URL.rstrip("/")
            url = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url
            payload = {
                "model": config.LLM_MODEL,
                "messages": messages,
                "tools": AGENT_TOOLS,
                "temperature": config.LLM_TEMPERATURE
            }
            headers = {"Content-Type": "application/json"}
            if config.LLM_API_KEY:
                headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"
            
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=40) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    assistant_msg = data["choices"][0]["message"]
            except Exception as e:
                logger.error(f"智能体交互异常: {e}")
                return "处理时遇到了一点网络波动，请稍后再试一下。"

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                final_content = assistant_msg.get("content", "")
                cleaned_reply = clean_qq_markdown(strip_tool_leak(final_content))
                ADMIN_CHAT_HISTORY.append({"role": "user", "content": user_text})
                ADMIN_CHAT_HISTORY.append({"role": "assistant", "content": cleaned_reply})
                if len(ADMIN_CHAT_HISTORY) > 20:
                    ADMIN_CHAT_HISTORY = ADMIN_CHAT_HISTORY[-20:]
                return cleaned_reply

            messages.append(assistant_msg)
            for tc in tool_calls:
                call_id = tc.get("id")
                fn_name = tc.get("function", {}).get("name")
                try:
                    fn_args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                except Exception:
                    fn_args = {}
                tool_output = execute_agent_tool(fn_name, fn_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fn_name,
                    "content": tool_output
                })
        return "已为您执行了相关操作，请告诉我是否还需要进一步调整。"

    try:
        reply = ai_provider.call_agent(messages, AGENT_TOOLS, execute_agent_tool)
    except Exception as exc:
        logger.error(f"CLI 智能体交互异常: {exc}")
        return "处理时遇到了一点波动，请确认 CLI 已安装并完成登录后再试。"
    cleaned = clean_qq_markdown(strip_tool_leak(reply))
    ADMIN_CHAT_HISTORY.append({"role": "user", "content": user_text})
    ADMIN_CHAT_HISTORY.append({"role": "assistant", "content": cleaned})
    return cleaned

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

def process_message(user_id: int, group_id: int, raw_text: str, is_group: bool, message_id: int, sender_name: str = ""):
    text = raw_text.strip()

    # 1. 目标群消息处理：语义查重、修正合并与动态提醒
    if is_group and group_id in config.TARGET_GROUP_IDS:
        last_seen = last_seen_message_ids.get(group_id, 0)
        if message_id > last_seen:
            last_seen_message_ids[group_id] = message_id

        if text:
            save_group_message({"message_id": message_id, "group_id": group_id, "sender": sender_name or str(user_id), "text": text})

        logger.info(f"目标群 [{group_id}] 收到新消息，交由 AI 语义分析: {text[:40]}...")
        current_todos = get_all_active_tasks()
        eval_res = evaluate_notice_against_existing_todos(text, current_todos)
        logger.info(f"群通知语义裁决结果: {eval_res}")

        decision = eval_res.get("decision", "ignore")
        if decision in ["ignore", "duplicate"]:
            logger.info(f"群消息裁决为 {decision}，静默跳过。")
            return

        if decision in ["update", "new"]:
            sync_res = apply_notice_evaluation(eval_res)
            action_taken = sync_res.get("action_taken")
            if action_taken in ["created", "updated"]:
                task_info = eval_res.get("task", {})
                notice_msg = generate_natural_notice_report(action_taken, task_info, text)
                send_private_msg(config.ADMIN_QQ, notice_msg)
                logger.info(f"群待办 {action_taken} 已由 AI 自主组织语言汇报管理员。")
        return

    # 2. 管理员私聊消息处理：ReAct 智能体多轮闭环
    if not is_group and user_id == config.ADMIN_QQ:
        logger.info(f"收到管理员私聊消息: '{text}'，进入智能体处理...")
        reply = run_admin_agent(text)
        send_private_msg(config.ADMIN_QQ, reply)

async def periodic_check_task():
    logger.info("QQ 适配器：30 分钟定时断流防漏巡检任务已启动...")
    while True:
        try:
            await asyncio.sleep(1800)
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

                new_lines = []
                for m in new_messages:
                    c = m.get("raw_message", "").strip()
                    s = m.get("sender", {}).get("card") or m.get("sender", {}).get("nickname") or m.get("user_id")
                    if c:
                        new_lines.append(f"{s}: {c}")
                if not new_lines:
                    continue

                combined = "\n".join(new_lines)
                current_todos = get_all_active_tasks()
                eval_res = evaluate_notice_against_existing_todos(combined, current_todos)
                decision = eval_res.get("decision", "ignore")
                if decision in ["update", "new"]:
                    sync_res = apply_notice_evaluation(eval_res)
                    action_taken = sync_res.get("action_taken")
                    if action_taken in ["created", "updated"]:
                        task_info = eval_res.get("task", {})
                        notice_msg = generate_natural_notice_report(action_taken, task_info, combined)
                        send_private_msg(config.ADMIN_QQ, notice_msg)
        except Exception as e:
            logger.error(f"QQ 适配器定时巡检异常: {e}")

async def ws_listener():
    import websockets
    ws_url = f"{config.NAPCAT_WS_URL}?access_token={config.NAPCAT_TOKEN}" if config.NAPCAT_TOKEN else config.NAPCAT_WS_URL
    logger.info(f"正在连接 NapCat WebSocket 服务: {ws_url}")

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
        except (websockets.ConnectionClosed, OSError) as e:
            logger.warning(f"QQ 适配器 WebSocket 断开连接 ({e})，5 秒后尝试重连...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"QQ 适配器发生未捕获异常: {e}")
            await asyncio.sleep(5)

async def run_qq_adapter():
    """QQ 适配器总入口（支持开关自省）"""
    if not config.ENABLE_QQ:
        logger.info("QQ 通道已禁用 (ENABLE_QQ=false)，跳过启动。")
        return

    logger.info("🚀 QQ 通道适配器正在启动...")
    logger.info(f"管理员 QQ: {config.ADMIN_QQ}")
    logger.info(f"监听群组: {config.TARGET_GROUP_IDS}")
    logger.info(f"NapCat API: {config.NAPCAT_HTTP_URL}")

    await asyncio.gather(
        ws_listener(),
        periodic_check_task()
    )

if __name__ == "__main__":
    config.setup_logging()
    asyncio.run(run_qq_adapter())
