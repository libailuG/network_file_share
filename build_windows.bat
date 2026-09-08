@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo [1/4] 检查 Python...
where py >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python。请先安装 Python 3.11 或 3.12，并勾选 Add Python to PATH。
  pause
  exit /b 1
)

echo [2/4] 创建独立构建环境...
if not exist ".windows-build-env\Scripts\python.exe" py -3 -m venv .windows-build-env
if errorlevel 1 goto :failed

echo [3/4] 安装构建依赖...
".windows-build-env\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".windows-build-env\Scripts\python.exe" -m pip install -r requirements.txt "pyinstaller>=6.0,<7"
if errorlevel 1 goto :failed

echo [4/4] 生成 Windows EXE...
".windows-build-env\Scripts\python.exe" -m PyInstaller ^
  --noconfirm --clean --onefile --windowed ^
  --name LAN_File_Share ^
  --collect-all flask --collect-all jinja2 ^
  network_file_share_windows.py
if errorlevel 1 goto :failed

copy /y windows_config.example.json dist\windows_config.example.json >nul
echo.
echo 构建成功：dist\LAN_File_Share.exe
echo 将 EXE 复制到任意可写文件夹后双击即可运行。
explorer "%cd%\dist"
pause
exit /b 0

:failed
echo.
echo 构建失败，请查看上方错误信息。
pause
exit /b 1
