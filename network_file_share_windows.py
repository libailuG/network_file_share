#!/usr/bin/env python3
"""Windows desktop launcher for the LAN file share and anonymous chat."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import tempfile
import urllib.request
from logging.handlers import RotatingFileHandler
from contextlib import ExitStack
from pathlib import Path
from PyQt6.QtCore import QEvent, QLockFile, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QGridLayout, QLabel, QMainWindow,
    QMenu, QMessageBox, QPushButton, QStyle, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from werkzeug.serving import make_server

from network_file_server import create_app


DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8000,
    "password": "",
    "max_upload_mb": 20480,
    "auto_start": True,
}

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE = "LANFileShare"


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        arguments = [str(Path(sys.executable).resolve()), "--autostart"]
    else:
        interpreter = Path(sys.executable).resolve()
        windowed = interpreter.with_name("pythonw.exe")
        if windowed.exists():
            interpreter = windowed
        arguments = [str(interpreter), str(Path(__file__).resolve()), "--autostart"]
    command = subprocess.list2cmdline(arguments)
    if len(command) > 260:
        raise OSError("程序路径过长，请移动到较短的目录后设置开机启动")
    return command


def read_autostart() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, AUTOSTART_VALUE)
            return value
    except FileNotFoundError:
        return None


def set_autostart(enabled: bool) -> None:
    if sys.platform != "win32":
        raise OSError("开机启动仅支持 Windows")
    import winreg
    desired = startup_command() if enabled else None
    if read_autostart() == desired:
        return
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, access=winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_VALUE, 0, winreg.REG_SZ, desired)
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_VALUE)
            except FileNotFoundError:
                pass


def save_config(base_dir: Path, config: dict) -> None:
    path = base_dir / "config.json"
    current = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    current.update(config)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=base_dir, delete=False) as output:
            temporary = Path(output.name)
            json.dump(current, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def application_dir() -> Path:
    """Use the executable directory so data stays beside the Windows app."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_config(base_dir: Path) -> dict:
    config = DEFAULT_CONFIG.copy()
    config_path = base_dir / "config.json"
    if config_path.exists():
        loaded = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, dict):
            raise ValueError("config.json 必须是 JSON 对象")
        config.update({key: loaded[key] for key in DEFAULT_CONFIG if key in loaded})

    config["host"] = str(config["host"])
    config["port"] = int(config["port"])
    config["password"] = str(config["password"] or "")
    config["max_upload_mb"] = int(config["max_upload_mb"])
    if not isinstance(config["auto_start"], bool):
        raise ValueError("auto_start 必须是 true 或 false")
    if not 1 <= config["port"] <= 65535:
        raise ValueError("port 必须在 1～65535 之间")
    if config["max_upload_mb"] < 1:
        raise ValueError("max_upload_mb 必须大于 0")
    return config


def local_ip() -> str:
    """Find the preferred LAN address without sending application data."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return str(probe.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        probe.close()


def configure_logging(log_dir: Path) -> RotatingFileHandler:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "server.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("werkzeug").addHandler(handler)
    logging.getLogger("werkzeug").setLevel(logging.INFO)
    return handler


class WindowsLauncher(QMainWindow):
    def __init__(self, base_dir: Path, config: dict):
        super().__init__()
        self.base_dir = base_dir
        self.config = config
        self.share_dir = base_dir / "data"
        self.log_dir = base_dir / "logs"
        self.share_dir.mkdir(parents=True, exist_ok=True)
        self.log_handler = configure_logging(self.log_dir)

        self.server = None
        self.thread = None
        self._exit_requested = False
        self._tray_notice_shown = False
        self.local_url = f"http://127.0.0.1:{config['port']}"
        self.lan_url = f"http://{local_ip()}:{config['port']}"
        self._build_window()
        self._build_tray()

    def _build_window(self) -> None:
        self.setWindowTitle("局域网文件共享 · Qt")
        self.resize(720, 560)
        self.setMinimumSize(620, 540)
        self.setStyleSheet("""
            QMainWindow { background: #f4f7fb; }
            QWidget { font-family: 'Microsoft YaHei UI'; font-size: 13px; color: #172b4d; }
            QLabel#title { font-size: 24px; font-weight: 700; }
            QLabel#subtitle { color: #62748d; }
            QFrame#card { background: white; border: 1px solid #dce5f0; border-radius: 12px; }
            QPushButton { background: white; border: 1px solid #cbd8e8; border-radius: 7px; padding: 11px 16px; }
            QPushButton:hover { background: #eaf1fb; }
            QPushButton#primary { background: #2563eb; color: white; border: none; }
            QPushButton#primary:hover { background: #1d4ed8; }
        """)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        title = QLabel("局域网文件共享与匿名聊天")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel("将文件放入共享文件夹，同一局域网的设备即可通过浏览器访问。")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)
        self.status_label = QLabel("● 服务未启动")
        layout.addWidget(self.status_label)
        self.startup_checkbox = QCheckBox("开机启动（登录 Windows 后自动运行）")
        self.startup_checkbox.setChecked(self.config["auto_start"])
        self.startup_checkbox.setEnabled(sys.platform == "win32")
        self.startup_checkbox.setToolTip("默认开启；开机启动时直接进入托盘，不弹出浏览器。取消勾选立即生效。")
        self.startup_checkbox.toggled.connect(self.change_autostart)
        layout.addWidget(self.startup_checkbox)
        card = QFrame()
        card.setObjectName("card")
        card.setMinimumHeight(210)
        details = QGridLayout(card)
        details.setContentsMargins(18, 18, 18, 18)
        details.setVerticalSpacing(14)
        self.local_label = QLabel()
        self.lan_label = QLabel()
        for row, (label, value) in enumerate((
            ("本机访问", self.local_label), ("局域网访问", self.lan_label),
            ("共享文件夹", QLabel(str(self.share_dir))),
            ("聊天记录", QLabel(str(self.log_dir / "chat.jsonl"))),
            ("访问口令", QLabel("已设置" if self.config["password"] else "未设置")),
        )):
            details.addWidget(QLabel(label), row, 0)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            details.addWidget(value, row, 1)
        details.setColumnStretch(1, 1)
        self._update_addresses()
        layout.addWidget(card)
        buttons = QGridLayout()
        for row, column, text, callback in (
            (0, 0, "打开文件共享", lambda: self.open_url(self.local_url)),
            (0, 1, "打开匿名聊天", lambda: self.open_url(self.local_url + "/chat")),
            (0, 2, "打开共享文件夹", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.share_dir)))),
            (1, 0, "复制局域网地址", self.copy_address),
            (1, 1, "最小化到托盘", self.minimize_to_tray),
            (1, 2, "停止并退出", self.exit_application),
        ):
            button = QPushButton(text)
            if row == 0 and column == 0:
                button.setObjectName("primary")
            button.clicked.connect(callback)
            buttons.addWidget(button, row, column)
        layout.addLayout(buttons)
        layout.addStretch()
        self.statusBar().showMessage("最小化或关闭窗口会转入托盘；选择“停止并退出”结束共享。")

    def _build_tray(self) -> None:
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("局域网文件共享")
        self.tray_menu = QMenu(self)
        for text, callback in (
            ("显示主窗口", self.restore_window),
            ("打开文件共享", lambda: self.open_url(self.local_url)),
            ("打开匿名聊天", lambda: self.open_url(self.local_url + "/chat")),
            ("打开共享文件夹", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.share_dir)))),
            ("复制局域网地址", self.copy_address),
        ):
            self.tray_menu.addAction(text).triggered.connect(callback)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("停止并退出").triggered.connect(self.exit_application)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.messageClicked.connect(self.restore_window)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore_window()

    def restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def minimize_to_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.showMinimized()
            return
        self.tray.show()
        self.hide()
        if not self._tray_notice_shown:
            self.tray.showMessage("文件共享继续运行", "点击托盘图标恢复窗口，右键菜单可停止并退出。")
            self._tray_notice_shown = True

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (event.type() == QEvent.Type.WindowStateChange and self.isMinimized()
                and QSystemTrayIcon.isSystemTrayAvailable()):
            QTimer.singleShot(0, self.minimize_to_tray)

    def exit_application(self) -> None:
        self._exit_requested = True
        self.close()
        QApplication.instance().quit()

    def _update_addresses(self) -> None:
        self.local_label.setText(self.local_url)
        self.lan_label.setText(self.lan_url)

    def change_autostart(self, enabled: bool) -> None:
        previous = self.config["auto_start"]
        updated = {**self.config, "auto_start": enabled}
        saved = False
        try:
            save_config(self.base_dir, updated)
            saved = True
            set_autostart(enabled)
        except (OSError, ValueError) as error:
            if saved:
                try:
                    save_config(self.base_dir, self.config)
                except OSError:
                    logging.getLogger("werkzeug").exception("Failed to restore startup preference")
            self.startup_checkbox.blockSignals(True)
            self.startup_checkbox.setChecked(previous)
            self.startup_checkbox.blockSignals(False)
            QMessageBox.warning(self, "设置未生效", f"无法更改开机启动：\n{error}")
            return
        self.config.update(updated)
        self.statusBar().showMessage("已开启开机启动" if enabled else "已关闭开机启动", 5000)

    def sync_autostart(self) -> None:
        if sys.platform != "win32":
            return
        try:
            set_autostart(self.config["auto_start"])
        except OSError as error:
            logging.getLogger("werkzeug").warning("Could not register startup: %s", error)
            self.statusBar().showMessage(f"开机启动未生效：{error}")
            self.tray.showMessage("开机启动未生效", str(error), QSystemTrayIcon.MessageIcon.Warning)

    @staticmethod
    def open_url(url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def copy_address(self) -> None:
        QApplication.clipboard().setText(self.lan_url)
        self.statusBar().showMessage("局域网地址已复制", 4000)

    def start(self) -> None:
        app = create_app(self.share_dir, self.config["password"] or None,
                         self.config["max_upload_mb"], self.log_dir)
        try:
            self.server = make_server(self.config["host"], self.config["port"], app, threaded=True)
        except SystemExit as error:
            raise RuntimeError(f"无法监听端口 {self.config['port']}，请关闭已运行的共享程序或修改 config.json。") from error
        port = self.server.server_port
        self.local_url = f"http://127.0.0.1:{port}"
        self.lan_url = f"http://{local_ip()}:{port}"
        self._update_addresses()
        self.tray.setToolTip(f"文件共享正在运行\n{self.lan_url}")
        self.thread = threading.Thread(target=self.server.serve_forever, name="lan-file-share", daemon=True)
        self.thread.start()
        self.status_label.setText("● 服务正在运行")
        self.status_label.setStyleSheet("color: #16883c; font-weight: bold;")

    def stop(self) -> None:
        if self.server is not None:
            if self.thread is not None and self.thread.is_alive():
                self.server.shutdown()
                self.thread.join(timeout=2)
            self.server.server_close()
            self.server = None
        if self.log_handler is not None:
            logging.getLogger("werkzeug").removeHandler(self.log_handler)
            self.log_handler.close()
            self.log_handler = None

    def closeEvent(self, event) -> None:
        if not self._exit_requested and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.minimize_to_tray()
            return
        self.tray.hide()
        self.stop()
        event.accept()
        QApplication.instance().quit()


def self_test(application: QApplication, report_path: Path) -> int:
    """Exercise the frozen application without touching the user's files."""
    report = {"ok": False}
    launcher = None
    global AUTOSTART_KEY
    original_startup_key = AUTOSTART_KEY
    test_startup_key = None
    try:
        with ExitStack() as cleanup:
            directory = cleanup.enter_context(tempfile.TemporaryDirectory())
            config = {**DEFAULT_CONFIG, "host": "127.0.0.1", "port": 0}
            launcher = WindowsLauncher(Path(directory), config)
            cleanup.callback(launcher.stop)
            launcher.start()
            config["port"] = launcher.server.server_port
            launcher.show()
            application.processEvents()
            with urllib.request.urlopen(launcher.local_url, timeout=10) as response:
                assert response.status == 200
            upload = urllib.request.Request(launcher.local_url + "/upload-stream/?path=check.txt", data=b"portable Qt check", method="POST")
            with urllib.request.urlopen(upload, timeout=10) as response:
                assert response.status == 200
            with urllib.request.urlopen(launcher.local_url + "/download/check.txt", timeout=10) as response:
                assert response.read() == b"portable Qt check"
            with urllib.request.urlopen(launcher.local_url + "/api/chat/messages", timeout=10) as response:
                assert json.load(response)["messages"] == []
            launcher.copy_address()
            assert application.clipboard().text() == launcher.lan_url
            assert launcher.startup_checkbox.isChecked()
            if sys.platform == "win32":
                # Exercise real registry I/O in an isolated key, never in Run.
                test_startup_key = "Software\\LANFileShareTests\\" + Path(directory).name
                AUTOSTART_KEY = test_startup_key
                launcher.startup_checkbox.setChecked(False)
                assert read_autostart() is None
                assert load_config(Path(directory))["auto_start"] is False
                launcher.startup_checkbox.setChecked(True)
                assert read_autostart() == startup_command()
                assert load_config(Path(directory))["auto_start"] is True
                launcher.startup_checkbox.setChecked(False)
                assert read_autostart() is None
                assert load_config(Path(directory))["auto_start"] is False
                AUTOSTART_KEY = original_startup_key
            tray_available = QSystemTrayIcon.isSystemTrayAvailable()
            if tray_available:
                launcher.showMinimized()
                application.processEvents()
                application.processEvents()
                assert not launcher.isVisible() and launcher.tray.isVisible()
                launcher.restore_window()
                application.processEvents()
                launcher.minimize_to_tray()
                application.processEvents()
                assert not launcher.isVisible() and launcher.tray.isVisible()
                with urllib.request.urlopen(launcher.local_url, timeout=10) as response:
                    assert response.status == 200
                launcher.restore_window()
                application.processEvents()
                assert launcher.isVisible()
                launcher.close()
                assert launcher.server is not None and not launcher.isVisible()
            report.update({"ok": True, "frozen": bool(getattr(sys, "frozen", False)),
                           "tray_available": tray_available, "exe": sys.executable,
                           "checks": ["window", "upload", "download", "chat", "clipboard", "shutdown"]})
            if tray_available:
                report["checks"].extend(["minimize_to_tray", "tray_hide", "background_http", "restore", "close_to_tray"])
            if sys.platform == "win32":
                report["checks"].extend(["autostart_default", "autostart_enable", "autostart_disable", "autostart_persistence"])
            server_thread = launcher.thread
            launcher.exit_application()
            assert not server_thread.is_alive()
    except Exception as error:
        import traceback
        report = {"ok": False, "error": repr(error), "traceback": traceback.format_exc()}
        if launcher is not None:
            launcher.stop()
    finally:
        AUTOSTART_KEY = original_startup_key
        if test_startup_key is not None:
            import winreg
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, test_startup_key)
            except FileNotFoundError:
                pass
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def main() -> int:
    base_dir = application_dir()
    application = QApplication(sys.argv)
    application.setApplicationName("LAN File Share")
    application.setQuitOnLastWindowClosed(False)
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        return self_test(application, Path(sys.argv[2]))
    launcher = None
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(base_dir / ".lan-file-share.lock"))
        if not lock.tryLock(0):
            QMessageBox.information(None, "程序已运行", "共享程序已经启动，请使用现有窗口。")
            return 0
        config = load_config(base_dir)
        save_config(base_dir, config)
        launcher = WindowsLauncher(base_dir, config)
        launcher.start()
        launcher.sync_autostart()
    except Exception as error:
        if launcher is not None:
            launcher.stop()
        QMessageBox.critical(None, "启动失败", f"无法启动服务：\n{error}")
        return 1
    application.aboutToQuit.connect(launcher.stop)
    if "--autostart" in sys.argv and QSystemTrayIcon.isSystemTrayAvailable():
        launcher.hide()
    else:
        launcher.show()
    if "--autostart" not in sys.argv:
        QTimer.singleShot(600, lambda: launcher.open_url(launcher.local_url))
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
