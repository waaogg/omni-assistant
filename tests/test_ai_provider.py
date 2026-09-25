#!/usr/bin/env python3
"""
Test AI Provider multi-backend abstraction (OpenAI compatible / DeepSeek / agy)
"""

import sys
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import config, ai_provider

def test_default_config():
    assert config.AI_PROVIDER == "agy", f"Expected default agy, got {config.AI_PROVIDER}"
    assert "deepseek" in config.LLM_BASE_URL.lower() or "v1" in config.LLM_BASE_URL, "Default base URL should point to DeepSeek"
    assert config.LLM_MODEL == "deepseek-chat"
    print("✅ test_default_config passed.")

def test_openai_compatible_call_structure():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"choices": [{"message": {"content": "Hello from DeepSeek Mock"}}]}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        with patch.object(config, "AI_PROVIDER", "openai"):
            reply = ai_provider.call_ai("Test message", system_prompt="Test system")
        assert reply == "Hello from DeepSeek Mock"

        # Verify call args
        args, kwargs = mock_urlopen.call_args
        req = args[0]
        assert req.get_method() == "POST"
        assert req.get_header("Content-type") == "application/json"
        assert "chat/completions" in req.full_url
    print("✅ test_openai_compatible_call_structure passed.")

def test_agy_call_structure():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"response": "Hello from agy CLI Mock", "conversation_id": "test_conv_123"}',
            stderr=""
        )

        with patch.object(config, "AI_PROVIDER", "agy"):
            reply = ai_provider.call_ai("Test prompt via agy")
            assert reply == "Hello from agy CLI Mock"
            
            # Check args
            args, kwargs = mock_run.call_args
            cmd_args = args[0]
            assert cmd_args[0].lower().endswith(("agy", "agy.exe", "agy.cmd"))
            assert "-p" in cmd_args
            assert "--model" in cmd_args
    print("✅ test_agy_call_structure passed.")

def test_node_ai_provider():
    res = subprocess.run(
        ["node", "-e", """
        const ai = require('./core/ai_provider.js');
        if (typeof ai.executeAI !== 'function') process.exit(1);
        if (ai.AI_PROVIDER !== 'agy') process.exit(2);
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

if __name__ == "__main__":
    test_default_config()
    test_openai_compatible_call_structure()
    test_agy_call_structure()
    test_node_ai_provider()
    print("🎉 ALL AI PROVIDER TESTS PASSED 100%!")
