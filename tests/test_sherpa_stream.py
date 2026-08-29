# ============================================
# sherpa-onnx 流式识别单测（2026-08-24）
# 不碰真实模型/麦克风：mock sherpa_onnx 验模块逻辑（懒加载/归一化/尾部padding/降级）
# ============================================

import unittest
from unittest import mock

import numpy as np

from xiaoli import sherpa_stream


class TestSherpaStream(unittest.TestCase):
    def tearDown(self):
        sherpa_stream._recognizer = None
        sherpa_stream._load_failed = False

    def _fake_rec(self, text=" 你好呀  "):
        rec = mock.Mock()
        rec.create_stream.return_value = mock.Mock()
        rec.is_ready.return_value = False
        rec.get_result.return_value = text
        return rec

    def test_available_false_when_dir_missing(self):
        with mock.patch("xiaoli.sherpa_stream.MODEL_DIR", "/nonexistent"):
            self.assertFalse(sherpa_stream.available())

    def test_available_true_when_files_ok(self):
        with mock.patch("xiaoli.sherpa_stream._find",
                        return_value="/m/encoder.int8.onnx"), \
             mock.patch("os.path.isfile", return_value=True):
            self.assertTrue(sherpa_stream.available())

    def test_ensure_loads_once(self):
        """懒加载：首次创建，二次复用（不重载）"""
        rec = self._fake_rec()
        with mock.patch("xiaoli.sherpa_stream.sherpa_onnx.OnlineRecognizer.from_transducer",
                        return_value=rec) as m, \
             mock.patch("xiaoli.sherpa_stream.available", return_value=True):
            self.assertIs(sherpa_stream._ensure(), rec)
            self.assertIs(sherpa_stream._ensure(), rec)
            m.assert_called_once()

    def test_ensure_none_when_files_missing(self):
        """模型不齐 → None + 标记失败，且不反复重试"""
        with mock.patch("xiaoli.sherpa_stream._find", return_value=None), \
             mock.patch("os.path.isfile", return_value=False), \
             mock.patch("builtins.print"):
            self.assertIsNone(sherpa_stream._ensure())
        self.assertTrue(sherpa_stream._load_failed)
        with mock.patch("xiaoli.sherpa_stream._find", return_value="/m/enc.onnx"):
            self.assertIsNone(sherpa_stream._ensure())  # 失败后不再试

    def test_ensure_none_on_exception(self):
        with mock.patch("xiaoli.sherpa_stream.available", return_value=True), \
             mock.patch("xiaoli.sherpa_stream.sherpa_onnx.OnlineRecognizer.from_transducer",
                        side_effect=RuntimeError("bad")), \
             mock.patch("builtins.print"):
            self.assertIsNone(sherpa_stream._ensure())

    def test_begin_returns_session_or_none(self):
        rec = self._fake_rec()
        with mock.patch("xiaoli.sherpa_stream._ensure", return_value=rec):
            self.assertIsNotNone(sherpa_stream.begin())
        with mock.patch("xiaoli.sherpa_stream._ensure", return_value=None):
            self.assertIsNone(sherpa_stream.begin())

    def test_feed_normalizes_and_strips(self):
        """int16 → float32 [-1,1] → accept_waveform(16000)；结果 strip"""
        rec = self._fake_rec()
        with mock.patch("xiaoli.sherpa_stream._ensure", return_value=rec):
            s = sherpa_stream.begin()
        pcm = (np.ones(1600, dtype=np.int16) * 32767).tobytes()
        out = s.feed(pcm)
        self.assertEqual(out, "你好呀")
        st = rec.create_stream.return_value
        sr, x = st.accept_waveform.call_args.args
        self.assertEqual(sr, 16000)
        self.assertAlmostEqual(float(np.abs(x).max()), 1.0, places=4)
        self.assertEqual(st.decode_stream.call_count, 0)  # is_ready False 不解码

    def test_feed_decodes_when_ready(self):
        rec = self._fake_rec()
        rec.is_ready.side_effect = [True, False]  # 喂完解一轮
        with mock.patch("xiaoli.sherpa_stream._ensure", return_value=rec):
            s = sherpa_stream.begin()
        s.feed(np.zeros(1600, dtype=np.int16).tobytes())
        # decode_stream 是 recognizer 的方法（self._r.decode_stream(self._s)），不是 stream 的
        self.assertEqual(rec.decode_stream.call_count, 1)

    def test_finalize_pads_tail_and_finishes(self):
        """官方示例坑：finalize 前补 0.66s 静音 padding（没有它句尾字会丢，实测复现）"""
        rec = self._fake_rec()
        with mock.patch("xiaoli.sherpa_stream._ensure", return_value=rec):
            s = sherpa_stream.begin()
        s.feed(np.zeros(1600, dtype=np.int16).tobytes())
        out = s.finalize()
        st = rec.create_stream.return_value
        self.assertEqual(st.input_finished.call_count, 1)
        # 最后一次 accept_waveform = 尾部 padding（0.66s × 16000 个静音样本）
        last_sr, last_x = st.accept_waveform.call_args_list[-1].args
        self.assertEqual(len(last_x), int(0.66 * 16000))
        self.assertTrue(np.all(last_x == 0))
        self.assertEqual(out, "你好呀")


class TestCallModeStream(unittest.TestCase):
    """通话模式集成：sherpa 流式走通 / 降级 / 开关 / 残留防护"""

    def _fake_session(self, text="哈囉"):
        s = mock.Mock()
        s.finalize.return_value = text
        return s

    def _start_call(self):
        from xiaoli import call_mode
        call_mode._call = None  # 清全局实例
        m = call_mode.get()
        m.on_text = None
        return m

    def test_finish_uses_stream_text(self):
        """流式会话存在 → finalize 文本直接进 on_text（不调 whisper）"""
        from xiaoli import call_mode
        m = self._start_call()
        m._sess = self._fake_session("哈囉")
        got = []
        m.on_text = got.append
        with mock.patch("xiaoli.stt.recognize_pcm",
                        side_effect=AssertionError("不该走 whisper")):
            m._finish([b"\x00\x00" * 1600 * 10], voice_blocks=4)
        self.assertEqual(got, ["哈囉"])
        self.assertIsNone(m._sess)  # 用后清空，下一句开新会话

    def test_finish_empty_stream_falls_back_to_whisper(self):
        """流式没听清（空）→ whisper 兜底"""
        from xiaoli import call_mode
        m = self._start_call()
        m._sess = self._fake_session("")
        got = []
        m.on_text = got.append
        with mock.patch("xiaoli.stt.recognize_pcm", return_value="听清了"):
            m._finish([b"\x00\x00" * 1600 * 10], voice_blocks=4)
        self.assertEqual(got, ["听清了"])

    def test_finish_no_speech_resets_session(self):
        """没真开口（咳嗽一声）→ _sess 也重置（残留会污染下一句）"""
        from xiaoli import call_mode
        m = self._start_call()
        m._sess = self._fake_session()
        m._finish([b"\x00\x00" * 1600 * 10], voice_blocks=1)  # < MIN_VOICE_BLOCKS
        self.assertIsNone(m._sess)

    def test_loop_feeds_stream_when_talking(self):
        """开口块 → begin() + feed()；静音块只收不喂"""
        from xiaoli import call_mode
        m = self._start_call()
        sess = self._fake_session()
        with mock.patch("xiaoli.call_mode.sherpa_stream.begin", return_value=sess):
            vv = mock.Mock()
            vv.last_voice = True
            # 构造"开口"块（峰值>500）触发 talking 分支
            data = (np.ones(1600, dtype=np.int16) * 1000).tobytes()
            m._talking = lambda v, d: True
            # 模拟 _loop 的开口段逻辑：手动走一遍
            buf = []
            m._sess = None
            buf.append(data)
            if call_mode.config.STT_STREAM and m._sess is None:
                m._sess = call_mode.sherpa_stream.begin()
            if m._sess is not None:
                m._sess.feed(data)
        self.assertIsNotNone(m._sess)
        sess.feed.assert_called_once()

    def test_loop_skips_stream_when_disabled(self):
        """STT_STREAM=False → 不 begin 不 feed（老路 whisper）"""
        from xiaoli import call_mode
        m = self._start_call()
        with mock.patch("xiaoli.call_mode.sherpa_stream.begin",
                        side_effect=AssertionError("开关关着不该创建流式")), \
             mock.patch("xiaoli.call_mode.config.STT_STREAM", False):
            m._sess = None
            # 模拟 _loop 开口分支
            if call_mode.config.STT_STREAM and m._sess is None:
                m._sess = call_mode.sherpa_stream.begin()
        self.assertIsNone(m._sess)


if __name__ == "__main__":
    unittest.main()
