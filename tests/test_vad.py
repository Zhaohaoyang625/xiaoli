# ============================================
# 流式人声检测 VAD 测试（2026-08-23 批2 A-P0-1）
# Silero VAD（faster-whisper 内置 ONNX）+ OLLVT 参数：
# prob≥0.4 且 int16 RMS dB≥45 双条件 → 5 窗平滑 → 3 连击开口（0.1s）→ 24 连 miss 收尾（0.8s）
# 真实模型测试（模型随 faster-whisper 包分发 ~2MB，零下载）：
#   静音必 False、低音量必 False（db 门槛）、真实人声（sfx 拟声降采样）必 True
# ============================================

import os
import sys
import unittest
import wave
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import vad


def _sfx_16k(name):
    """读 data/sfx/{name}.wav（24k）→ 16k int16 数组（线性降采样）"""
    with wave.open(os.path.join("data", "sfx", name + ".wav")) as w:
        frames = w.readframes(w.getnframes())
        sr = w.getframerate()
    audio = np.frombuffer(frames, dtype=np.int16)
    if sr == 16000:
        return audio
    # 24k→16k：隔 3 取 2（比例 2/3）
    idx = np.arange(0, len(audio), sr / 16000.0).astype(int)
    return audio[idx]


def _chunks(audio, n=3200):
    """切成 100ms 块（3200 样本 @16k）"""
    return [audio[i:i + n] for i in range(0, len(audio), n)]


class TestDb(unittest.TestCase):
    def test_silence_db_neg_inf(self):
        self.assertEqual(vad.SileroVad._db(np.zeros(512, dtype=np.int16)), -np.inf)

    def test_full_scale_sine_db(self):
        """满幅正弦（rms≈0.707×32767）→ ≈90dB（满幅 ≈90dB 的公式基准）"""
        t = np.arange(512)
        s = (32767 * np.sin(2 * np.pi * 440 * t / 16000)).astype(np.int16)
        db = vad.SileroVad._db(s)
        self.assertGreater(db, 85)
        self.assertLess(db, 92)

    def test_quiet_noise_below_threshold(self):
        """低音量噪声（int16 rms≈100 ≈ 40dB）→ 低于 45dB 门槛"""
        np.random.seed(7)
        s = np.random.randint(-300, 300, 512).astype(np.int16)
        self.assertLess(vad.SileroVad._db(s), 45.0)


class TestSileroVadReal(unittest.TestCase):
    """真实 Silero 模型（本机有 onnxruntime；模型加载失败则整组跳过）"""

    @classmethod
    def setUpClass(cls):
        if not vad.ready():
            raise unittest.SkipTest("Silero VAD 模型不可用（缺 onnxruntime 或加载失败）")
        cls.v = vad.SileroVad()

    def test_silence_false(self):
        """纯静音 → 永远 False（喂 1s）"""
        for _ in range(10):
            self.assertFalse(self.v.feed(b"\x00\x00" * 3200))

    def test_quiet_noise_false(self):
        """低音量噪声（db<45 门槛）→ False（prob 高也会被 db 拒）"""
        np.random.seed(3)
        quiet = (np.random.randn(3200) * 0.0005).astype(np.float32)  # rms≈0.0005 ≈ -66dBFS ≈ int16 16
        data = (quiet * 32767).astype(np.int16)
        v = vad.SileroVad()
        for _ in range(6):
            self.assertFalse(v.feed(data.tobytes()))

    def test_real_voice_true(self):
        """真实人声（她叹气/清嗓，24k 降 16k）→ 开口 ACTIVE"""
        v = vad.SileroVad()
        for name in ("sigh", "clear_throat", "chuckle"):
            audio = _sfx_16k(name)
            if len(audio) < 3200:
                continue
            for c in _chunks(audio):
                if v.feed(c):
                    return  # 至少一段语音开口成功
        self.fail("喂完三段真实人声都没开口（VAD 判定过严？）")

    def test_voice_then_silence_closes(self):
        """说话后 0.8s 静音 → 收尾（24 连 miss）→ False"""
        v = vad.SileroVad()
        audio = _sfx_16k("sigh")
        opened = False
        for c in _chunks(audio):
            if v.feed(c):
                opened = True
        self.assertTrue(opened)
        for _ in range(12):  # 1.2s 静音 > 0.8s 收尾
            v.feed(b"\x00\x00" * 3200)
        self.assertFalse(v.speech)

    def test_feed_no_model_returns_state(self):
        """模型没就绪 → feed 不崩，返回当前 speech 状态"""
        v = vad.SileroVad()
        with mock.patch.object(vad, "_model", None):
            self.assertFalse(v.feed(b"\x00\x00" * 3200))

    def test_feed_2d_sounddevice_input(self):
        """sounddevice 返回 (1600,1) 二维 → 必须展平（2026-08-23 通话模式聋的根因：
        2D 喂模型抛 AssertionError 被 except 吞 → feed 永远 False → 通话模式听不到）"""
        v = vad.SileroVad()
        audio = _sfx_16k("sigh")
        opened = False
        for c in _chunks(audio):
            # 模拟 sd.InputStream.read 的真实返回：channels=1 也是 (n, 1) 二维
            if v.feed(np.asarray(c).reshape(-1, 1)):
                opened = True
        self.assertTrue(opened, "2D 输入下 VAD 必须能判定人声（展平修复）")
        # 修复前这里静默 False——AssertionError 被 except: pass 吞掉

    def test_last_voice_true_on_real_voice(self):
        """真实人声块 → last_voice=True（判停强语音窗）"""
        v = vad.SileroVad()
        audio = _sfx_16k("sigh")
        for c in _chunks(audio):
            v.feed(c)
            if v.last_voice:
                break
        self.assertTrue(v.last_voice, "真实人声块必须激活强语音窗")

    def test_last_voice_false_on_silence(self):
        """静音块 → last_voice=False（判停强语音窗不激活）"""
        v = vad.SileroVad()
        v.feed(b"\x00\x00" * 3200)
        self.assertFalse(v.last_voice)

    def test_last_voice_init_false(self):
        """构造后未喂数据 → last_voice=False（判停计数不误起步）"""
        self.assertFalse(vad.SileroVad().last_voice)


class TestVadModule(unittest.TestCase):
    def test_get_none_when_unavailable(self):
        """模型不可用 → get() 返回 None（调用方回退能量阈值，永远能听）"""
        with mock.patch.object(vad, "ready", return_value=False):
            self.assertIsNone(vad.get())

    def test_get_singleton(self):
        """get() 返回同一实例（懒加载单例）"""
        if not vad.ready():
            self.skipTest("模型不可用")
        vad._singleton = None
        a = vad.get()
        b = vad.get()
        self.assertIs(a, b)
        vad._singleton = None

    def test_reset_rebuilds_singleton(self):
        """reset() 后 get() 返回新实例（通话模式重开防状态机残留）"""
        if not vad.ready():
            self.skipTest("模型不可用")
        vad._singleton = None
        a = vad.get()
        vad.reset()
        b = vad.get()
        self.assertIsNot(a, b)
        vad._singleton = None

    def test_preload_background(self):
        """preload 不阻塞（后台线程加载）"""
        with mock.patch("threading.Thread") as th:
            vad.preload()
            th.assert_called_once()
            self.assertEqual(th.return_value.start.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
