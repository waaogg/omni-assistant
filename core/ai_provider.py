import os
import json
import logging
import subprocess
import urllib.request
import urllib.error
import asyncio
from typing import List, Dict, Any, Optional

from core import config

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
            res_json = json.loads(body)
            choices = res_json.get("choices", [])
            if not choices:
                raise AIProviderError(f"No choices returned from LLM endpoint: {body}")
            content = choices[0].get("message", {}).get("content", "")
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

def _call_antigravity_cli(prompt: str, timeout: int = 300) -> str:
    """Execute inference via Google Antigravity CLI (agy) subprocess."""
    bin_path = config.AGY_BIN_PATH or "agy"
    model = config.AGY_MODEL or "gemini-3.8-flash-low"
    
    args = [
        bin_path,
        "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "--dangerously-skip-permissions"
    ]
    
    try:
        res = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False
        )
        if res.returncode != 0 and not res.stdout.strip():
            raise AIProviderError(f"agy CLI failed (code {res.returncode}): {res.stderr.strip()}")
        
        raw_stdout = res.stdout.strip()
        try:
            parsed = json.loads(raw_stdout)
            if isinstance(parsed, dict) and "response" in parsed:
                return str(parsed["response"]).strip()
        except Exception:
            pass
        return raw_stdout
    except subprocess.TimeoutExpired as e:
        raise AIProviderError(f"agy CLI timed out after {timeout}s") from e
    except FileNotFoundError as e:
        raise AIProviderError(f"agy CLI binary not found at '{bin_path}'. Please verify AGY_BIN_PATH.") from e
    except Exception as e:
        raise AIProviderError(f"Failed to execute agy CLI: {e}") from e

def call_chat_completions(messages: List[Dict[str, str]], temperature: Optional[float] = None, timeout: int = 60) -> str:
    """Unified entrypoint for chat completions across configured AI provider."""
    provider = config.AI_PROVIDER
    if provider == "agy":
        # Format conversation messages into a single prompt for agy CLI
        formatted_prompt = ""
        for m in messages:
            role = m.get("role", "user").upper()
            content = m.get("content", "")
            formatted_prompt += f"[{role}]:\n{content}\n\n"
        return _call_antigravity_cli(formatted_prompt.strip(), timeout=300)
    elif provider in ("openai", "deepseek", "default"):
        return _call_openai_compatible(messages, temperature=temperature, timeout=timeout)
    else:
        raise AIProviderError(f"Unsupported AI_PROVIDER '{provider}'. Choose 'openai' or 'agy'.")

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
