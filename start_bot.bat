@echo off
chcp 65001 >nul
title Omni-Assistant 微信待办助手 [全功能实时调试与日志监控终端]
color 0B
cd /d "%~dp0"
if not exist "adapters\wechat\wechat_bot.js" (
  cd /d "C:\Users\WaaoGG\Documents\Default Project\todo-sync\omni-assistant"
)

:run_loop
echo.
echo ===============================================================================
echo          Omni-Assistant 微信智能助手 - 全功能实时调试与日志监控终端
echo ===============================================================================
echo [启动目录] %CD%
echo [启动时间] %DATE% %TIME%
echo [监控功能] 包含 AGY 子进程管理、流式 Token 统计、MCP 工具调用与微信双向实时请求
echo -------------------------------------------------------------------------------
echo.

node adapters/wechat/wechat_bot.js

echo.
echo ===============================================================================
echo [警告] 微信智能助手服务进程已退出 (退出码: %ERRORLEVEL%)
echo [自愈防护] 正在等待 3 秒后自动重新拉起服务... 若需停止，请直接关闭窗口或按 Ctrl+C。
echo ===============================================================================
timeout /t 3 /nobreak >nul
goto run_loop
