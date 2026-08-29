# ============================================
# 小李的回声消除 AEC（2026-08-23，复盘清单 A-P1-1）
# 场景：她正在播放声音（Qwen3-TTS/火山/edge），音箱→麦克风传回来她的回声。
# 无 AEC 时打断检测只能靠"峰值阈值调高"硬扛（PEAK_INTERRUPT=2000，细语插话听不到）。
# 方案：pyaec（speexdsp 的 ctypes 封装，Rust 作者维护，Windows wheel 直接装）。
#   near = 麦克风帧，far = voice.py 正在播放的 PCM（不需要 WASAPI loopback）。
# 已知边界（scripts/test_aec.py 小测实证）：
#   ✅ 监听态（她播你听）消回声 ~12dB，滤波收敛
#   ⚠️ 双讲（你插话瞬间）会短暂误伤本人声——打断判定用"AEC 输出 VAD + 原始峰值"
#      双保险，插话的大音量峰值必然过硬阈值（不依赖 AEC），细语插话靠 AEC 消完
#      回声后 VAD 正常灵敏度判中——AEC 是"把灵敏度调回正常"的手段，不是判定本身。
# 发散防护：far/rec 时钟漂移（播放/采集各自缓冲）会让滤波器发散（输出能量异常
#   飙升）——连续 5 帧输出 RMS > 输入 RMS×1.5 → 重建实例重新收敛。
# 未装 pyaec / 初始化失败 → get() 返回 None → call_mode 走原逻辑（永远能打断）。
# ============================================

import threading

import numpy as np

FRAME = 320           # 20ms @16k（speexdsp 常用帧长）
SR = 16000            # 麦克风采样率（与 stt.RATE 一致）
FAR_MS = 200          # far 取"最近 200ms 播放内容"（覆盖音箱→麦克风声学延迟余量）
DIVERGE_LIMIT = 1.5   # 输出 RMS > 输入×1.5 连续 N 帧 = 发散
DIVERGE_FRAMES = 5

_ec = None
_lock = threading.Lock()


def get():
    """全局单例（懒加载）。未装 pyaec / 初始化失败 → None（调用方回退原逻辑）。"""
    global _ec
    if _ec is None:
        with _lock:
            if _ec is None:
                try:
                    import pyaec
                    _ec = EchoCanceller(pyaec)
                except Exception:
                    _ec = None  # 没装/加载失败 → 永远走原打断逻辑
    return _ec


def reset():
    """强制重建（测试用/发散后重试）"""
    global _ec
    with _lock:
        _ec = None


class EchoCanceller:
    """流式 AEC：feed(near, far) 一帧 → 干净帧。far 长度不足时补静音（滤波器顺延）。"""

    def __init__(self, pyaec, frame=FRAME, sr=SR):
        self._frame = frame
        # filter_length = 样本数（speexdsp 参数）；2000 样本 @16k = 125ms——
        # 覆盖声学延迟（~50ms）+ 房间混响（100ms+），比小测的 800 更贴近真实房间
        self._aec = pyaec.Aec(frame, 2000, sr, enable_preprocess=True)
        self._diverge_count = 0

    def _rebuild(self, pyaec=None):
        """滤波器发散 → 重建实例重新收敛（far/rec 漂移后的自救）"""
        import pyaec as _p
        self._aec = _p.Aec(self._frame, 2000, SR, enable_preprocess=True)
        self._diverge_count = 0

    def feed(self, rec, far):
        """rec/far：int16 ndarray（同帧长）→ 消除回声后的 int16 ndarray。
        far 不足一帧 → 静音补足（刚开始播/播放间隙）。
        输出发散迹象 → 重建。失败（任何异常）→ 原样返回 rec（永远有声音）。"""
        try:
            if len(rec) < self._frame:
                rec = np.pad(rec, (0, self._frame - len(rec)))
            if len(far) < self._frame:
                far = np.pad(far, (0, self._frame - len(far)))
            out = np.asarray(self._aec.cancel_echo(rec[:self._frame], far[:self._frame]),
                             dtype=np.int16)
            # 发散防护：回声消除后输出能量不该比输入（含回声）还高
            in_rms = np.sqrt(np.mean(rec[:self._frame].astype(np.float64) ** 2))
            out_rms = np.sqrt(np.mean(out.astype(np.float64) ** 2))
            if in_rms > 100 and out_rms > in_rms * DIVERGE_LIMIT:
                self._diverge_count += 1
                if self._diverge_count >= DIVERGE_FRAMES:
                    self._rebuild()
            else:
                self._diverge_count = 0
            return out
        except Exception:
            return rec[:self._frame]
