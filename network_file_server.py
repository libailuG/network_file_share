#!/usr/bin/env python3
"""A small browser-based file share for trusted local networks."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
import time
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from flask import (
    Flask,
    Response,
    abort,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>局域网文件共享</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
    h1 { font-size: 1.6rem; }
    .bar, form { display:flex; gap:.6rem; align-items:center; flex-wrap:wrap; }
    .card { border:1px solid #9996; border-radius:12px; padding:1rem; margin:1rem 0; }
    table { border-collapse:collapse; width:100%; }
    th, td { text-align:left; padding:.65rem; border-bottom:1px solid #9995; }
    th:last-child, td:last-child { text-align:right; }
    a { color:#1683d8; text-decoration:none; }
    input, button { font:inherit; padding:.5rem .7rem; }
    .muted { opacity:.7; font-size:.9rem; }
    .error { color:#d33; }
  </style>
</head>
<body>
  <div class="bar"><h1 style="margin-right:auto">📁 局域网文件共享</h1>
    <a href="{{ url_for('chat') }}">💬 匿名聊天</a>
  </div>
  <div class="bar muted">当前位置：<a href="{{ url_for('browse') }}">根目录</a>
  {% for crumb in crumbs %} / <a href="{{ url_for('browse', subpath=crumb.path) }}">{{ crumb.name }}</a>{% endfor %}</div>

  <p class="bar">
    {% if parent is not none %}<a href="{{ url_for('browse', subpath=parent) }}">⬅ 返回上级目录</a>{% endif %}
    <a href="{{ url_for('download_folder', subpath=current) }}">⬇ 下载当前文件夹（ZIP）</a>
  </p>

  <div class="card">
    <form id="upload-form" action="{{ url_for('upload', subpath=current) }}"
          data-stream-url="{{ url_for('upload_stream', subpath=current) }}"
          data-success-url="{{ url_for('browse', subpath=current) }}"
          method="post" enctype="multipart/form-data">
      <label>选择文件：<input id="file-input" type="file" name="files" multiple></label>
      <label>选择文件夹：<input id="folder-input" type="file" name="folders" webkitdirectory directory multiple></label>
      <button id="upload-button" type="submit">上传文件/文件夹</button>
    </form>
    <div id="upload-state" style="display:none;margin-top:.7rem">
      <progress id="upload-progress" value="0" max="100" style="width:min(100%,420px)"></progress>
      <span id="upload-text">准备上传…</span>
    </div>
    <form action="{{ url_for('mkdir', subpath=current) }}" method="post" style="margin-top:.7rem">
      <input name="name" placeholder="新文件夹名称" required>
      <button type="submit">新建文件夹</button>
    </form>
    {% if message %}<p class="{{ 'error' if is_error else '' }}">{{ message }}</p>{% endif %}
  </div>

  <table>
    <thead><tr><th>名称</th><th>大小</th><th>修改时间</th><th>操作</th></tr></thead>
    <tbody>
    {% for item in items %}
      <tr>
        <td>{% if item.is_dir %}📁 <a href="{{ url_for('browse', subpath=item.rel) }}">{{ item.name }}</a>{% else %}📄 {{ item.name }}{% endif %}</td>
        <td>{{ item.size }}</td><td>{{ item.mtime }}</td>
        <td>{% if item.is_dir %}<a href="{{ url_for('download_folder', subpath=item.rel) }}">下载文件夹</a>{% else %}<a href="{{ url_for('download', subpath=item.rel) }}">下载</a>{% endif %}</td>
      </tr>
    {% else %}<tr><td colspan="4" class="muted">这个文件夹是空的</td></tr>{% endfor %}
    </tbody>
  </table>
  <script>
    const form = document.getElementById('upload-form');
    const button = document.getElementById('upload-button');
    const fileInput = document.getElementById('file-input');
    const folderInput = document.getElementById('folder-input');
    const state = document.getElementById('upload-state');
    const progress = document.getElementById('upload-progress');
    const text = document.getElementById('upload-text');

    function formatSpeed(bytesPerSecond) {
      if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return '计算中…';
      const units = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
      let value = bytesPerSecond;
      let unit = 0;
      while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
      }
      return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (fileInput.files.length === 0 && folderInput.files.length === 0) {
        state.style.display = 'block';
        text.textContent = '请先选择文件或文件夹';
        return;
      }

      const items = [
        ...Array.from(fileInput.files, file => ({file, path: file.name})),
        ...Array.from(folderInput.files, file => ({
          file, path: file.webkitRelativePath || file.name
        })),
      ];
      const totalBytes = items.reduce((sum, item) => sum + item.file.size, 0);
      const loaded = new Array(items.length).fill(0);
      button.disabled = true;
      state.style.display = 'block';
      text.textContent = '正在上传：0%';
      const startedAt = performance.now();
      let lastAt = startedAt;
      let lastTotalLoaded = 0;
      let displayedSpeed = 0;
      let completed = 0;
      let nextIndex = 0;
      let failed = false;
      const activeRequests = new Set();

      function updateProgress(index, bytes) {
        loaded[index] = bytes;
        const totalLoaded = loaded.reduce((sum, value) => sum + value, 0);
        const now = performance.now();
        const elapsed = (now - lastAt) / 1000;
        if (elapsed >= 0.25) {
          const currentSpeed = Math.max(0, totalLoaded - lastTotalLoaded) / elapsed;
          displayedSpeed = displayedSpeed ? displayedSpeed * 0.65 + currentSpeed * 0.35 : currentSpeed;
          lastAt = now;
          lastTotalLoaded = totalLoaded;
        }
        const percent = totalBytes ? Math.round(totalLoaded * 100 / totalBytes) : 100;
        progress.value = percent;
        text.textContent = `正在上传：${percent}% · ${formatSpeed(displayedSpeed)} · ${completed}/${items.length} 个文件`;
      }

      function uploadOne(item, index) {
        return new Promise((resolve, reject) => {
          const target = new URL(form.dataset.streamUrl, window.location.href);
          target.searchParams.set('path', item.path);
          const xhr = new XMLHttpRequest();
          activeRequests.add(xhr);
          xhr.open('POST', target);
          xhr.setRequestHeader('Content-Type', 'application/octet-stream');
          xhr.upload.addEventListener('progress', e => updateProgress(index, e.loaded));
          xhr.addEventListener('load', () => {
            activeRequests.delete(xhr);
            if (xhr.status >= 200 && xhr.status < 300) {
              loaded[index] = item.file.size;
              completed += 1;
              updateProgress(index, item.file.size);
              resolve();
            } else {
              reject(new Error(`HTTP ${xhr.status}：${xhr.responseText || '服务器拒绝了上传'}`));
            }
          });
          xhr.addEventListener('error', () => reject(new Error('网络连接中断')));
          xhr.addEventListener('abort', () => reject(new Error('上传已取消')));
          xhr.send(item.file);
        });
      }

      async function worker() {
        while (!failed) {
          const index = nextIndex++;
          if (index >= items.length) return;
          await uploadOne(items[index], index);
        }
      }

      // 大文件顺序直写，避免机械硬盘来回寻道；小文件使用三个连接降低请求等待。
      const hasLargeFile = items.some(item => item.file.size >= 256 * 1024 * 1024);
      const workerCount = Math.min(hasLargeFile ? 1 : 3, items.length);
      try {
        await Promise.all(Array.from({length: workerCount}, () => worker()));
        const seconds = Math.max((performance.now() - startedAt) / 1000, 0.001);
        text.textContent = `上传成功 · 平均 ${formatSpeed(totalBytes / seconds)} · 正在刷新…`;
        const success = new URL(form.dataset.successUrl, window.location.href);
        success.searchParams.set('message', `已上传 ${items.length} 个文件`);
        window.location.href = success;
      } catch (error) {
        failed = true;
        for (const xhr of activeRequests) xhr.abort();
        button.disabled = false;
        text.textContent = `上传失败：${error.message}，请重试`;
      }
    });
  </script>
</body></html>"""

CHAT_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>局域网匿名聊天</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { max-width:900px; margin:1.5rem auto; padding:0 1rem; }
    .bar { display:flex; gap:.8rem; align-items:center; flex-wrap:wrap; }
    h1 { font-size:1.6rem; margin-right:auto; }
    a { color:#1683d8; text-decoration:none; }
    .muted { opacity:.7; font-size:.9rem; }
    #messages { height:min(65vh,620px); overflow-y:auto; border:1px solid #9996;
      border-radius:12px; padding:.7rem 1rem; margin:1rem 0; background:#7771; }
    .message { padding:.65rem 0; border-bottom:1px solid #9993; }
    .time { display:block; opacity:.6; font-size:.8rem; margin-bottom:.25rem; }
    .body { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.45; }
    form { display:flex; gap:.6rem; align-items:flex-end; }
    textarea { flex:1; min-height:2.7rem; max-height:10rem; resize:vertical;
      font:inherit; padding:.65rem; border-radius:8px; }
    button { font:inherit; padding:.65rem 1rem; }
    #status { min-height:1.3rem; margin:.5rem 0; }
    .error { color:#d33; }
  </style>
</head>
<body>
  <div class="bar"><h1>💬 局域网匿名聊天</h1>
    <a href="{{ url_for('browse') }}">📁 文件共享</a>
  </div>
  <div class="muted">当前匿名在线 <strong id="online-count">{{ online_count }}</strong> 人 · 聊天室不显示用户身份 · 聊天记录保存在服务器 logs 目录</div>
  <div id="messages" aria-live="polite"><p id="empty" class="muted">正在加载历史聊天…</p></div>
  <form id="chat-form">
    <textarea id="message-input" maxlength="1000" placeholder="输入消息，Enter 发送，Shift+Enter 换行" required></textarea>
    <button id="send-button" type="submit">发送</button>
  </form>
  <p id="status" class="muted"></p>
  <script>
    const messages = document.getElementById('messages');
    const form = document.getElementById('chat-form');
    const input = document.getElementById('message-input');
    const button = document.getElementById('send-button');
    const status = document.getElementById('status');
    let latestId = 0;
    let loading = false;

    function addMessage(item) {
      if (messages.querySelector(`[data-id="${Number(item.id)}"]`)) return;
      document.getElementById('empty')?.remove();
      const row = document.createElement('div');
      row.className = 'message';
      row.dataset.id = item.id;
      const timestamp = document.createElement('span');
      timestamp.className = 'time';
      const parsed = new Date(item.timestamp);
      timestamp.textContent = Number.isNaN(parsed.getTime()) ? item.timestamp : parsed.toLocaleString();
      const body = document.createElement('div');
      body.className = 'body';
      body.textContent = item.message;
      row.append(timestamp, body);
      messages.append(row);
      latestId = Math.max(latestId, Number(item.id) || 0);
    }

    async function loadMessages() {
      if (loading) return;
      loading = true;
      const shouldStick = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
      try {
        const response = await fetch(`{{ url_for('chat_messages') }}?after=${latestId}`, {cache:'no-store'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        document.getElementById('online-count').textContent = data.online_count;
        for (const item of data.messages) addMessage(item);
        if (shouldStick && data.messages.length) messages.scrollTop = messages.scrollHeight;
        status.textContent = '';
        status.className = 'muted';
      } catch (error) {
        status.textContent = `连接失败，正在重试：${error.message}`;
        status.className = 'error';
      } finally {
        loading = false;
      }
    }

    form.addEventListener('submit', async event => {
      event.preventDefault();
      const message = input.value.trim();
      if (!message) return;
      button.disabled = true;
      try {
        const response = await fetch(`{{ url_for('send_chat_message') }}`, {
          method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        input.value = '';
        addMessage(data.message);
        messages.scrollTop = messages.scrollHeight;
        status.textContent = '';
        input.focus();
      } catch (error) {
        status.textContent = `发送失败：${error.message}`;
        status.className = 'error';
      } finally {
        button.disabled = false;
      }
    });

    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    loadMessages();
    setInterval(loadMessages, 1500);
  </script>
</body></html>"""

LOGIN_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>登录</title>
<body style="font-family:system-ui;max-width:400px;margin:4rem auto;padding:1rem">
<h2>局域网文件共享</h2><form method="post"><input type="password" name="password"
placeholder="访问口令" autofocus required><button>进入</button></form>
{% if error %}<p style="color:#d33">口令错误</p>{% endif %}</body></html>"""


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def safe_name(raw_name: str) -> str:
    """Keep Unicode names while removing any client-supplied directory portion."""
    name = raw_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if name in {"", ".", ".."} or "\x00" in name or any(ord(ch) < 32 for ch in name):
        return ""
    return name


def safe_relative_name(raw_name: str) -> Path | None:
    """Validate a browser-provided folder-relative filename."""
    normalized = raw_name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts:
        return None
    clean_parts = []
    for part in path.parts:
        if safe_name(part) != part:
            return None
        clean_parts.append(part)
    return Path(*clean_parts)


class StreamingZipBuffer:
    """A non-seekable output accepted by zipfile and drained by a generator."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._position = 0

    def write(self, data: bytes) -> int:
        self._buffer.extend(data)
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        data = bytes(self._buffer)
        self._buffer.clear()
        return data


class ChatStore:
    """Thread-safe append-only JSONL chat history."""

    def __init__(self, log_dir: Path, retained_messages: int = 5000):
        self.log_dir = log_dir.resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / "chat.jsonl"
        self._messages: deque[dict] = deque(maxlen=retained_messages)
        self._lock = threading.Lock()
        self._next_id = 1
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as history:
            for raw_line in history:
                try:
                    item = json.loads(raw_line)
                    message_id = int(item["id"])
                    if message_id < 1 or not all(key in item for key in ("message", "timestamp")):
                        continue
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
                self._messages.append(item)
                self._next_id = max(self._next_id, message_id + 1)

    def after(self, message_id: int, initial_limit: int = 200) -> list[dict]:
        with self._lock:
            result = [item.copy() for item in self._messages if item["id"] > message_id]
        if message_id == 0:
            return result[-initial_limit:]
        return result

    @staticmethod
    def public_message(item: dict) -> dict:
        """Return only fields that are safe to expose to chat clients."""
        return {key: item[key] for key in ("id", "timestamp", "message")}

    def append(self, message: str) -> dict:
        with self._lock:
            item = {
                "id": self._next_id,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "message": message,
            }
            encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self.path.open("a", encoding="utf-8") as log:
                log.write(encoded)
                log.flush()
                os.fsync(log.fileno())
            self._messages.append(item)
            self._next_id += 1
            return self.public_message(item)


def create_app(root: Path, password: str | None, max_upload_mb: int, log_dir: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FILE_SHARE_SECRET", secrets.token_hex(32))
    app.config["MAX_CONTENT_LENGTH"] = max_upload_mb * 1024 * 1024
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    chat_store = ChatStore(log_dir if log_dir is not None else root.parent / "logs")
    chat_rate_limits: dict[str, float] = {}
    chat_rate_lock = threading.Lock()
    chat_presence: dict[str, float] = {}
    chat_presence_lock = threading.Lock()

    def anonymous_identity() -> str:
        client_id = session.get("chat_client_id")
        if not client_id:
            client_id = secrets.token_hex(16)
            session["chat_client_id"] = client_id
        return client_id

    def mark_chat_online(client_id: str) -> int:
        """Record a private heartbeat and return the active anonymous session count."""
        now = time.monotonic()
        with chat_presence_lock:
            chat_presence[client_id] = now
            for stale_id in [key for key, seen_at in chat_presence.items() if now - seen_at > 10]:
                del chat_presence[stale_id]
            return len(chat_presence)

    def safe_path(subpath: str = "") -> Path:
        candidate = (root / subpath).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            abort(403)
        return candidate

    def directory_zip_chunks(directory: Path):
        """Build a ZIP archive incrementally without a temporary archive file."""
        output = StreamingZipBuffer()
        top_name = directory.name or "network_file"
        entries = sorted(directory.rglob("*"), key=lambda item: item.relative_to(directory).as_posix())
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            if not entries:
                archive.writestr(f"{top_name}/", b"")
                if data := output.drain():
                    yield data

            for entry in entries:
                # Do not package symlink targets: a link may expose a file outside the share.
                if entry.is_symlink():
                    continue
                try:
                    entry.resolve().relative_to(root)
                    relative = entry.relative_to(directory)
                except (OSError, ValueError):
                    continue
                archive_name = PurePosixPath(top_name, *relative.parts).as_posix()
                if entry.is_dir():
                    archive.writestr(f"{archive_name.rstrip('/')}/", b"")
                    if data := output.drain():
                        yield data
                    continue
                if not entry.is_file():
                    continue

                info = zipfile.ZipInfo.from_file(entry, archive_name, strict_timestamps=False)
                info.compress_type = zipfile.ZIP_STORED
                with entry.open("rb") as source, archive.open(info, "w", force_zip64=True) as member:
                    while block := source.read(1024 * 1024):
                        member.write(block)
                        if data := output.drain():
                            yield data
                if data := output.drain():
                    yield data

        if data := output.drain():
            yield data

    @app.before_request
    def require_login():
        if password and request.endpoint != "login" and not session.get("authorized"):
            if request.path.startswith("/api/"):
                return {"error": "登录已失效，请刷新页面后重新登录"}, 401
            if request.endpoint == "upload_stream":
                return Response("登录已失效，请刷新页面后重新登录", status=401, content_type="text/plain; charset=utf-8")
            return redirect(url_for("login", next=request.full_path))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = False
        if request.method == "POST":
            if secrets.compare_digest(request.form.get("password", ""), password or ""):
                session["authorized"] = True
                target = request.args.get("next", "/")
                if not target.startswith("/") or target.startswith("//"):
                    target = "/"
                return redirect(target)
            error = True
        return render_template_string(LOGIN_PAGE, error=error)

    @app.get("/")
    @app.get("/browse/")
    @app.get("/browse/<path:subpath>")
    def browse(subpath: str = ""):
        current_path = safe_path(subpath)
        if not current_path.is_dir():
            abort(404)
        items = []
        for entry in sorted(current_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                stat = entry.stat()
            except OSError:
                continue
            rel = entry.relative_to(root).as_posix()
            items.append({
                "name": entry.name, "rel": rel, "is_dir": entry.is_dir(),
                "size": "—" if entry.is_dir() else human_size(stat.st_size),
                "mtime": __import__("datetime").datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        parts = Path(subpath).parts if subpath else ()
        crumbs, built = [], []
        for part in parts:
            built.append(part)
            crumbs.append({"name": part, "path": "/".join(built)})
        parent = Path(subpath).parent.as_posix() if subpath else None
        if parent == ".":
            parent = ""
        return render_template_string(
            PAGE, items=items, current=subpath, crumbs=crumbs, parent=parent,
            message=request.args.get("message"), is_error=request.args.get("error") == "1",
        )

    @app.get("/chat")
    def chat():
        online_count = mark_chat_online(anonymous_identity())
        return render_template_string(CHAT_PAGE, online_count=online_count)

    @app.get("/api/chat/messages")
    def chat_messages():
        try:
            after = max(0, int(request.args.get("after", "0")))
        except ValueError:
            return {"error": "after 参数无效"}, 400
        online_count = mark_chat_online(anonymous_identity())
        items = [chat_store.public_message(item) for item in chat_store.after(after)]
        return {
            "messages": items,
            "latest_id": items[-1]["id"] if items else after,
            "online_count": online_count,
        }

    @app.post("/api/chat/messages")
    def send_chat_message():
        if request.content_length is not None and request.content_length > 16 * 1024:
            return {"error": "消息请求过大"}, 413
        payload = request.get_json(silent=True)
        message = payload.get("message", "") if isinstance(payload, dict) else ""
        if not isinstance(message, str):
            return {"error": "消息格式无效"}, 400
        message = message.strip()
        if not message:
            return {"error": "消息不能为空"}, 400
        if len(message) > 1000:
            return {"error": "消息不能超过 1000 个字符"}, 400

        client_id = anonymous_identity()
        mark_chat_online(client_id)
        now = time.monotonic()
        with chat_rate_lock:
            previous = chat_rate_limits.get(client_id, 0.0)
            if now - previous < 0.5:
                return {"error": "发送太快，请稍后再试"}, 429
            chat_rate_limits[client_id] = now
            if len(chat_rate_limits) > 10000:
                chat_rate_limits.clear()
                chat_rate_limits[client_id] = now

        item = chat_store.append(message)
        return {"message": item}, 201

    @app.post("/upload-stream/")
    @app.post("/upload-stream/<path:subpath>")
    def upload_stream(subpath: str = ""):
        """Write one raw request body directly to its final directory."""
        destination = safe_path(subpath)
        if not destination.is_dir():
            abort(404)

        relative = safe_relative_name(request.args.get("path", ""))
        if relative is None:
            return Response("文件路径无效", status=400, content_type="text/plain; charset=utf-8")

        target = safe_path((Path(subpath) / relative).as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            return Response("目标路径是一个文件夹", status=409, content_type="text/plain; charset=utf-8")

        temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.uploading")
        written = 0
        try:
            with temporary.open("xb", buffering=4 * 1024 * 1024) as output:
                while chunk := request.stream.read(4 * 1024 * 1024):
                    output.write(chunk)
                    written += len(chunk)
            if request.content_length is not None and written != request.content_length:
                return Response("上传数据不完整", status=400, content_type="text/plain; charset=utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

        return Response('{"ok":true}', content_type="application/json")

    @app.post("/upload/")
    @app.post("/upload/<path:subpath>")
    def upload(subpath: str = ""):
        destination = safe_path(subpath)
        if not destination.is_dir():
            abort(404)
        saved = 0
        uploads = [(item, False) for item in request.files.getlist("files")]
        uploads += [(item, True) for item in request.files.getlist("folders")]
        for uploaded, keep_path in uploads:
            relative = safe_relative_name(uploaded.filename or "") if keep_path else None
            if not keep_path:
                name = safe_name(uploaded.filename or "")
                relative = Path(name) if name else None
            if relative is None:
                continue
            target = safe_path((Path(subpath) / relative).as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            uploaded.save(target)
            saved += 1
        return redirect(url_for("browse", subpath=subpath, message=f"已上传 {saved} 个文件"))

    @app.post("/mkdir/")
    @app.post("/mkdir/<path:subpath>")
    def mkdir(subpath: str = ""):
        destination = safe_path(subpath)
        name = safe_name(request.form.get("name", ""))
        if not name:
            return redirect(url_for("browse", subpath=subpath, message="文件夹名称无效", error="1"))
        try:
            (destination / name).mkdir()
            message, error = f"已创建文件夹：{name}", None
        except FileExistsError:
            message, error = "同名文件或文件夹已存在", "1"
        return redirect(url_for("browse", subpath=subpath, message=message, error=error))

    @app.get("/download/<path:subpath>")
    def download(subpath: str):
        target = safe_path(subpath)
        if not target.is_file():
            abort(404)
        return send_file(target, as_attachment=True, download_name=target.name, conditional=True)

    @app.get("/download-folder/")
    @app.get("/download-folder/<path:subpath>")
    def download_folder(subpath: str = ""):
        target = safe_path(subpath)
        if not target.is_dir():
            abort(404)
        archive_name = f"{target.name or 'network_file'}.zip"
        encoded_name = quote(archive_name, safe="")
        return Response(
            directory_zip_chunks(target),
            content_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=folder.zip; filename*=UTF-8''{encoded_name}",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
            direct_passthrough=True,
        )

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return Response(f"上传内容超过 {max_upload_mb} MB 限制", status=413, content_type="text/plain; charset=utf-8")

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在局域网中共享文件并进行匿名聊天")
    parser.add_argument("--dir", type=Path, default=Path(__file__).parent / "network_file", help="共享目录")
    parser.add_argument("--logs-dir", type=Path, default=Path(__file__).parent / "logs", help="聊天记录目录")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--password", default=os.environ.get("FILE_SHARE_PASSWORD"), help="可选访问口令")
    parser.add_argument("--max-upload-mb", type=int, default=20480, help="单次请求上传大小上限")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app = create_app(args.dir, args.password, args.max_upload_mb, args.logs_dir)
    print(f"共享目录: {args.dir.resolve()}")
    print(f"聊天记录: {(args.logs_dir / 'chat.jsonl').resolve()}")
    print(f"访问地址: http://<本机局域网IP>:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)
