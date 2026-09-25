import os
import json
import logging
import urllib.request
import urllib.error
import asyncio
import subprocess
import shlex
from typing import Callable
from typing import List, Dict, Any, Optional

from core import config
from core.cli_manager import ensure_cli

logger = logging.getLogger("AIProvider")

class AIProviderError(Exception):
    """Raised when an AI provider call fails."""
    pass

def _call_openai_compatible(messages: List[Dict[str, str]], temperature: Optional[float] = None, timeout: int = 60) -> str:
    """Execute a chat completion call against an OpenAI-compatible endpoint (e.g. DeepSeek, OpenAI)."""
    base_url = config.LLM_BASE_URL.rstrip("/")
    if not base_url.endswith("/chat/completions"):
        url = f"{base_url}/chat/completions"
    else:
        url = base_url

    temp = temperature if temperature is not None else config.LLM_TEMPERATURE
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temp
    }
    data = json.dumps(payload).encode("utf-8")
    
    headers = {
        "Content-Type": "application/json"
    }
    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                res_json = json.loads(body)
            except json.JSONDecodeError as exc:
                raise AIProviderError("LLM endpoint returned invalid JSON") from exc
            choices = res_json.get("choices", [])
            if not choices:
                raise AIProviderError("LLM endpoint returned no choices")
            content = choices[0].get("message", {}).get("content", "")
            if not isinstance(content, str):
                raise AIProviderError("LLM response content must be a string")
            return content.strip()
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        logger.error(f"OpenAI-compatible HTTP {e.code} error from {url}: {err_body}")
        raise AIProviderError(f"HTTP {e.code}: {err_body}") from e
    except urllib.error.URLError as e:
        logger.error(f"OpenAI-compatible connection failed to {url}: {e.reason}")
        raise AIProviderError(f"Connection failed: {e.reason}") from e
    except Exception as e:
        logger.error(f"Unexpected error calling LLM {url}: {e}")
        raise AIProviderError(str(e)) from e


def _openai_request(messages, temperature=None, timeout=60, tools=None):
    base_url = config.LLM_BASE_URL.rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": config.LLM_TEMPERATURE if temperature is None else temperature,
    }
    if tools:
        payload["tools"] = tools
    headers = {"Content-Type": "application/json"}
    if config.LLM_API_KEY:
        headers["Authorization"] = f"******"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AIProviderError("LLM endpoint returned invalid JSON") from exc
    if not result.get("choices"):
        raise AIProviderError("LLM endpoint returned no choices")
    return result


def call_chat_completions(messages: List[Dict[str, str]], temperature: Optional[float] = None, timeout: int = 60) -> str:
    """Call the configured HTTP or local CLI provider."""
    if config.AI_PROVIDER in {"openai", "deepseek", "default"}:
        return _call_openai_compatible(messages, temperature=temperature, timeout=timeout)
    prompt = "\n\n".join(f"[{m.get('role', 'user').upper()}]\n{m.get('content', '')}" for m in messages)
    return _call_cli(prompt, timeout=timeout)


def _cli_command(provider: str, executable: str, prompt: str) -> List[str]:
    custom = os.getenv(f"{provider.upper()}_ARGS")
    if custom:
        return [executable] + [part.replace("{prompt}", prompt) for part in shlex.split(custom)]
    if provider == "agy":
        model = getattr(config, "AGY_MODEL", "gemini-3.8-flash-low") or "gemini-3.8-flash-low"
        return [executable, "-p", prompt, "--model", model, "--output-format", "json"]
    if provider == "codex":
        return [executable, "exec", "--json", prompt]
    if provider == "opencode":
        return [executable, "run", "--format", "json", prompt]
    return [executable, "-p", prompt, "--output-format", "json"]


def _extract_cli_text(stdout: str) -> str:
    def find_text(value):
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for key in ("response", "output", "text", "message", "content"):
                found = find_text(value.get(key))
                if found:
                    return found
            for child in value.values():
                if isinstance(child, str) and child in {"type", "item.completed", "tool_call", "final"}:
                    continue
                found = find_text(child)
                if found:
                    return found
        if isinstance(value, list):
            for child in reversed(value):
                found = find_text(child)
                if found:
                    return found
        return ""

    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = find_text(value)
        if found:
            return found
    return stdout.strip()


def _call_cli(prompt: str, timeout: int = 300) -> str:
    raw_stdout = _call_cli_raw(prompt, timeout)
    response = _extract_cli_text(raw_stdout)
    if not response:
        raise AIProviderError(f"{config.AI_PROVIDER} CLI returned an empty response")
    return response


def _call_cli_raw(prompt: str, timeout: int = 300) -> str:
    executable = ensure_cli(config.AI_PROVIDER)
    command = _cli_command(config.AI_PROVIDER, executable, prompt)
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise AIProviderError(
            f"{config.AI_PROVIDER} CLI failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )
    return result.stdout


def call_agent(
    messages: List[Dict[str, str]],
    tools: List[Dict[str, Any]],
    tool_executor: Callable[[str, dict], str],
    max_turns: int = 3,
    timeout: int = 300,
) -> str:
    """Run native HTTP tools or a JSON tool protocol over local CLIs."""
    if config.AI_PROVIDER in {"openai", "deepseek", "default"}:
        return _call_openai_compatible_with_tools(messages, tools, tool_executor, max_turns)

    tool_text = json.dumps(tools, ensure_ascii=False)
    transcript = list(messages)
    for _ in range(max_turns):
        prompt = (
            "\n\n".join(f"[{m.get('role', 'user').upper()}]\n{m.get('content', '')}" for m in transcript)
            + "\n\n[TOOLS]\n"
            + tool_text
            + '\n\nReturn either {"type":"tool_call","name":"...","arguments":{...}} '
              'or {"type":"final","content":"..."} as one JSON object.'
        )
        raw = _call_cli_raw(prompt, timeout=timeout)
        parsed = _parse_json(raw)
        if parsed.get("type") == "tool_call":
            name = parsed.get("name", "")
            arguments = parsed.get("arguments") if isinstance(parsed.get("arguments"), dict) else {}
            output = tool_executor(name, arguments)
            transcript.extend([
                {"role": "assistant", "content": raw},
                {"role": "tool", "content": output},
            ])
            continue
        return parsed.get("content", raw) if isinstance(parsed.get("content", raw), str) else raw
    raise AIProviderError("Agent tool loop exhausted without a final response")


def _parse_json(text: str) -> dict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        if start >= 0:
            try:
                value, _ = json.JSONDecoder().raw_decode(text[start:])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def _call_openai_compatible_with_tools(messages, tools, tool_executor, max_turns):
    history = list(messages)
    for _ in range(max_turns):
        body = _openai_request(history, tools=tools)
        assistant = body["choices"][0]["message"]
        calls = assistant.get("tool_calls") or []
        if not calls:
            return assistant.get("content", "").strip()
        history.append(assistant)
        for call in calls:
            function = call.get("function", {})
            try:
                arguments = json.loads(function.get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {}
            history.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": tool_executor(function.get("name", ""), arguments),
            })
    raise AIProviderError("Agent tool loop exhausted without a final response")

def call_ai(prompt: str, system_prompt: Optional[str] = None, temperature: Optional[float] = None, timeout: int = 60) -> str:
    """Convenience helper to call AI with prompt and optional system prompt."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return call_chat_completions(messages, temperature=temperature, timeout=timeout)

async def call_ai_async(prompt: str, system_prompt: Optional[str] = None, temperature: Optional[float] = None, timeout: int = 60) -> str:
    """Asynchronous wrapper for call_ai."""
    return await asyncio.to_thread(call_ai, prompt, system_prompt, temperature, timeout)

async def call_chat_completions_async(messages: List[Dict[str, str]], temperature: Optional[float] = None, timeout: int = 60) -> str:
    """Asynchronous wrapper for call_chat_completions."""
    return await asyncio.to_thread(call_chat_completions, messages, temperature, timeout)
