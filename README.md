# 局域网文件共享与匿名聊天

轻量局域网文件共享工具，使用 Flask 提供网页服务，使用 PyQt6 提供 Windows 控制窗口与系统托盘。
支持浏览、上传下载文件、保留目录结构的文件夹上传、实时 ZIP 文件夹下载，以及匿名聊天。

## Windows 便携软件

适用于 Windows 10/11 64 位。将便携包完整解压到可写文件夹，双击 `LAN_File_Share.exe` 即可运行，无需安装 Python、conda 或 Qt。

- `data/`：共享文件目录，位于 EXE 同目录。
- `logs/`：运行日志和 `chat.jsonl` 聊天记录。
- `config.json`：运行配置；单独复制 EXE 时会自动创建默认配置。
- 程序显示本机地址与局域网地址，并自动打开浏览器；其他设备连接同一局域网后使用窗口显示的地址。
- 最小化、点击“最小化到托盘”或关闭窗口后，服务继续运行。点击托盘图标恢复，右键菜单可打开网页、复制地址或“停止并退出”。
- 如系统托盘不可用，最小化保留在任务栏，关闭窗口则停止服务。
- 同一程序目录只允许运行一个实例。
- “开机启动”默认开启，首次正常运行时注册当前用户的 Windows 登录启动项，无需管理员权限。取消勾选立即关闭并保存，下次启动仍保持关闭。
- 开机启动时直接进入托盘，不弹出浏览器；移动 EXE 后手动启动一次会更新启动路径。

默认监听 `0.0.0.0:8000`，默认无访问口令。修改 `config.json` 后重启生效：

```json
{
  "host": "0.0.0.0",
  "port": 8000,
  "password": "",
  "max_upload_mb": 20480,
  "auto_start": true
}
```

支持中文口令。默认单次请求上传上限为 20 GB。
桌面软件读取 `config.json`；命令行服务使用下方的命令行参数或环境变量。

若其他设备无法连接，可右键 `enable_firewall.bat` 选择“以管理员身份运行”。该脚本只放行本地子网对同目录 EXE 指定 TCP 端口的访问，适用于专用和公用网络，不关闭防火墙。移动程序或修改端口后需重新运行。

## 从源码运行

建议使用 Python 3.12。在已激活的 Python 环境中安装依赖：

```powershell
python -m pip install -r requirements.txt
python network_file_share_windows.py
```

使用 conda 时，例如：

```powershell
conda activate pyqt
python -m pip install -r requirements.txt
python network_file_share_windows.py
```

Windows 也可在已激活环境的终端中运行 `start.bat`。脚本使用当前环境，不依赖特定用户名或安装路径。

仅运行网页服务：

```powershell
python network_file_server.py --dir ./data --logs-dir ./logs
```

命令行入口默认共享目录同样为脚本同目录的 `data/`。使用 `start_server.bat` 也可启动；按 Ctrl+C 停止。

自定义端口、共享目录、聊天日志和上传上限：

```powershell
python network_file_server.py --port 9000 --dir ./data --logs-dir ./logs --max-upload-mb 4096
```

设置访问口令：

```powershell
$env:FILE_SHARE_PASSWORD = "访问口令"
python network_file_server.py
```

`--password` 参数也可指定口令。

## 文件传输

- 网页使用原始请求体分块写入临时文件，完成后提交，减少大文件的额外暂存与复制。multipart 兼容接口同样先写临时文件。
- 同名上传返回 HTTP 409，不覆盖已有文件；请重命名后重试。
- 文件夹上传保留目录结构。浏览器不提供没有文件的空文件夹，因此空文件夹不会被上传。
- 含大文件时网页顺序上传，小文件最多使用三个并发连接。
- 目录下载边打包 ZIP 边发送，不生成完整临时 ZIP，默认仅打包、不压缩。
- 校验目录边界，并拒绝 Windows 盘符、设备名称和备用数据流名称。

## 匿名聊天

网页每 1.5 秒同步新消息。最近 10 秒有心跳的浏览器会话计为在线，同一会话只计一次。
消息不包含用户名，历史记录追加到 `logs/chat.jsonl`；服务重启会恢复历史。
每条消息最多 1000 个字符，同一浏览器两次发送至少间隔 0.5 秒。
内存保留最近 5000 条，首次进入展示最近 200 条。同步游标与即时显示分开，避免自己发送消息时跳过尚未拉取的其他消息。

## Windows 打包

在 Windows 上激活准备好的 Python 环境，然后运行：

```powershell
python -m pip install -r requirements.txt "pyinstaller>=6,<7"
python build_exe.py
```

或在该终端运行 `build_windows.bat`。生成的 `LAN_File_Share.exe` 位于项目根目录。
构建会隔离 PATH 中其他工具的 DLL，避免混入与 Qt 不兼容的 ICU 或 OpenSSL。
重新构建前，请通过托盘菜单退出正在运行的 EXE。

分发时只需 EXE；也可附带默认配置、空 `data/`、防火墙脚本和说明。
不要把自己的共享文件、本机口令配置或聊天日志放进分发包。
Windows EXE 应在 Windows 环境中构建。

## 测试

```powershell
python -m unittest -v test
```

验证 Qt 窗口、真实上传下载、托盘隐藏恢复和退出：

```powershell
python network_file_share_windows.py --self-test report.json
```

打包版本也支持同样的自检，测试使用临时目录，不修改用户共享文件：

```powershell
.\LAN_File_Share.exe --self-test report.json
```

托盘相关检查仅在系统托盘可用时执行。

## 核心文件

| 文件 | 作用 |
| --- | --- |
| `network_file_server.py` | 文件服务、聊天存储、网页模板与前端脚本 |
| `network_file_share_windows.py` | Qt 主窗口、系统托盘、配置读取与服务生命周期 |
| `build_exe.py` | 独立 EXE 构建与依赖路径隔离 |
| `test.py` | 自动化测试 |
| `windows_config.example.json` | 默认配置示例 |
| `enable_firewall.ps1` | 本地子网防火墙规则 |

程序面向可信局域网，默认监听所有网卡。共享目录不应放置敏感文件；公网部署需要 HTTPS 和更完善的认证及资源控制。
