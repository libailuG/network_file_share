@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_firewall.ps1"
if errorlevel 1 echo 请右键此脚本，选择“以管理员身份运行”。
pause
