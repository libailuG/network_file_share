"""Build from the active Python environment without unrelated PATH DLLs."""
import os
import subprocess
import sys
from pathlib import Path
import PyQt6

base = Path(__file__).resolve().parent
prefix = Path(sys.prefix)
qt_bin = Path(PyQt6.__path__[0]) / 'Qt6' / 'bin'
system_root = Path(os.environ.get('SystemRoot', 'C:/Windows'))
environment = os.environ.copy()
# Unrelated tools may carry an ICU DLL with incompatible exports for Qt.
environment['PATH'] = os.pathsep.join(map(str, (
    qt_bin, prefix, prefix / 'Library' / 'bin', prefix / 'Scripts',
    system_root / 'System32', system_root,
)))
for name in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'PYTHONPATH', 'QT_QPA_PLATFORM'):
    environment.pop(name, None)
subprocess.run([
    sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
    '--onefile', '--windowed', '--noupx', '--name', 'LAN_File_Share',
    '--distpath', str(base), '--workpath', str(base / 'work' / 'pyinstaller'),
    '--specpath', str(base / 'work'),
    '--exclude-module', 'tkinter', '--exclude-module', 'PyQt5',
    '--exclude-module', 'PySide2', '--exclude-module', 'PySide6',
    str(base / 'network_file_share_windows.py'),
], cwd=base, env=environment, check=True)
