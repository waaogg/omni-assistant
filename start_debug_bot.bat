@echo off
chcp 65001 >nul
title Omni-Assistant WeChat Bot [DEBUG MODE]
color 0B

cd /d "%~dp0"

:run_loop
cls
echo ===============================================================================
echo          Omni-Assistant WeChat Bot - Realtime Debug Console
echo ===============================================================================
echo [Working Directory] %CD%
echo [Launch Time]       %DATE% %TIME%
echo [Mode]              DEBUG MODE (--debug enabled)
echo [Log Level]         Full Protocol Telemetry + Raw Inbound/Outbound Payloads
echo -------------------------------------------------------------------------------
echo.

echo [Step 1/3] Terminating conflicting background bot instances...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*adapters/wechat/wechat_bot.js*' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo [Step 2/3] Checking environment & auth...
if exist "data\wechat\auth_pool.json" (
    echo   - WeChat Auth: Multi-account pool active (data\wechat\auth_pool.json)
) else if exist "data\wechat\auth.json" (
    echo   - WeChat Auth: Found existing login session (data\wechat\auth.json)
) else (
    echo   - WeChat Auth: No session found. QR code login will be generated.
)
echo   - Web Portal:  http://localhost:3000 (Open to scan QR code anytime)
if exist ".env" (
    echo   - Environment: Found configuration (.env)
) else (
    echo   - Environment: WARNING: .env file missing!
)

echo [Step 3/3] Starting WeChat Bot daemon in debug mode...
echo ===============================================================================
echo.

set DEBUG=1
set NODE_ENV=development
node adapters/wechat/wechat_bot.js --debug

echo.
echo ===============================================================================
echo  [PROCESS STOPPED] WeChat Bot exited with code %ERRORLEVEL%
echo ===============================================================================
echo.
echo  Enter 'r' and press ENTER to restart, or press ENTER to close this window.
set /p USER_ACTION="  [Restart/Quit]: "
if /i "%USER_ACTION%"=="r" goto run_loop
