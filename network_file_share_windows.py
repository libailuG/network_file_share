#!/usr/bin/env python3
"""Windows desktop launcher for the LAN file share and anonymous chat."""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import Button, Label, Tk, messagebox

from werkzeug.serving import make_server

from network_file_server import create_app


DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8000,
    "password": "",
    "max_upload_mb": 20480,
}


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


def configure_logging(log_dir: Path) -> None:
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


class WindowsLauncher:
    def __init__(self, window: Tk, base_dir: Path, config: dict):
        self.window = window
        self.base_dir = base_dir
        self.config = config
        self.share_dir = base_dir / "network_file"
        self.log_dir = base_dir / "logs"
        self.share_dir.mkdir(parents=True, exist_ok=True)
        configure_logging(self.log_dir)

        app = create_app(
            self.share_dir,
            config["password"] or None,
            config["max_upload_mb"],
            self.log_dir,
        )
        self.server = make_server(config["host"], config["port"], app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, name="lan-file-share", daemon=True)

        self.local_url = f"http://127.0.0.1:{config['port']}"
        self.lan_url = f"http://{local_ip()}:{config['port']}"
        self._build_window()

    def _build_window(self) -> None:
        self.window.title("局域网文件共享与匿名聊天")
        self.window.geometry("580x355")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.stop)

        Label(self.window, text="局域网文件共享与匿名聊天", font=("Microsoft YaHei UI", 17, "bold")).pack(pady=(22, 8))
        Label(self.window, text="● 服务正在运行", fg="#16883c", font=("Microsoft YaHei UI", 11, "bold")).pack()
        Label(self.window, text=f"本机地址：{self.local_url}", font=("Microsoft YaHei UI", 10)).pack(pady=(16, 2))
        Label(self.window, text=f"局域网地址：{self.lan_url}", font=("Microsoft YaHei UI", 10)).pack(pady=2)
        Label(self.window, text=f"共享目录：{self.share_dir}", font=("Microsoft YaHei UI", 9)).pack(pady=(13, 2))
        Label(self.window, text=f"聊天记录：{self.log_dir / 'chat.jsonl'}", font=("Microsoft YaHei UI", 9)).pack(pady=2)

        Button(self.window, text="打开文件共享", width=16, command=lambda: webbrowser.open(self.local_url)).place(x=42, y=235)
        Button(self.window, text="打开匿名聊天", width=16, command=lambda: webbrowser.open(self.local_url + "/chat")).place(x=220, y=235)
        Button(self.window, text="打开共享文件夹", width=16, command=lambda: os.startfile(self.share_dir)).place(x=398, y=235)
        Button(self.window, text="复制局域网地址", width=16, command=self.copy_address).place(x=130, y=285)
        Button(self.window, text="停止并退出", width=16, command=self.stop).place(x=330, y=285)

    def copy_address(self) -> None:
        self.window.clipboard_clear()
        self.window.clipboard_append(self.lan_url)
        self.window.update()
        messagebox.showinfo("已复制", f"已复制：{self.lan_url}", parent=self.window)

    def start(self) -> None:
        self.thread.start()
        self.window.after(600, lambda: webbrowser.open(self.local_url))
        self.window.mainloop()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.window.destroy()


def main() -> int:
    base_dir = application_dir()
    window = Tk()
    window.withdraw()
    try:
        config = load_config(base_dir)
        launcher = WindowsLauncher(window, base_dir, config)
    except Exception as error:
        messagebox.showerror("启动失败", f"无法启动服务：\n{error}", parent=window)
        window.destroy()
        return 1

    window.deiconify()
    launcher.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
