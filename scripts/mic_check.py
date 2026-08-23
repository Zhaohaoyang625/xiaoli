# ============================================
# 麦克风诊断（遇到"听不到"时先跑这个）
# 作用：①列出所有设备 ②看默认输入设备 ③录3秒测峰值
# 跑法：python tools/mic_check.py
# ============================================

import time

import numpy as np
import sounddevice as sd

print("=== 1. 设备列表（找带『← 默认输入』的） ===")
for i, d in enumerate(sd.query_devices()):
    mark = " ← 默认输入" if d.get("isdefault_input") else ""
    print(f"  {i} | {d['name']} | 输入声道:{d['max_input_channels']}{mark}")

print(f"\n=== 2. 当前默认输入设备：{sd.default.device} ===")

print("\n=== 3. 现在请对着麦克风说 3 秒话（说啥都行） ===")
data = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="int16")
sd.wait()
peak = int(np.abs(data).max())
print(f"3 秒录音峰值：{peak}")
if peak > 1000:
    print("结论：麦克风有声音 ✅ → 问题不在设备，继续往下查")
else:
    print("结论：麦克风没采到声音 ❌")
    print("可能原因：")
    print("  ① 默认输入设备变了（插了耳机/新麦克风？）→ 看第2步设备号")
    print("  ② 有别的程序占用了麦克风（微信语音、录音软件、浏览器网页？）→ 关掉再试")
    print("  ③ 麦克风被系统静音或隐私权限关了 → Windows设置→隐私→麦克风")
