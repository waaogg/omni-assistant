"""
Omni-Assistant (微信与全渠道个人智能助理) - 统一主程序入口
支持：
1. 模块化通道按需选配启动 (ENABLE_WECHAT)
2. 统一 OpenAI 兼容接口 与本地 CLI (agy / codex / opencode / claude) AI 调度中心
3. 优雅启停与进程编排
"""

import os
import sys
import asyncio
import signal
import logging
import argparse
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import config
from core.config import setup_logging
from core.cli_manager import ensure_cli

logger = logging.getLogger("OmniMain")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def print_banner():
    banner = r"""
   ___                  _              _     _              _   
  / _ \ _ __ ___  _ __ (_)            /_\   (_)___ ___  ___| |_ 
 | | | | '_ ` _ \| '_ \| |  _____    //_\\  | / __/ __|/ _ \ __|
 | |_| | | | | | | | | | | |_____|  /  _  \ | \__ \__ \  __/ |_ 
  \___/|_| |_| |_|_| |_|_|          \_/ \_/ |_|___/___/\___|\__|
  ==============================================================
               微信个人智能助理 (Omni-Assistant)
  ==============================================================
    """
    print(banner)

async def run_wechat_subprocess(stop_event: asyncio.Event):
    """Run the WeChat adapter and keep the supervisor alive across child exits."""
    wechat_script = PROJECT_ROOT / "adapters" / "wechat" / "wechat_bot.js"
    if not wechat_script.exists():
        logger.error(f"微信适配器脚本不存在: {wechat_script}")
        return

    while not stop_event.is_set():
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "node", str(wechat_script),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                env=os.environ.copy()
            )
            logger.info(f"微信适配器子进程已拉起 (PID: {proc.pid})")
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                if decoded:
                    logger.info("[WeChat] %s", decoded)

            ret = await proc.wait()
            if stop_event.is_set():
                break
            logger.error("微信适配器异常退出 (Code: %s)，5 秒后重启", ret)
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                proc.terminate()
                await proc.wait()
            raise
        except OSError as exc:
            logger.error("启动微信适配器失败: %s，5 秒后重试", exc)
            await asyncio.sleep(5)

async def main():
    parser = argparse.ArgumentParser(description="Omni-Assistant 微信智能管家中控")
    parser.add_argument("--check-only", action="store_true", help="仅执行环境与配置检查后退出")
    args = parser.parse_args()

    setup_logging()
    print_banner()

    logger.info("正在自检通道配置与 AI 驱动环境...")
    config_errors = config.validate_config()
    if config_errors:
        for error in config_errors:
            logger.error("配置错误: %s", error)
        return 2
    if config.AI_PROVIDER not in ("openai", "deepseek", "default"):
        try:
            cli_path = ensure_cli(config.AI_PROVIDER)
            logger.info("AI CLI 已就绪: %s (%s)", config.AI_PROVIDER, cli_path)
        except Exception as exc:
            logger.error("AI CLI 未就绪: %s", exc)
            return 2
    active_model = getattr(config, "AGY_MODEL", config.LLM_MODEL) if config.AI_PROVIDER == "agy" else config.LLM_MODEL
    logger.info(f"AI 驱动引擎: [{config.AI_PROVIDER.upper()}] (Model: {active_model})")
    if config.AI_PROVIDER in ("openai", "deepseek", "default"):
        logger.info(f"OpenAI Base URL: {config.LLM_BASE_URL}")

    qq_status = "✅ 已启用" if config.ENABLE_QQ else "⏸️ 未启用 (跳过)"
    wechat_status = "✅ 已启用" if config.ENABLE_WECHAT else "⏸️ 未启用 (跳过)"
    todo_status = "✅ 已启用" if config.ENABLE_MS_TODO else "⏸️ 未启用"

    logger.info(f"QQ 通道状态: {qq_status}")
    logger.info(f"微信通道状态: {wechat_status}")
    logger.info(f"微软 To Do 同步: {todo_status}")

    if not config.ENABLE_QQ and not config.ENABLE_WECHAT:
        logger.warning("==========================================================")
        logger.warning("⚠️  当前未启用任何消息通道 (ENABLE_QQ=false, ENABLE_WECHAT=false)")
        logger.warning("👉 请按需在 .env 中设置 ENABLE_QQ=true 或 ENABLE_WECHAT=true")
        logger.warning("==========================================================")
        return 0

    if args.check_only:
        logger.info("配置自检完成 (--check-only 模式)，退出。")
        return 0

    tasks = []
    stop_event = asyncio.Event()

    if config.ENABLE_QQ:
        try:
            from adapters.qq.qqbot_agent import run_qq_adapter
        except ImportError:
            from legacy.qq.qqbot_agent import run_qq_adapter
        tasks.append(asyncio.create_task(run_qq_adapter()))

    if config.ENABLE_WECHAT:
        tasks.append(asyncio.create_task(run_wechat_subprocess(stop_event)))

    # Graceful shutdown handler
    loop = asyncio.get_running_loop()

    def _shutdown_signal():
        logger.info("接收到终止信号，正在关闭各通道适配器...")
        stop_event.set()
        for task in tasks:
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown_signal)
        except NotImplementedError:
            pass

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("各通道任务已安全取消。")
    except Exception as e:
        logger.error(f"运行中发生异常: {e}")

    logger.info("Omni-Assistant 服务已安全停止。")
    return 0

if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code if code is not None else 0)
