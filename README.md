# 局域网文件共享与匿名聊天

将文件放入 `network_file` 文件夹后，同一局域网的手机或电脑可以用浏览器查看和下载，也可以上传文件、上传整个文件夹、下载整个文件夹、创建子文件夹。

首页右上角可进入匿名聊天室。聊天室不显示或记录用户名称，只展示消息内容、时间和当前匿名在线人数，并且每 1.5 秒同步新消息。最近 10 秒内仍有聊天室心跳的浏览器会话计为在线，每个会话只计一次。聊天记录以 JSON Lines 格式追加保存在 `logs/chat.jsonl`，服务重启后会自动加载最近的历史记录。

网页中的“选择文件夹”会保留文件夹内的目录结构。Chrome、Edge 等现代浏览器支持这一功能；受浏览器机制限制，不包含文件的空文件夹不会被上传。

网页上传使用数据流直接写盘：大文件不会先完整写入 Flask 临时文件再复制，能够减少磁盘写入和上传完成后的等待。大文件顺序写入，大量小文件会自动使用最多三个并发连接。

目录列表中的“下载文件夹”会将所选目录实时打包为 ZIP 下载；“下载当前文件夹”可以下载当前正在浏览的整个目录。ZIP 数据边打包边发送，不会在服务器上额外生成一个完整的临时压缩包。为优先保证局域网传输速度，ZIP 默认只打包、不压缩文件内容。

## 启动

```bash
python -m pip install -r requirements.txt
python network_file_server.py
```

查看本机局域网 IP：

```bash
hostname -I
```

假设本机 IP 是 `192.168.1.20`，其他设备访问：

```text
http://192.168.1.20:8000
```

两台设备需要连接到同一局域网；若无法访问，请允许防火墙放行 TCP 8000 端口。

## Windows 双击版

Windows 10/11 可以生成无需安装 Python、双击即用的独立程序。第一次构建需要在
Windows 上安装 Python 3.11 或 3.12，然后双击：

```text
build_windows.bat
```

构建完成后程序位于：

```text
dist\LAN_File_Share.exe
```

把该 EXE 放进一个可写文件夹后双击。程序会自动创建同目录下的 `network_file` 和
`logs`，显示本机与局域网地址，并自动打开浏览器。关闭控制窗口时服务会一起停止。

Windows 防火墙第一次询问时需要允许“专用网络”访问，否则其他局域网设备无法连接。

如需更改端口、口令或上传上限，把 `windows_config.example.json` 复制到 EXE 同目录，
重命名为 `config.json` 后修改。口令以明文保存在本机配置文件中；完全可信的局域网可以
保持为空。

注意：Windows EXE 必须在 Windows 环境中构建。PyInstaller 会打包当前操作系统的
Python，无法在 Linux 上直接生成可靠的 Windows 可执行文件。

## 可选配置

设置访问口令（推荐在非完全可信的局域网使用）：

```bash
python network_file_server.py --password "你的口令"
```

也可以使用环境变量，避免口令出现在命令历史中：

```bash
FILE_SHARE_PASSWORD="你的口令" python network_file_server.py
```

更改端口、目录或单次上传上限：

```bash
python network_file_server.py --port 9000 --dir /path/to/share --max-upload-mb 4096
```

更改聊天记录目录：

```bash
python network_file_server.py --logs-dir /path/to/logs
```

聊天消息最多 1000 个字符。为防止误刷屏，同一浏览器两次发送至少间隔 0.5 秒。程序启动时最多在内存中保留最近 5000 条历史记录，首次打开页面显示最近 200 条；完整记录仍保存在 `chat.jsonl` 中。

默认每个文件的大小上限为 20 GB。可通过 `--max-upload-mb` 调整；不建议设置得超过共享目录所在文件系统的单文件大小限制。

注意：程序默认监听所有网卡，同一网络中的设备都可能访问。请勿在共享目录放置敏感文件；公网使用时应增加 HTTPS 和更完善的身份认证。
