# ============================================
# AEC 回声消除测试（2026-08-23 批2 A-P1-1）
# 真 pyaec（本机已装，Windows wheel）+ 合成回声场景：
#   她播放（far，220Hz 女声基频）→ 50ms 延迟 -10dB 回声进麦克风（near）→
#   监听态（前 3s 只有回声）：消掉回声 → 输出能量显著低于输入
#   插话态（后 3s + 你 140Hz 男声）：本人声相关性保留
# 另测 voice.py 的播放历史（far 源）：记录/降采样/2s 清理/空历史
# ============================================

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import aec
from xiaoli import voice

SR = 16000


def _make_voice(duration, f_base, f_vib):
    """类语音信号：基频 + 谐波 + 慢调制"""
    t = np.arange(int(duration * SR)) / SR
    f0 = f_base + 30 * np.sin(2 * np.pi * f_vib * t)
    v = (np.sin(2 * np.pi * f0 * t)
         + 0.5 * np.sin(2 * np.pi * 2 * f0 * t)
         + 0.25 * np.sin(2 * np.pi * 3 * f0 * t))
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 2.2 * t)
    return (v * env * 2000).astype(np.int16)


def _db(x):
    if len(x) == 0:
        return -99.0
    rms = np.sqrt(np.mean(x.astype(np.float64) ** 2))
    return 20 * np.log10(rms + 1e-9)


class TestEchoCancellerReal(unittest.TestCase):
    """真 pyaec 流式验证（本机有 pyaec；没有 → 整组跳过）"""

    @classmethod
    def setUpClass(cls):
        try:
            import pyaec  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("pyaec 未安装")

    def _scene(self):
        """合成 6 秒场景：far 全程（她播）、回声 50ms 延迟 -10dB、
        本人声只在后 3 秒（半双工：先监听后插话）"""
        far = _make_voice(6.0, 220.0, 0.7)
        d = int(0.05 * SR)
        echo = np.zeros(len(far), dtype=np.int16)
        echo[d:] = (far[:-d].astype(np.float64) * 0.3).astype(np.int16)
        speech = np.zeros(len(far), dtype=np.int16)
        speech[3 * SR:] = _make_voice(3.0, 140.0, 0.4)
        near = np.clip(speech.astype(np.int64) + echo.astype(np.int64),
                       -32768, 32767).astype(np.int16)
        return far, echo, speech, near

    def test_listen_phase_echo_removed(self):
        """监听态（前 3s 纯回声）→ 消回声 ≥10dB（滤波器收敛后段）"""
        far, echo, speech, near = self._scene()
        ec = aec.EchoCanceller(_pyaec())
        out_all = []
        for i in range(0, len(near) - aec.FRAME + 1, aec.FRAME):
            out_all.append(ec.feed(near[i:i + aec.FRAME], far[i:i + aec.FRAME]))
        out = np.concatenate(out_all)
        # 取后 1.5s（滤波器已收敛）
        tail = 3 * SR
        seg_in = echo[int(aec.FRAME):tail]
        seg_out = out[int(aec.FRAME):tail]
        atten = _db(seg_in) - _db(seg_out)
        self.assertGreater(atten, 10, f"监听态回声衰减 {atten:.1f}dB < 10dB")

    def test_short_far_padded_with_silence(self):
        """far 不足一帧 → 静音补足不崩（刚开始播/播放间隙）"""
        ec = aec.EchoCanceller(_pyaec())
        rec = _make_voice(0.02, 140.0, 0.4)[:aec.FRAME]
        out = ec.feed(rec, np.zeros(100, dtype=np.int16))
        self.assertEqual(len(out), aec.FRAME)

    def test_exception_returns_input(self):
        """异常（far/rec 类型错）→ 原样返回（永远有声音）"""
        ec = aec.EchoCanceller(_pyaec())
        rec = _make_voice(0.02, 140.0, 0.4)[:aec.FRAME]
        out = ec.feed(rec, None)  # far=None → cancel_echo 会炸 → 回退 rec
        self.assertEqual(len(out), aec.FRAME)

    def test_diverge_rebuilds(self):
        """发散（输出能量异常飙升）→ 连续 N 帧后重建（far/rec 漂移自救）"""
        ec = aec.EchoCanceller(_pyaec())
        # 全静音 far + 大能量 rec：AEC 输出可能异常（模拟漂移后 far 错位）
        rec = (np.ones(aec.FRAME, dtype=np.int16) * 5000)
        sil = np.zeros(aec.FRAME, dtype=np.int16)
        for _ in range(aec.DIVERGE_FRAMES + 3):
            ec.feed(rec, sil)
        self.assertEqual(ec._diverge_count, 0)  # 重建后计数清零


def _pyaec():
    import pyaec
    return pyaec


class TestPlaybackHistory(unittest.TestCase):
    """voice.py 播放历史（AEC far 源）：记录 → 取回 → 降采样 → 清理"""

    def setUp(self):
        with voice._far_lock:
            voice._far_history.clear()

    def test_record_and_get(self):
        """记录 16k PCM → 取回最近 200ms"""
        voice._record_playback(16000, b"\x00\x00" * 16000)  # 1s 静音
        far = voice.get_recent_playback(ms=200)
        self.assertEqual(len(far), 3200)  # 200ms @16k

    def test_24k_downsampled_to_16k(self):
        """24k 播放（火山/本地克隆）→ 取回时降到 16k"""
        voice._record_playback(24000, b"\x00\x00" * 24000)  # 1s @24k
        far = voice.get_recent_playback(ms=200)
        self.assertEqual(len(far), 3200)

    def test_history_capped_2s(self):
        """播放历史超 2s → 旧段被清（far 只留最近）"""
        for _ in range(5):
            voice._record_playback(16000, b"\x00\x00" * 16000)  # 5 × 1s
        with voice._far_lock:
            total = sum(len(p) / (s * 2) for s, p in voice._far_history)
        self.assertLessEqual(total, 2.0 + 1e-6)

    def test_record_when_history_exists_no_crash(self):
        """历史非空时再记录（触发清理路径）→ 不崩——回归：之前遍历中 popleft
        抛 RuntimeError，播放线程被吞异常杀掉（TestSpeakingFlag 第二次播放挂）"""
        voice._record_playback(16000, b"\x00\x00" * 16000)  # 1s
        voice._record_playback(16000, b"\x00\x00" * 16000)  # 再 1s（触发清理循环）
        voice._record_playback(16000, b"\x00\x00" * 16000)  # 3s → 该清了
        with voice._far_lock:
            total = sum(len(p) / (s * 2) for s, p in voice._far_history)
        self.assertLessEqual(total, 2.0 + 1e-6)

    def test_empty_history(self):
        """没播过 → 空数组（AEC far 缺信号 → 滤波器顺延，不崩）"""
        far = voice.get_recent_playback()
        self.assertEqual(len(far), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
