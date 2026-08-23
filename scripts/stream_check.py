# ============================================
# 流式录音诊断（对比 sd.rec 一次性 vs sd.InputStream 流式）
# 小李的 stt.py 用的是 InputStream 流式（0.1秒一块读）
# 如果这里测出来没声音 → 打开方式的问题；有声音 → 问题在触发流程
# 跑法：python scripts/stream_check.py
# ============================================

import time

import numpy as np
import sounddevice as sd

CHUNK = 1600  # 0.1 秒 @ 16kHz —— 和 stt.py 完全一样的参数

print("=== 用 InputStream 流式录 3 秒（和 stt.py 相同的方式） ===")
print("请在这 3 秒内一直说话……")
stream = sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                        blocksize=CHUNK)
stream.start()
peaks = []
t0 = time.time()
while time.time() - t0 < 3:
    data, _ = stream.read(CHUNK)
    peaks.append(int(np.abs(data).max()))
stream.stop()
stream.close()

print("每块峰值（共", len(peaks), "块）：", peaks)
mx = max(peaks)
print("最大峰值：", mx)
if mx > 500:
    print("结论：流式录音有声音 ✅ → stt.py 的打开方式没问题，问题在触发流程/时机")
else:
    print("结论：流式录音没声音 ❌ → InputStream 打开方式有问题（和 sd.rec 行为不同！）")
