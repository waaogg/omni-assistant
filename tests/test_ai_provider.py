#!/usr/bin/env python3
"""Test the OpenAI-compatible AI provider contract."""

import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import config, ai_provider

def test_default_config():
    assert config.AI_PROVIDER == "openai", f"Expected default openai, got {config.AI_PROVIDER}"
    assert "deepseek" in config.LLM_BASE_URL.lower() or "v1" in config.LLM_BASE_URL, "Default base URL should point to DeepSeek"
    assert config.LLM_MODEL == "deepseek-chat"
    print("✅ test_default_config passed.")

def test_openai_compatible_call_structure():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"choices": [{"message": {"content": "Hello from DeepSeek Mock"}}]}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        reply = ai_provider.call_ai("Test message", system_prompt="Test system")
        assert reply == "Hello from DeepSeek Mock"

        # Verify call args
        args, kwargs = mock_urlopen.call_args
        req = args[0]
        assert req.get_method() == "POST"
        assert req.get_header("Content-type") == "application/json"
        assert "chat/completions" in req.full_url
    print("✅ test_openai_compatible_call_structure passed.")

def test_node_ai_provider():
    import subprocess
    res = subprocess.run(
        ["node", "-e", """
        const ai = require('./core/ai_provider.js');
        if (typeof ai.executeAI !== 'function') process.exit(1);
        if (ai.AI_PROVIDER !== 'openai') process.exit(2);
        if (ai.LLM_MODEL !== 'deepseek-chat') process.exit(3);
        console.log('Node AI Module OK');
        """],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True
    )
    assert res.returncode == 0, f"Node AI provider test failed: {res.stderr}"
    assert "Node AI Module OK" in res.stdout
    print("✅ test_node_ai_provider passed.")


def test_cli_provider_parses_json_response():
    with patch.object(config, "AI_PROVIDER", "codex"), \
         patch("core.ai_provider.ensure_cli", return_value="codex"), \
         patch("core.ai_provider.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"message","text":"CLI response"}\n',
            stderr="",
        )
        assert ai_provider.call_ai("hello") == "CLI response"
        assert run.call_args.args[0][0] == "codex"


def test_cli_provider_parses_nested_jsonl_event():
    with patch.object(config, "AI_PROVIDER", "codex"), \
         patch("core.ai_provider.ensure_cli", return_value="codex"), \
         patch("core.ai_provider.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout='{"type":"item.completed","item":{"type":"agent_message","content":"nested response"}}\n',
            stderr="",
        )
        assert ai_provider.call_ai("hello") == "nested response"


def test_cli_agent_executes_tool_then_final():
    with patch.object(config, "AI_PROVIDER", "codex"), \
         patch("core.ai_provider.ensure_cli", return_value="codex"), \
         patch("core.ai_provider.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout='{"type":"tool_call","name":"echo","arguments":{"value":"x"}}', stderr=""),
            MagicMock(returncode=0, stdout='{"type":"final","content":"done"}', stderr=""),
        ]
        result = ai_provider.call_agent(
            [{"role": "user", "content": "use tool"}],
            [{"name": "echo"}],
            lambda name, args: args["value"],
        )
        assert result == "done"


def test_cli_provider_requires_install_opt_in():
    with patch.object(config, "AUTO_INSTALL_CLI", False), \
         patch("core.cli_manager.shutil.which", return_value=None):
        from core.cli_manager import ensure_cli
        try:
            ensure_cli("codex")
        except FileNotFoundError as exc:
            assert "AUTO_INSTALL_CLI=true" in str(exc)
        else:
            raise AssertionError("missing CLI should fail when auto-install is disabled")

if __name__ == "__main__":
    test_default_config()
    test_openai_compatible_call_structure()
    test_node_ai_provider()
    print("🎉 ALL AI PROVIDER TESTS PASSED 100%!")
