# 局域网文件共享

将文件放入 `network_file` 文件夹后，同一局域网的手机或电脑可以用浏览器查看和下载，也可以上传文件、创建子文件夹。

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

注意：程序默认监听所有网卡，同一网络中的设备都可能访问。请勿在共享目录放置敏感文件；公网使用时应增加 HTTPS 和更完善的身份认证。
