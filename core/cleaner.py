import re
import json

def strip_tool_leak(text: str) -> str:
    """彻底防止任何工具链与内部 JSON 数据结构泄露到 QQ 对话中"""
    if not text:
        return ""
    
    # 过滤模型可能追加的英文安全说明/拒答杂质
    text = re.sub(r'I cannot fulfill this request.*', '', text, flags=re.DOTALL | re.IGNORECASE).strip()

    # 如果包含工具链特征词
    tool_keywords = ['"action":', '"task_detail":', '"is_notice":', '"has_todo":', '"reply":']
    if any(k in text for k in tool_keywords) or (text.strip().startswith("{") and "}" in text):
        # 尝试提取其中的 reply 字段
        start = text.find("{")
        replies = []
        while start != -1:
            try:
                obj, idx = json.JSONDecoder().raw_decode(text[start:])
                if isinstance(obj, dict):
                    rep = obj.get("reply")
                    if rep and isinstance(rep, str) and rep.strip():
                        replies.append(rep.strip())
                start += idx
            except Exception:
                start += 1
            start = text.find("{", start)

        if replies:
            # 优先使用提取出来的第一条有效人类回复文本
            return replies[0]

        # 如果没有有效 reply（例如单纯的 action 或 task_detail），清除所有 JSON 结构
        cleaned = re.sub(r'\{[^{}]*\}', '', text, flags=re.DOTALL).strip()
        cleaned = re.sub(r'["\']?action["\']?\s*:\s*["\']?\w+["\']?,?', '', cleaned)
        cleaned = re.sub(r'["\']?task_detail["\']?\s*:\s*["\']?[^"\']*["\']?,?', '', cleaned)
        cleaned = cleaned.replace("{", "").replace("}", "").strip()
        return cleaned or "已为您收到并处理该指令。"

    return text

def clean_qq_markdown(text: str) -> str:
    """
    清洗 QQ 消息中无法正常渲染并导致视觉混乱的 Markdown 标记及模型思考标签：
    1. 移除思考标签 <think>...</think> 或 [Assistant]: 等
    2. 移除加粗/斜体 **text**, *text*, __text__
    3. 移除行内代码反引号 `text` 与代码块 ```
    4. 移除无序列表星号/减号 * / - 转换为清爽符号 •
    5. 移除水平分割线 --- 或 === 转换为优美分隔符
    6. 移除 Markdown 标题符 ###
    7. 移除 Markdown 超链接语法 [text](url) -> text
    8. 移除引用符号 >
    """
    if not text:
        return ""

    # 先过滤工具链与模型内部标签
    text = strip_tool_leak(text)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'\[Assistant\]:?', '', text, flags=re.IGNORECASE)

    lines = text.split("\n")
    cleaned_lines = []

    for line in lines:
        l = line.strip()
        if not l:
            cleaned_lines.append("")
            continue

        # 过滤单纯的水平分割线 --- 或 === 或 -------------------
        if re.match(r'^[-=_~*]{3,}$', l):
            cleaned_lines.append("──────────────────────")
            continue

        # 标题 # ## ### -> 直接保留文本
        l = re.sub(r'^#{1,6}\s*', '', l)

        # 移除引用符号 >
        l = re.sub(r'^>\s*', '', l)

        # 移除代码块标识
        l = re.sub(r'```[a-zA-Z]*', '', l)

        # 移除加粗与斜体 **text** -> text
        l = re.sub(r'\*\*(.*?)\*\*', r'\1', l)
        l = re.sub(r'__(.*?)__', r'\1', l)

        # 移除行内反引号 `text` -> text
        l = re.sub(r'`(.*?)`', r'\1', l)

        # 处理 Markdown 超链接 [标题](链接) -> 标题
        l = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1 (\2)', l)

        # 处理列表开头的 * 或 - -> •
        l = re.sub(r'^\s*[\*\-]\s+', '• ', l)

        # 移除单独遗留的 * 符号
        l = re.sub(r'(?<!\w)\*(?!\w)', '', l)

        cleaned_lines.append(l)

    res = "\n".join(cleaned_lines)
    # 消除连续3个以上换行
    res = re.sub(r'\n{3,}', '\n\n', res)
    return res.strip()

if __name__ == "__main__":
    sample = """{
"action": "chat",
"task_detail": "",
"reply": "已检查，请确认需要检查的 todo 具体内容或发送待办清单。"
}{
"action": "chat",
"task_detail": "",
"reply": "好的，请随时发送需要处理的清单或指令。"
}"""
    print("--- 清洗前 ---")
    print(sample)
    print("--- 清洗结果 ---")
    print(clean_qq_markdown(sample))
