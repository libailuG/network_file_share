@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python network_file_server.py --dir "%~dp0data" --logs-dir "%~dp0logs"
if errorlevel 1 goto :failed
exit /b 0
:failed
echo Failed. Activate your Python environment and check the error above.
pause
exit /b 1
