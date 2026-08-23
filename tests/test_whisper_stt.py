# ============================================
# 本地识别 whisper_stt 单测（2026-08-23 补缺口）
# 降级逻辑：STT_LOCAL 关/模型缺失/加载失败/识别异常 → None（stt.py 降级火山）
# 不碰真模型、不占显存——全部 mock
# ============================================

import os
import unittest
from unittest import mock

from xiaoli import whisper_stt

# 预导入 faster_whisper（真模型加载一次）：
# 测试里 patch 全局 os.path.isdir 期间若再触发 transformers 导入，
# 它的目录探测全返回 True → 无限递归 RecursionError（2026-08-23 实测踩坑）。
# 预导入后 sys.modules 有缓存，_load() 里的 from import 只取属性不重导。
import faster_whisper  # noqa: E402


class TestConfigGate(unittest.TestCase):
    """开关/就绪检查（不碰模型）"""

    def test_disabled_returns_none(self):
        """STT_LOCAL=False → 不加载模型直接 None"""
        with mock.patch.object(whisper_stt.config, "STT_LOCAL", False), \
             mock.patch.object(whisper_stt, "_load", return_value=True) as m:
            self.assertIsNone(whisper_stt.transcribe(b"\x00" * 320))
            m.assert_not_called()

    def test_load_false_returns_none(self):
        with mock.patch.object(whisper_stt.config, "STT_LOCAL", True), \
             mock.patch.object(whisper_stt, "_load", return_value=False):
            self.assertIsNone(whisper_stt.transcribe(b"\x00" * 320))


class TestLoad(unittest.TestCase):
    def setUp(self):
        self._saved_model = whisper_stt._model
        whisper_stt._model = None
        whisper_stt._loading = False

    def tearDown(self):
        whisper_stt._model = self._saved_model
        whisper_stt._loading = False

    def test_already_loaded(self):
        whisper_stt._model = object()
        self.assertTrue(whisper_stt._load())

    @mock.patch("xiaoli.whisper_stt.os.path.isdir", return_value=False)
    def test_model_missing_prints_and_false(self, m_isdir):
        self.assertFalse(whisper_stt._load())

    def _patch_whisper(self, side_effect=None):
        return mock.patch.object(faster_whisper, "WhisperModel",
                                 side_effect=side_effect)

    @mock.patch("xiaoli.whisper_stt._add_nvidia_dlls")
    @mock.patch("xiaoli.whisper_stt.os.path.isdir", return_value=True)
    def test_load_success(self, m_isdir, m_dll):
        with self._patch_whisper() as m_wm:
            self.assertTrue(whisper_stt._load())
            m_wm.assert_called_once()
            self.assertIsNotNone(whisper_stt._model)

    @mock.patch("xiaoli.whisper_stt.os.path.isdir", return_value=True)
    def test_load_failure_resets_and_false(self, m_isdir):
        with self._patch_whisper(side_effect=RuntimeError("no kernel image")):
            self.assertFalse(whisper_stt._load())
        self.assertIsNone(whisper_stt._model)
        self.assertFalse(whisper_stt._loading)  # finally 复位

    def test_concurrent_loading_returns_false(self):
        """正在加载时再调 → False（不重复加载，直接降级）"""
        whisper_stt._loading = True
        self.assertFalse(whisper_stt._load())


class TestTranscribe(unittest.TestCase):
    def setUp(self):
        self._saved_model = whisper_stt._model
        whisper_stt._model = object()  # 视为已加载
        whisper_stt.config.STT_LOCAL = True

    def tearDown(self):
        whisper_stt._model = self._saved_model

    def _seg(self, text):
        class _Seg:
            pass
        s = _Seg()
        s.text = text
        return s

    @mock.patch("xiaoli.whisper_stt._load", return_value=True)
    def test_normal_path(self, m_load):
        """真返回文本（含句内换行拼接），语言锁中文"""
        fake = mock.Mock()
        fake.transcribe.return_value = (iter([self._seg("你"), self._seg("好")]), mock.Mock())
        whisper_stt._model = fake
        text = whisper_stt.transcribe(b"\x00" * 320)
        self.assertEqual(text, "你好")
        kwargs = fake.transcribe.call_args.kwargs
        self.assertEqual(kwargs.get("language"), "zh")
        self.assertEqual(kwargs.get("beam_size"), 1)
        self.assertIs(kwargs.get("vad_filter"), True)

    @mock.patch("xiaoli.whisper_stt._load", return_value=True)
    def test_empty_text_returns_none(self, m_load):
        fake = mock.Mock()
        fake.transcribe.return_value = (iter([self._seg("  ")]), mock.Mock())
        whisper_stt._model = fake
        self.assertIsNone(whisper_stt.transcribe(b"\x00" * 320))

    @mock.patch("xiaoli.whisper_stt._load", return_value=True)
    def test_exception_returns_none(self, m_load):
        """识别抛异常 → None（stt.py 降级火山），不往外崩"""
        fake = mock.Mock()
        fake.transcribe.side_effect = RuntimeError("cuda OOM")
        whisper_stt._model = fake
        self.assertIsNone(whisper_stt.transcribe(b"\x00" * 320))


class TestAddNvidiaDlls(unittest.TestCase):
    @mock.patch("site.getsitepackages",
                return_value=[r"C:\py\Lib\site-packages"])
    @mock.patch("xiaoli.whisper_stt.os.path.isdir", side_effect=lambda p: "nvidia" in p)
    @mock.patch.dict("os.environ", {"PATH": r"C:\Windows"})
    @mock.patch("xiaoli.whisper_stt.os.add_dll_directory")
    def test_path_injected(self, m_add, m_isdir, m_site):
        whisper_stt._add_nvidia_dlls()
        # cublas + cudnn 两个 bin 都注入 PATH
        self.assertIn("nvidia/cublas/bin", os.environ["PATH"])
        self.assertIn("nvidia/cudnn/bin", os.environ["PATH"])
        self.assertEqual(m_add.call_count, 2)


if __name__ == "__main__":
    unittest.main()
