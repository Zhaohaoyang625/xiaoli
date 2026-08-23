# ============================================
# 拟声层 sfx 单测（2026-08-23 补缺口）
# 三路径：wav 缓存命中 / 现场合成存盘 / 全失败静默（+一次性警告）
# 播放全部 mock——测试里不真出声
# ============================================

import os
import tempfile
import unittest
import wave

import numpy as np
from unittest import mock

from xiaoli import sfx


class TestLoad(unittest.TestCase):
    def setUp(self):
        # 隔离：缓存清空 + _SFX_DIR 指向临时目录（不碰真 data/sfx/）
        self._saved_cache = sfx._cache
        self._saved_dir = sfx._SFX_DIR
        self._saved_warned = sfx._warned
        self._tmp = tempfile.TemporaryDirectory()
        sfx._cache = {}
        sfx._warned = set()
        sfx._SFX_DIR = self._tmp.name

    def tearDown(self):
        sfx._cache = self._saved_cache
        sfx._SFX_DIR = self._saved_dir
        sfx._warned = self._saved_warned
        self._tmp.cleanup()

    def test_wav_cache_hit_no_synthesis(self):
        """已有 wav → 读文件直接出 (sr, data)，不调 tts_api"""
        pcm = np.zeros(16000, dtype=np.int16).tobytes()
        with wave.open(os.path.join(self._tmp.name, "sigh.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes(pcm)
        with mock.patch.object(sfx.tts_api, "synthesize") as m:
            got = sfx._load("sigh")
            m.assert_not_called()
        self.assertEqual(got[0], 24000)
        self.assertEqual(len(got[1]), 16000)

    def test_no_wav_synthesizes_and_saves(self):
        """无 wav → 合成存盘，第二次直接读文件"""
        pcm = np.zeros(4800, dtype=np.int16).tobytes()
        with mock.patch.object(sfx.tts_api, "synthesize", return_value=pcm):
            got = sfx._load("chuckle")
        self.assertEqual(got[0], 24000)
        self.assertTrue(os.path.exists(os.path.join(self._tmp.name, "chuckle.wav")))
        # 第二次（缓存还在）不合成
        with mock.patch.object(sfx.tts_api, "synthesize") as m:
            sfx._load("chuckle")
            m.assert_not_called()

    def test_synth_failure_warns_once(self):
        """全失败 → None + 警告只打一次（防每轮刷屏）"""
        with mock.patch.object(sfx.tts_api, "synthesize", return_value=None), \
             mock.patch("builtins.print") as m_print:
            self.assertIsNone(sfx._load("cough"))
            m_print.assert_called_once()  # 一次警告
            self.assertIsNone(sfx._load("cough"))
            m_print.assert_called_once()  # 第二次不再警告
        self.assertIn("cough", sfx._warned)

    def test_unknown_name_uses_text_as_def(self):
        """不在定义表的名字 → 用名字本身当文本合成"""
        pcm = np.zeros(4800, dtype=np.int16).tobytes()
        with mock.patch.object(sfx.tts_api, "synthesize", return_value=pcm) as m:
            sfx._load("嗯哼")
            self.assertEqual(m.call_args[0][0], "嗯哼")

    def test_corrupt_wav_falls_back_to_synthesis(self):
        """wav 损坏读不出 → 现场合成兜底，不崩"""
        with open(os.path.join(self._tmp.name, "sigh.wav"), "wb") as f:
            f.write(b"RIFF garbage")
        pcm = np.zeros(4800, dtype=np.int16).tobytes()
        with mock.patch.object(sfx.tts_api, "synthesize", return_value=pcm):
            got = sfx._load("sigh")
        self.assertIsNotNone(got)


class TestGenAll(unittest.TestCase):
    def setUp(self):
        self._saved_cache = sfx._cache
        self._saved_dir = sfx._SFX_DIR
        self._tmp = tempfile.TemporaryDirectory()
        sfx._cache = {}
        sfx._SFX_DIR = self._tmp.name

    def tearDown(self):
        sfx._cache = self._saved_cache
        sfx._SFX_DIR = self._saved_dir
        self._tmp.cleanup()

    @mock.patch.object(sfx.tts_api, "synthesize",
                       return_value=np.zeros(4800, dtype=np.int16).tobytes())
    def test_gen_all_creates_all_wavs(self, m_synth):
        sfx._gen_all()
        for name in sfx._SFX_DEF:
            self.assertTrue(os.path.exists(
                os.path.join(self._tmp.name, name + ".wav")),
                f"{name}.wav 没生成")
        self.assertEqual(m_synth.call_count, len(sfx._SFX_DEF))


class TestPlay(unittest.TestCase):
    def setUp(self):
        self._saved_cache = sfx._cache
        self._saved_dir = sfx._SFX_DIR
        self._tmp = tempfile.TemporaryDirectory()
        sfx._cache = {}
        sfx._SFX_DIR = self._tmp.name
        # 预置素材（成功路径用）
        pcm = np.zeros(4800, dtype=np.int16).tobytes()
        with wave.open(os.path.join(self._tmp.name, "sigh.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes(pcm)

    def tearDown(self):
        sfx._cache = self._saved_cache
        sfx._SFX_DIR = self._saved_dir
        self._tmp.cleanup()

    @mock.patch("xiaoli.sfx.sd")
    def test_play_blocking_plays(self, m_sd):
        sfx.play_blocking("sigh")
        m_sd.play.assert_called_once()

    @mock.patch("xiaoli.sfx.sd")
    def test_play_blocking_never_raises(self, m_sd):
        """素材缺失 → 静默返回不崩"""
        m_sd.play.side_effect = RuntimeError("no device")
        sfx.play_blocking("missing")  # 不崩
        sfx.play("missing")           # 不崩（且不开线程）

    @mock.patch("xiaoli.sfx.sd")
    def test_play_async_runs_in_thread(self, m_sd):
        sfx.play("sigh")
        m_sd.play.assert_called_once()


if __name__ == "__main__":
    unittest.main()
