# ============================================
# 通话模式监听链路诊断（2026-08-23）
# 模拟 call_mode 的监听：InputStream 同参数 + 判定逻辑逐块打印。
# 对着麦克风说话，看数据流：峰值/RMS/VAD 判定——一刀切出问题在哪一层。
# ============================================

import sys
import time

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import sounddevice as sd

from xiaoli import stt, vad

print("加载 VAD（首次 ~0.1s）…", flush=True)
v = vad.get()
print(f"VAD 实例: {'有（Silero）' if v else '无（回退能量阈值）'}", flush=True)

print("开麦克风（16k，同通话模式参数），说 15 秒话，逐块打印…", flush=True)
stream = sd.InputStream(samplerate=stt.RATE, channels=1,
                        dtype="int16", blocksize=stt.CHUNK)
stream.start()
t0 = time.time()
while time.time() - t0 < 15:
    data, _ = stream.read(stt.CHUNK)
    peak = int(np.abs(data).max())
    rms = float(np.sqrt(np.mean(np.square(data.astype(np.float64)))))
    db = 20 * np.log10(rms + 1e-7)
    if v is not None:
        t = v.feed(data)
        # 强窗=块级判停窗（prob≥0.5，vad.PROB_STOP）；VAD=True 强窗=False →
        # 环境人声嫌疑（prob 0.4~0.6 区间）——判停被它拖慢的现场证据
        verdict = f"VAD={t} 强窗={v.last_voice}"
    else:
        verdict = f"能量={'说' if peak > 500 else '静'}"
    print(f"  {time.time()-t0:5.1f}s peak={peak:6d} dB={db:5.1f} {verdict}", flush=True)
stream.stop()
stream.close()
print("诊断结束", flush=True)
