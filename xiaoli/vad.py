# ============================================
# 小李的流式人声检测 VAD（2026-08-23，批2 A-P0-1）
# 背景：复盘报告点名"打断检测器清一色 Silero VAD（模型），没有一家用纯能量阈值"——
# 我们是全行业唯一用固定峰值 500/2000 的（安静/嘈杂环境都会错判）。
# 本模块移植 OLLVT 的 SileroVAD（docs/research/sources/code-v2/ollvt/vad/silero.py）：
#   512 窗（32ms@16k）逐窗 → prob≥0.4 且 int16 RMS dB≥45 双条件
#   → 5 窗平滑（deque 取平均）→ 3 连击开口（0.1s）→ 24 连 miss 收尾（0.8s）
# 模型：faster-whisper 内置同一个 Silero ONNX（faster_whisper.vad.get_vad_model，
#   ~2MB 随 pip 包分发零下载；比 silero-vad 包少一个依赖）。
# 未就绪（onnxruntime 缺/加载失败）→ get() 返回 None，call_mode 回退能量阈值（永远能听）。
# db 门槛 60→45→50：OLLVT 有浏览器 AEC 才敢用 60dB 高门槛；45dB（int16
#   RMS≈180）本底噪声（笔记本风扇/电流声 RMS 常到 200~400 ≈ 46~52dB）轻松
#   过 → VAD 把环境声当"在说话"，收了十几秒噪声识别出空文本（用户实测
#   "点开没反应"：3 次听你说完 10~12 秒全空）。50dB（RMS≈560）滤掉本底，
#   正常说话（近麦 65~75dB）不受影响；防回声仍靠打断分支峰值兜底。
# ============================================

import threading
from collections import deque

import numpy as np

# OLLVT 参数（照抄源码，见模块头注释）
WINDOW = 512          # 512 样本 = 32ms @16k
PROB_THRESHOLD = 0.4  # Silero 输出概率 ≥ 0.4 算"人声窗"
DB_THRESHOLD = 50.0   # int16 RMS dB 门槛（满幅 32767 ≈ 90dB；45→50 见模块头注释）
SMOOTH_WINDOW = 5     # 5 窗平滑（防单窗模型抖动）
HITS_OPEN = 3         # 3 连击 = 真开口（0.1s，防咳嗽/爆音）
MISSES_CLOSE = 24     # 24 连 miss = 说完了（0.8s）
# 判停专用阈值（2026-08-23 用户实测"听不到"根因）：环境人声（游戏/视频里的人话）
# prob 常在 0.4~0.6，会把状态机 speech 拖住不放 → 判停等到十几秒甚至 20s 截断。
# 近麦真说话 prob 0.7+。last_voice 用 PROB_STOP 判块级"强语音窗"，call_mode 数
# "连续 0.8s 无强语音窗"判停——环境人声很快漏出空隙判停，真说话不受影响。
PROB_STOP = 0.5

_model = None
_loading = False
_lock = threading.Lock()
_singleton = None


def _load():
    """加载 faster-whisper 内置 Silero ONNX（CPU，~2MB）。并发安全。"""
    global _model, _loading
    with _lock:
        if _model is not None:
            return True
        if _loading:
            return False
        _loading = True
    try:
        from faster_whisper.vad import get_vad_model
        _model = get_vad_model()
        return True
    except Exception:
        _model = None
        return False
    finally:
        _loading = False


def ready():
    """VAD 是否可用（未加载时同步加载——onnx 初始化 ~0.1s，只在首次调用卡一下）"""
    return _model is not None or _load()


def preload():
    """启动后台预热（不阻塞聊天）"""
    threading.Thread(target=_load, daemon=True).start()


def get():
    """全局单例（懒加载）。未就绪 → None（调用方回退能量阈值）。"""
    global _singleton
    if _singleton is None and ready():
        _singleton = SileroVad()
    return _singleton


def reset():
    """重建单例（2026-08-23 通话模式 start() 调用）：状态机残留（speech/hits/misses
    未收尾）会让重开后的静音被当"还在说话"——白收一段静音去识别。"""
    global _singleton
    _singleton = None


class SileroVad:
    """流式 VAD（OLLVT 状态机移植）。feed 一块 PCM → 现在是否在说话（ACTIVE 态）。
    用法：每 100ms 块调一次 feed，内部按 512 窗滑窗+平滑+连击计数。"""

    def __init__(self):
        self._prob_win = deque(maxlen=SMOOTH_WINDOW)
        self._db_win = deque(maxlen=SMOOTH_WINDOW)
        self._hits = 0
        self._misses = 0
        self.speech = False  # ACTIVE：正在说话（外部读这个，或看 feed 返回值）
        self.last_voice = False  # 本块"强语音窗"（prob≥PROB_STOP 且 db≥门槛）——判停用

    @staticmethod
    def _db(audio_int16):
        """int16 数组的 RMS 分贝（满幅 32767 ≈ 90dB；OLLVT calculate_db 同式）"""
        rms = np.sqrt(np.mean(np.square(audio_int16.astype(np.float64))))
        return 20 * np.log10(rms + 1e-7) if rms > 0 else -np.inf

    def feed(self, pcm) -> bool:
        """喂一块 16k PCM（int16 bytes 或 ndarray）→ 现在是否 ACTIVE。
        失败（模型没就绪/异常）→ 返回当前 speech 状态（调用方别崩）。
        坑（2026-08-23 实测抓出，通话模式聋的根因）：sounddevice 的 stream.read()
        对 channels=1 也返回 (1600, 1) 二维数组——SileroVADModel 断言输入必须 1D，
        2D 输入抛 AssertionError 被 except 吞掉 → feed 永远 False → 通话模式永远
        判定"没人说话"。必须 reshape(-1) 展平（1D 输入 reshape 是 no-op）。"""
        if _model is None:
            return self.speech
        try:
            audio = np.frombuffer(pcm, dtype=np.int16) if isinstance(pcm, (bytes, bytearray)) \
                else np.asarray(pcm, dtype=np.int16).reshape(-1)
            # 整块喂模型 → 逐 512 窗概率序列（onnx 批处理比逐窗 torch 快）。
            # 坑：模型要求输入是 512 的倍数（否则 AssertionError）→ 截断到整窗
            n = len(audio) - (len(audio) % WINDOW)
            if n == 0:
                return self.speech
            audio_f = audio[:n].astype(np.float32) / 32768.0
            probs = _model(audio_f)
            # 本块"强语音窗"（判停专用，独立于状态机）：整块平均 prob ≥ PROB_STOP
            # 且整块 dB ≥ 门槛。环境人声（prob 0.4~0.6）判 False → 判停能及时发生
            self.last_voice = (float(np.mean(probs)) >= PROB_STOP
                               and self._db(audio[:n]) >= DB_THRESHOLD)
            for i in range(0, n, WINDOW):
                win = audio[i:i + WINDOW]
                prob = probs[i // WINDOW]
                self._prob_win.append(prob)
                self._db_win.append(self._db(win))
                smoothed = (np.mean(self._prob_win), np.mean(self._db_win))
                if smoothed[0] >= PROB_THRESHOLD and smoothed[1] >= DB_THRESHOLD:
                    self._hits += 1
                    self._misses = 0
                    if not self.speech and self._hits >= HITS_OPEN:
                        self.speech = True
                else:
                    self._misses += 1
                    self._hits = 0
                    if self.speech and self._misses >= MISSES_CLOSE:
                        self.speech = False
        except Exception:
            pass  # VAD 挂了不影响通话（旧逻辑还能听）
        return self.speech
