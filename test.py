import json
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from network_file_server import create_app, safe_name
from network_file_share_windows import load_config, save_config


class FileShareChatTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.share = base / "share"
        self.logs = base / "logs"
        self.app = create_app(self.share, None, 1, self.logs)
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary.cleanup()

    def test_unicode_password_login_and_wrong_unicode_input(self):
        protected = create_app(self.share, "中文口令🔒", 1, self.logs)
        protected.testing = True
        client = protected.test_client()
        self.assertEqual(client.post('/login', data={'password': '错误口令'}).status_code, 200)
        self.assertEqual(client.get('/api/chat/messages').status_code, 401)
        self.assertEqual(client.post('/login', data={'password': '中文口令🔒'}).status_code, 302)
        self.assertEqual(client.get('/api/chat/messages').status_code, 200)

    def test_windows_drive_and_device_names_are_rejected(self):
        for name in ('D:escape', 'file:stream', 'CON', 'nul.txt', 'COM1', 'LPT9.txt'):
            with self.subTest(name=name):
                self.assertEqual(safe_name(name), '')
                response = self.client.post('/mkdir/', data={'name': name})
                self.assertEqual(response.status_code, 302)
                self.assertIn('error=1', response.headers['Location'])
                self.assertEqual(self.client.post('/upload-stream/', query_string={'path': name}, data=b'x').status_code, 400)

    def test_duplicate_upload_does_not_destroy_existing_file(self):
        self.assertEqual(self.client.post('/upload-stream/?path=original.txt', data=b'original').status_code, 200)
        response = self.client.post('/upload-stream/?path=original.txt', data=b'changed')
        self.assertEqual(response.status_code, 409)
        response = self.client.post('/upload/', data={'files': (io.BytesIO(b'changed'), 'original.txt')})
        self.assertEqual(response.status_code, 409)
        self.assertEqual((self.share / 'original.txt').read_bytes(), b'original')
        self.assertFalse(list(self.share.glob('*.uploading')))

    def test_upload_limit_leaves_no_partial_file(self):
        response = self.client.post('/upload-stream/?path=large.bin', data=b'x' * (1024 * 1024 + 1))
        self.assertEqual(response.status_code, 413)
        self.assertFalse((self.share / 'large.bin').exists())
        self.assertFalse(list(self.share.glob('*.uploading')))

    def test_multipart_upload_preserves_nested_folder(self):
        response = self.client.post('/upload/', data={'folders': (io.BytesIO(b'content'), 'folder/nested/file.txt')})
        self.assertEqual(response.status_code, 302)
        self.assertEqual((self.share / 'folder/nested/file.txt').read_bytes(), b'content')

    def test_file_upload_download_and_traversal_protection(self):
        response = self.client.post(
            "/upload-stream/?path=hello.txt",
            data=b"hello",
            content_type="application/octet-stream",
        )
        self.assertEqual(response.status_code, 200)
        download = self.client.get("/download/hello.txt")
        self.assertEqual(download.data, b"hello")
        download.close()
        self.assertEqual(self.client.get("/download/../etc/passwd").status_code, 403)

    def test_folder_download_streams_zip_with_nested_and_empty_directories(self):
        folder = self.share / "中文资料"
        (folder / "子目录").mkdir(parents=True)
        (folder / "空目录").mkdir()
        (folder / "说明.txt").write_text("你好", encoding="utf-8")
        (folder / "子目录" / "data.bin").write_bytes(b"\x00\x01\x02")

        response = self.client.get("/download-folder/%E4%B8%AD%E6%96%87%E8%B5%84%E6%96%99")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/zip")
        self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            self.assertEqual(archive.read("中文资料/说明.txt").decode("utf-8"), "你好")
            self.assertEqual(archive.read("中文资料/子目录/data.bin"), b"\x00\x01\x02")
            self.assertIn("中文资料/空目录/", archive.namelist())
        response.close()

        self.assertEqual(self.client.get("/download-folder/../outside").status_code, 403)

    def test_folder_download_link_is_shown_for_directories(self):
        (self.share / "folder").mkdir(parents=True)
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("下载当前文件夹（ZIP）", page)
        self.assertIn("下载文件夹", page)

    def test_chat_message_is_logged_and_returned(self):
        self.client.get("/chat")
        response = self.client.post("/api/chat/messages", json={"message": "你好，局域网"})
        self.assertEqual(response.status_code, 201)
        sent = response.get_json()["message"]
        self.assertNotIn("name", sent)
        self.assertEqual(sent["message"], "你好，局域网")

        history = self.client.get("/api/chat/messages?after=0").get_json()["messages"]
        self.assertEqual(history, [sent])
        logged = json.loads((self.logs / "chat.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(logged, sent)

    def test_chat_history_survives_app_restart(self):
        self.client.get("/chat")
        sent = self.client.post("/api/chat/messages", json={"message": "持久化消息"}).get_json()["message"]

        restarted = create_app(self.share, None, 1, self.logs)
        restarted.testing = True
        history = restarted.test_client().get("/api/chat/messages?after=0").get_json()["messages"]
        self.assertEqual(history, [sent])

    def test_chat_validation_and_rate_limit(self):
        self.assertEqual(self.client.post("/api/chat/messages", json={"message": "   "}).status_code, 400)
        self.assertEqual(self.client.post("/api/chat/messages", json={"message": "x" * 1001}).status_code, 400)
        self.assertEqual(self.client.post("/api/chat/messages", json={"message": "第一条"}).status_code, 201)
        self.assertEqual(self.client.post("/api/chat/messages", json={"message": "第二条"}).status_code, 429)

    def test_password_also_protects_chat_api(self):
        protected = create_app(self.share, "secret", 1, self.logs)
        protected.testing = True
        client = protected.test_client()
        response = client.get("/api/chat/messages")
        self.assertEqual(response.status_code, 401)
        self.assertIn("登录已失效", response.get_json()["error"])

    def test_chat_reports_anonymous_online_count(self):
        first = self.app.test_client()
        second = self.app.test_client()
        self.assertEqual(first.get("/api/chat/messages").get_json()["online_count"], 1)
        self.assertEqual(second.get("/api/chat/messages").get_json()["online_count"], 2)
        self.assertEqual(first.get("/api/chat/messages").get_json()["online_count"], 2)


class WindowsLauncherTests(unittest.TestCase):
    def test_autostart_defaults_enabled_and_remembers_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.assertIs(load_config(base)['auto_start'], True)
            (base / 'config.json').write_text('{"port": 9000, "password": "existing", "custom": "keep"}', encoding='utf-8')
            config = load_config(base)
            config['auto_start'] = False
            save_config(base, config)
            restarted = load_config(base)
            self.assertIs(restarted['auto_start'], False)
            self.assertEqual(restarted['password'], 'existing')
            self.assertEqual(restarted['port'], 9000)
            self.assertEqual(json.loads((base / 'config.json').read_text(encoding='utf-8'))['custom'], 'keep')

    def test_autostart_rejects_non_boolean_config(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'config.json').write_text('{"auto_start": "false"}', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_config(base)

    def test_windows_config_defaults_and_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.assertEqual(load_config(base)["port"], 8000)
            (base / "config.json").write_text(
                json.dumps({"port": 9000, "password": "secret", "max_upload_mb": 512}),
                encoding="utf-8",
            )
            config = load_config(base)
            self.assertEqual(config["port"], 9000)
            self.assertEqual(config["password"], "secret")
            self.assertEqual(config["max_upload_mb"], 512)

    def test_windows_config_rejects_invalid_port(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config.json").write_text('{"port": 70000}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(base)


if __name__ == "__main__":
    unittest.main()
