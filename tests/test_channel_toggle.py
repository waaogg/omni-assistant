#!/usr/bin/env python3
"""Test that only the WeChat channel controls the active entrypoint."""

import subprocess
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def run_main_check(env_overrides: dict):
    env = os.environ.copy()
    env.update(env_overrides)
    res = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "main.py"), "--check-only"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROJECT_ROOT),
        env=env
    )
    combined = (res.stdout or "") + "\n" + (res.stderr or "")
    return res.returncode, combined

def test_wechat_disabled():
    code, out = run_main_check({"ENABLE_QQ": "true", "ENABLE_WECHAT": "false"})
    assert code == 0, f"Expected 0, got {code}"
    assert "微信通道状态" in out
    assert "未启用" in out
    assert "QQ" not in out
    print("✅ test_wechat_disabled passed.")

def test_wechat_enabled():
    code, out = run_main_check({"ENABLE_QQ": "true", "ENABLE_WECHAT": "true"})
    assert code == 0, f"Expected 0, got {code}"
    assert "微信通道状态" in out
    assert "已启用" in out
    assert "QQ" not in out
    print("✅ test_wechat_enabled passed.")

def test_qq_config_is_not_active():
    sys.path.insert(0, str(PROJECT_ROOT))
    from core import config

    assert not hasattr(config, "ENABLE_QQ")
    assert not hasattr(config, "NAPCAT_HTTP_URL")
    print("✅ test_qq_config_is_not_active passed.")

if __name__ == "__main__":
    test_wechat_disabled()
    test_wechat_enabled()
    test_qq_config_is_not_active()
    print("🎉 WECHAT-ONLY CHANNEL TESTS PASSED 100%!")
