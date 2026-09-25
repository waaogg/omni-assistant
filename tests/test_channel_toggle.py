#!/usr/bin/env python3
"""
Test channel toggle behavior (ENABLE_QQ, ENABLE_WECHAT)
"""

import subprocess
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def run_main_check(env_overrides: dict):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("ADMIN_QQ", "10000001")
    env.setdefault("TARGET_GROUP_IDS", "90000001")
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

def test_both_disabled():
    code, out = run_main_check({"ENABLE_QQ": "false", "ENABLE_WECHAT": "false"})
    assert code == 0, f"Expected 0, got {code}"
    assert "未启用任何消息通道" in out
    assert "QQ 通道状态: ⏸️ 未启用" in out
    assert "微信通道状态: ⏸️ 未启用" in out
    print("✅ test_both_disabled passed.")

def test_qq_only_enabled():
    code, out = run_main_check({"ENABLE_QQ": "true", "ENABLE_WECHAT": "false"})
    assert code == 0, f"Expected 0, got {code}"
    assert "QQ 通道状态: ✅ 已启用" in out
    assert "微信通道状态: ⏸️ 未启用" in out
    print("✅ test_qq_only_enabled passed.")

def test_wechat_only_enabled():
    code, out = run_main_check({"ENABLE_QQ": "false", "ENABLE_WECHAT": "true"})
    assert code == 0, f"Expected 0, got {code}"
    assert "QQ 通道状态: ⏸️ 未启用" in out
    assert "微信通道状态: ✅ 已启用" in out
    print("✅ test_wechat_only_enabled passed.")

def test_both_enabled():
    code, out = run_main_check({"ENABLE_QQ": "true", "ENABLE_WECHAT": "true"})
    assert code == 0, f"Expected 0, got {code}"
    assert "QQ 通道状态: ✅ 已启用" in out
    assert "微信通道状态: ✅ 已启用" in out
    print("✅ test_both_enabled passed.")

if __name__ == "__main__":
    test_both_disabled()
    test_qq_only_enabled()
    test_wechat_only_enabled()
    test_both_enabled()
    print("🎉 ALL CHANNEL TOGGLE TESTS PASSED 100%!")
