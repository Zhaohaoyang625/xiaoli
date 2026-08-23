# ============================================
# 看照片单测（2026-08-23）
# DeepSeek V4-Flash-Vision-Exp 视觉模型：路径检测 + base64 内联格式 + 失败兜底
# 不碰真实 API（mock llm.get_client，走统一 client 工厂）
# ============================================

import base64
import os
import tempfile
import unittest
from unittest import mock

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
    def _fake_client(self, reply="哇！这猫猫好可爱喔～你在哪拍的呀？"):
        fake_resp = mock.Mock()
        fake_resp.choices = [mock.Mock()]  # choices 是真实列表才能下标
        fake_resp.choices[0].message.content = reply
        client = mock.Mock()
        client.chat.completions.create.return_value = fake_resp
        return client

    def test_sends_vision_model_with_base64(self):
        """格式验证：视觉模型 + content 数组（text + image_url data URL，base64 正确）"""
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8fake-jpeg-bytes")
            path = f.name
        try:
            client = self._fake_client()
            with mock.patch.object(llm, "get_client", return_value=client):
                reply = vision.look_at_photo(path)
            self.assertEqual(reply, "哇！这猫猫好可爱喔～你在哪拍的呀？")
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
        finally:
            os.remove(path)

    def test_mime_map(self):
        self.assertEqual(vision._mime_fmt("a.jpg"), "jpeg")
        self.assertEqual(vision._mime_fmt("A.PNG"), "png")
        self.assertEqual(vision._mime_fmt("x.webp"), "webp")
        self.assertEqual(vision._mime_fmt("x.gif"), "gif")

    def test_api_failure_returns_none(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG fake")
            path = f.name
        try:
            client = mock.Mock()
            client.chat.completions.create.side_effect = RuntimeError("网络炸了")
            with mock.patch.object(llm, "get_client", return_value=client):
                self.assertIsNone(vision.look_at_photo(path))
        finally:
            os.remove(path)

    def test_huge_file_rejected(self):
        """超 20MB → None（不白花钱传大文件）"""
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8" * (11 * 1024 * 1024))  # 22MB
            path = f.name
        try:
            with mock.patch.object(llm, "get_client", side_effect=AssertionError("不该调用")):
                self.assertIsNone(vision.look_at_photo(path))
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
