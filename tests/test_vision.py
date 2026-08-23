# ============================================
# 看照片单测（2026-08-23）
# DeepSeek V4-Flash-Vision-Exp 视觉模型：路径检测 + base64 内联格式 + 失败兜底
# 不碰真实 API（mock llm.get_client，走统一 client 工厂）
# ============================================

import base64
import os
import queue
import tempfile
import unittest
from unittest import mock

from xiaoli import chat as chat_mod  # WebBridge 端点测试用
from xiaoli import config, llm, vision


class TestIsPhotoPath(unittest.TestCase):
    def test_recognizes_image_path(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            path = f.name
        try:
            self.assertTrue(vision.is_photo_path(path))
        finally:
            os.remove(path)

    def test_quoted_path_ok(self):
        """终端拖入文件有时带引号 → 剥引号再判断"""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            self.assertTrue(vision.is_photo_path(f'"{path}"'))
            self.assertTrue(vision.is_photo_path(f"'{path}'"))
        finally:
            os.remove(path)

    def test_non_existing_false(self):
        self.assertFalse(vision.is_photo_path(r"C:\不存在的目录\cat.jpg"))

    def test_non_image_ext_false(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"hi")
            path = f.name
        try:
            self.assertFalse(vision.is_photo_path(path))
        finally:
            os.remove(path)

    def test_empty_false(self):
        self.assertFalse(vision.is_photo_path(""))
        self.assertFalse(vision.is_photo_path("  "))


class TestLookAtPhoto(unittest.TestCase):
    def _fake_client(self, reply='{"reply": "哇！这猫猫好可爱喔～你在哪拍的呀？", "memory": "他的猫是橘猫"}',
                     content=None):
        fake_resp = mock.Mock()
        fake_resp.choices = [mock.Mock()]  # choices 是真实列表才能下标
        fake_resp.choices[0].message.content = content if content is not None else reply
        client = mock.Mock()
        client.chat.completions.create.return_value = fake_resp
        return client

    def _make_img(self, suffix=".jpg", data=b"\xff\xd8fake-jpeg-bytes"):
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            path = f.name
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_sends_vision_model_with_base64(self):
        """格式验证：视觉模型 + content 数组（text + image_url data URL，base64 正确）"""
        path = self._make_img()
        client = self._fake_client()
        with mock.patch.object(llm, "get_client", return_value=client):
            reply, memory = vision.look_at_photo(path)
        self.assertEqual(reply, "哇！这猫猫好可爱喔～你在哪拍的呀？")
        self.assertEqual(memory, "他的猫是橘猫")
        call = client.chat.completions.create.call_args
        kwargs = call.kwargs
        self.assertEqual(kwargs["model"], config.DEEPSEEK_VISION_MODEL)
        content = kwargs["messages"][1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        url = content[1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), b"\xff\xd8fake-jpeg-bytes")
        self.assertEqual(kwargs["messages"][0]["role"], "system")

    def test_empty_memory(self):
        """memory 空字符串 → ("", 不写记忆)"""
        path = self._make_img()
        client = self._fake_client(content='{"reply": "好美的海！", "memory": ""}')
        with mock.patch.object(llm, "get_client", return_value=client):
            reply, memory = vision.look_at_photo(path)
        self.assertEqual((reply, memory), ("好美的海！", ""))

    def test_fenced_json(self):
        """模型带 ``` 围栏 → 截取 {..} 解析"""
        path = self._make_img()
        client = self._fake_client(
            content='```json\n{"reply": "这是哪里呀？", "memory": "他去了海边"}\n```')
        with mock.patch.object(llm, "get_client", return_value=client):
            reply, memory = vision.look_at_photo(path)
        self.assertEqual((reply, memory), ("这是哪里呀？", "他去了海边"))

    def test_bad_json_fallback_to_plain_text(self):
        """模型没按 JSON 输出 → 整体当她的话，无记忆"""
        path = self._make_img()
        client = self._fake_client(content="哇这也太可爱了吧！")
        with mock.patch.object(llm, "get_client", return_value=client):
            reply, memory = vision.look_at_photo(path)
        self.assertEqual((reply, memory), ("哇这也太可爱了吧！", ""))

    def test_empty_content_none(self):
        path = self._make_img()
        client = self._fake_client(content="   ")
        with mock.patch.object(llm, "get_client", return_value=client):
            reply, memory = vision.look_at_photo(path)
        self.assertIsNone(reply)

    def test_mime_map(self):
        self.assertEqual(vision._mime_fmt("a.jpg"), "jpeg")
        self.assertEqual(vision._mime_fmt("A.PNG"), "png")
        self.assertEqual(vision._mime_fmt("x.webp"), "webp")
        self.assertEqual(vision._mime_fmt("x.gif"), "gif")

    def test_api_failure_returns_none(self):
        path = self._make_img(suffix=".png", data=b"\x89PNG fake")
        client = mock.Mock()
        client.chat.completions.create.side_effect = RuntimeError("网络炸了")
        with mock.patch.object(llm, "get_client", return_value=client):
            reply, memory = vision.look_at_photo(path)
        self.assertIsNone(reply)
        self.assertEqual(memory, "")

    def test_huge_file_rejected(self):
        """超 20MB → None（不白花钱传大文件）"""
        path = self._make_img(data=b"\xff\xd8" * (11 * 1024 * 1024))  # 22MB
        with mock.patch.object(llm, "get_client", side_effect=AssertionError("不该调用")):
            reply, memory = vision.look_at_photo(path)
        self.assertIsNone(reply)
        self.assertEqual(memory, "")


class TestHandlePhoto(unittest.TestCase):
    """主循环照片分支（handle_photo）：日记两条 + 记忆写入 + 失败兜底"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # 挂模块级状态（main() 里是 global，测试里直接设）
        chat_mod.diary = {"messages": [], "summary": "", "daily": {}}
        chat_mod.facts = []
        self._saved = chat_mod.diary, chat_mod.facts

    def tearDown(self):
        chat_mod.diary, chat_mod.facts = self._saved

    def _run(self, see="這貓貓好可愛！", memory=""):
        with mock.patch.object(chat_mod.vision, "look_at_photo", return_value=(see, memory)), \
             mock.patch.object(chat_mod, "say_with_continuation"), \
             mock.patch.object(chat_mod.context, "compress",
                               side_effect=lambda d: d), \
             mock.patch.object(chat_mod.context, "save_diary"), \
             mock.patch.object(chat_mod.proactive, "mark_activity"), \
             mock.patch.object(chat_mod.memory_mod, "save_facts"):
            chat_mod.handle_photo(r"C:\fake\cat.jpg")

    def test_reply_saved_to_diary(self):
        """她的话进日记；照片内容不进日记（只有痕迹）"""
        self._run(see="這隻貓貓好可愛！你在哪拍的呀？")
        msgs = chat_mod.diary["messages"]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"], "【图片】他发了一张照片")
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(msgs[1]["content"], "這隻貓貓好可愛！你在哪拍的呀？")

    def test_memory_written_when_notable(self):
        """照片里有值得记住的事 → 写档案"""
        self._run(memory="他新养的橘猫叫毛毛")
        self.assertEqual(len(chat_mod.facts), 1)
        self.assertIn("毛毛", chat_mod.facts[0]["content"])
        self.assertEqual(chat_mod.facts[0]["importance"], 5)

    def test_no_memory_when_empty(self):
        self._run(memory="")
        self.assertEqual(chat_mod.facts, [])

    def test_fallback_when_vision_fails(self):
        """视觉失败 → 兜底话照说照记"""
        self._run(see=None, memory="")
        self.assertEqual(chat_mod.diary["messages"][1]["content"], "齁…这张照片人家打不开捏，你换个图试试？")


class TestPhotoEndpoint(unittest.TestCase):
    """WebBridge /photo 端点：鉴权 + 魔数校验 + 存收件箱 + 进输入队列"""

    def _handler(self, body=b"", ct="image/jpeg"):
        h = chat_mod.WebBridge.__new__(chat_mod.WebBridge)
        h.path = "/photo?token=secret123"
        h.headers = {"Content-Length": str(len(body)), "Content-Type": ct}
        h.rfile = mock.Mock()
        h.rfile.read.return_value = body
        return h

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(chat_mod.paths, "DATA_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def test_no_token_forbidden(self):
        h = self._handler(b"x")
        h.path = "/photo"  # 无 token
        with mock.patch.object(chat_mod, "_bridge_token", "secret123"):
            resp = {}
            with mock.patch.object(chat_mod.WebBridge, "_json",
                                   side_effect=lambda d, c=200: resp.update({"body": d, "code": c})):
                chat_mod.WebBridge.do_POST(h)
        self.assertEqual(resp["code"], 403)

    def test_bad_magic_rejected(self):
        """不是真图片（文本内容）→ 400"""
        h = self._handler(b"hello world, this is not an image")
        with mock.patch.object(chat_mod, "_bridge_token", "secret123"):
            resp = {}
            with mock.patch.object(chat_mod.WebBridge, "_json",
                                   side_effect=lambda d, c=200: resp.update({"body": d, "code": c})):
                chat_mod.WebBridge.do_POST(h)
        self.assertEqual(resp["code"], 400)

    def test_too_large_rejected(self):
        h = self._handler(b"\xff\xd8" * 100)
        h.headers = {"Content-Length": str(21 * 1024 * 1024), "Content-Type": "image/jpeg"}
        with mock.patch.object(chat_mod, "_bridge_token", "secret123"):
            resp = {}
            with mock.patch.object(chat_mod.WebBridge, "_json",
                                   side_effect=lambda d, c=200: resp.update({"body": d, "code": c})):
                chat_mod.WebBridge.do_POST(h)
        self.assertEqual(resp["code"], 413)

    def test_photo_saved_and_queued(self):
        """正常照片 → 存进 data/inbox/ + 路径进输入队列（主循环 vision 分支自然处理）"""
        h = self._handler(b"\xff\xd8real-jpeg")
        q = queue.Queue()
        with mock.patch.object(chat_mod, "_bridge_token", "secret123"), \
             mock.patch.object(chat_mod, "input_queue", q):
            resp = {}
            with mock.patch.object(chat_mod.WebBridge, "_json",
                                   side_effect=lambda d, c=200: resp.update({"body": d, "code": c})):
                chat_mod.WebBridge.do_POST(h)
        self.assertTrue(resp["body"]["ok"])
        src, path = q.get_nowait()
        self.assertEqual(src, "web")
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(path.startswith(os.path.join(self.tmp, "inbox")))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"\xff\xd8real-jpeg")
        os.remove(path)

    def test_png_ext_by_magic(self):
        h = self._handler(b"\x89PNG\r\n\x1a\nfake", ct="image/png")
        q = queue.Queue()
        with mock.patch.object(chat_mod, "_bridge_token", "secret123"), \
             mock.patch.object(chat_mod, "input_queue", q):
            with mock.patch.object(chat_mod.WebBridge, "_json"):
                chat_mod.WebBridge.do_POST(h)
        _, path = q.get_nowait()
        self.assertTrue(path.endswith(".png"))
        os.remove(path)


if __name__ == "__main__":
    unittest.main()
