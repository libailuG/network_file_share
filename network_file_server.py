#!/usr/bin/env python3
"""A small browser-based file share for trusted local networks."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

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
  <h1>📁 局域网文件共享</h1>
  <div class="bar muted">当前位置：<a href="{{ url_for('browse') }}">根目录</a>
  {% for crumb in crumbs %} / <a href="{{ url_for('browse', subpath=crumb.path) }}">{{ crumb.name }}</a>{% endfor %}</div>

  {% if parent is not none %}<p><a href="{{ url_for('browse', subpath=parent) }}">⬅ 返回上级目录</a></p>{% endif %}

  <div class="card">
    <form action="{{ url_for('upload', subpath=current) }}" method="post" enctype="multipart/form-data">
      <input type="file" name="files" multiple required>
      <button type="submit">上传文件</button>
    </form>
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
        <td>{% if not item.is_dir %}<a href="{{ url_for('download', subpath=item.rel) }}">下载</a>{% else %}—{% endif %}</td>
      </tr>
    {% else %}<tr><td colspan="4" class="muted">这个文件夹是空的</td></tr>{% endfor %}
    </tbody>
  </table>
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


def create_app(root: Path, password: str | None, max_upload_mb: int) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FILE_SHARE_SECRET", secrets.token_hex(32))
    app.config["MAX_CONTENT_LENGTH"] = max_upload_mb * 1024 * 1024
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    def safe_path(subpath: str = "") -> Path:
        candidate = (root / subpath).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            abort(403)
        return candidate

    @app.before_request
    def require_login():
        if password and request.endpoint != "login" and not session.get("authorized"):
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

    @app.post("/upload/")
    @app.post("/upload/<path:subpath>")
    def upload(subpath: str = ""):
        destination = safe_path(subpath)
        if not destination.is_dir():
            abort(404)
        saved = 0
        for uploaded in request.files.getlist("files"):
            name = safe_name(uploaded.filename or "")
            if not name:
                continue
            uploaded.save(destination / name)
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

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return Response(f"上传内容超过 {max_upload_mb} MB 限制", status=413, content_type="text/plain; charset=utf-8")

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在局域网中通过浏览器上传和下载文件")
    parser.add_argument("--dir", type=Path, default=Path(__file__).parent / "network_file", help="共享目录")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--password", default=os.environ.get("FILE_SHARE_PASSWORD"), help="可选访问口令")
    parser.add_argument("--max-upload-mb", type=int, default=2048, help="单次请求上传大小上限")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app = create_app(args.dir, args.password, args.max_upload_mb)
    print(f"共享目录: {args.dir.resolve()}")
    print(f"访问地址: http://<本机局域网IP>:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)
