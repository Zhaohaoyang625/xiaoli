# ============================================
# sherpa-onnx 流式识别（2026-08-24 研究落地）
# "说一句出半句"：0.1s 块喂入 → 实时出 partial → 说完整句 finalize 即全文
# 用途：call_mode.py 通话模式识别加速（替代"说完才送 whisper 推理 1~2s"）
# 设计（对照官方示例 online-decode-files.py）：
#   - 模型：zipformer-zh-int8-2025-06-30（132MB，CPU 4 线程 RTF 0.067，实测）
#   - 尾部 padding：finalize 前喂 0.66s 静音（官方示例注明：没有它句尾字会丢，实测复现）
#   - 判停不用 is_endpoint（call_mode 的 VAD 判停已工作），只负责喂入+拿全文
#   - 模型缺失/加载失败 → begin() 返回 None，调用方走 whisper 降级（永远有耳朵）
# ============================================

import os
import threading

import numpy as np
import sherpa_onnx  # pip install sherpa-onnx（清华源，1.13.6）

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models", "sherpa",
                         "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30")
NUM_THREADS = 4
TAIL_PADDING_SECONDS = 0.66  # 官方示例值：句尾丢字靠它补（实测 001/003 复现）

_recognizer = None
_lock = threading.Lock()
_load_failed = False


def _find(pattern):
    """容错找模型文件：encoder.onnx / encoder.int8.onnx 都行"""
    d = MODEL_DIR
    if not os.path.isdir(d):
        return None
    for f in os.listdir(d):
        if f.startswith(pattern) and f.endswith(".onnx"):
            return os.path.join(d, f)
    return None


def available():
    """模型文件齐不齐（启动自检/日志用）"""
    return bool(_find("encoder") and _find("decoder") and _find("joiner")
                and os.path.isfile(os.path.join(MODEL_DIR, "tokens.txt")))


def _ensure():
    """懒加载识别器（线程安全）。失败 → None（调用方降级 whisper）"""
    global _recognizer, _load_failed
    if _recognizer is not None or _load_failed:
        return _recognizer
    with _lock:
        if _recognizer is not None or _load_failed:
            return _recognizer
        enc, dec, join = _find("encoder"), _find("decoder"), _find("joiner")
        tok = os.path.join(MODEL_DIR, "tokens.txt")
        if not (enc and dec and join and os.path.isfile(tok)):
            print(f"  [流式识别模型不齐：{MODEL_DIR}，通话模式用 whisper 识别]", flush=True)
            _load_failed = True
            return None
        try:
            _recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=tok, encoder=enc, decoder=dec, joiner=join,
                num_threads=NUM_THREADS, sample_rate=16000, feature_dim=80,
                decoding_method="greedy_search",
            )
        except Exception as e:
            print(f"  [流式识别加载失败：{e}，通话模式用 whisper 识别]", flush=True)
            _load_failed = True
    return _recognizer


class Session:
    """一句话的流式识别会话。用法：
        s = sherpa_stream.begin()          # None = 不可用（降级）
        s.feed(pcm_0_1s)                    # 每 0.1s 块喂一次（可选拿 partial）
        text = s.finalize()                 # 说完了 → 完整文本
    """

    def __init__(self, recognizer):
        self._r = recognizer
        self._s = recognizer.create_stream()

    def feed(self, pcm):
        """喂 0.1s int16 PCM（16k），返回当前 partial 文本（空串=没出字）"""
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
        self._s.accept_waveform(16000, x)
        while self._r.is_ready(self._s):
            self._r.decode_stream(self._s)
        return self._r.get_result(self._s).strip()

    def finalize(self):
        """说完了：补尾部静音 padding → 完整文本（空串=没听清，调用方降级）"""
        self._s.accept_waveform(
            16000, np.zeros(int(TAIL_PADDING_SECONDS * 16000), dtype=np.float32))
        self._s.input_finished()
        while self._r.is_ready(self._s):
            self._r.decode_stream(self._s)
        return self._r.get_result(self._s).strip()


def begin():
    """新会话。识别器不可用 → None（调用方走 whisper 降级）"""
    r = _ensure()
    if r is None:
        return None
    try:
        return Session(r)
    except Exception:
        return None


if __name__ == "__main__":
    # 自测：读一个语料 wav，流式识别打印 partial + 全文
    import glob
    import sys
    import time
    import wave
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    wavs = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..",
                                         "data", "gpt-sovits-corpus", "wavs", "*.wav")))
    if not wavs:
        print("[!!] 没有语料 wav")
        raise SystemExit(1)
    w = wavs[0]
    with wave.open(w) as f:
        sr = f.getframerate()
        raw = f.readframes(f.getnframes())
    # 24k → 16k（线性重采样）
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    n_out = int(len(x) * 16000 / sr)
    xr = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    pcm16 = xr.astype(np.int16)

    s = begin()
    if s is None:
        print("[!!] 流式识别不可用")
        raise SystemExit(1)
    t0 = time.time()
    step = 1600
    for i in range(0, len(pcm16), step):
        p = s.feed(pcm16[i:i + step].tobytes())
        if p and i % (step * 10) == 0:
            print("  partial:", p)
    text = s.finalize()
    print(f"  全文: {text}")
    print(f"  用时: {time.time() - t0:.2f}s（音频 {len(pcm16) / 16000:.1f}s）")
