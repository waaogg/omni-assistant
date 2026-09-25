@echo off
title Omni-Assistant WeChat Login Console
color 0E
cd /d "C:\Users\WaaoGG\Documents\Default Project\todo-sync\omni-assistant"

echo ===============================================================================
echo            Omni-Assistant WeChat Login - QR Code & Authorization
echo ===============================================================================
echo [Directory] %CD%
echo [Action] Forcing new WeChat login QR code generation (--login)
echo -------------------------------------------------------------------------------
echo.

node adapters/wechat/wechat_bot.js --login

echo.
echo ===============================================================================
echo WeChat login session completed.
echo ===============================================================================
pause
