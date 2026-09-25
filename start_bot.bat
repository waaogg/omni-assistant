@echo off
title Omni-Assistant WeChat Bot Console
color 0B
cd /d "C:\Users\WaaoGG\Documents\Default Project\todo-sync\omni-assistant"

:run_loop
echo ===============================================================================
echo          Omni-Assistant WeChat Bot - Realtime Debug Console
echo ===============================================================================
echo [Directory] %CD%
echo [Time] %DATE% %TIME%
echo [Monitoring] AGY Process, MCP Tool Telemetry, Inbound/Outbound WeChat
echo -------------------------------------------------------------------------------
echo.

node adapters/wechat/wechat_bot.js

echo.
echo ===============================================================================
echo [WARNING] WeChat Bot process exited with code %ERRORLEVEL%
echo [AUTO-RESTART] Restarting service in 3 seconds... Press Ctrl+C to stop.
echo ===============================================================================
timeout /t 3 /nobreak >nul
goto run_loop
