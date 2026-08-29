# ============================================
# AEC 小测（2026-08-23，复盘清单 A-P1-1）
# 目标：验证 pyaec（speexdsp 封装）在"麦克风收进她自己的声音"场景有效——
#   合成 far（她正在播的 PCM）+ near（她的回声 + 你本人声）→ 处理后：
#   ① 回声能量大幅下降（消得掉）
#   ② 本人声保留（不误伤）
# 真实接线方案（pass 后再做）：voice.py 播放线程把已送出 PCM 存进环形缓冲，
#   call_mode 取最近 ~200ms 当 far；near = 麦克风流。漂移靠 far 滑窗对齐。
# 注意：本脚本不是 pytest（放 scripts/ 防止被 testpaths=tests 收集）
# ============================================

import sys
import time
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pyaec

SR = 16000
FRAME = 320          # 20ms 帧（speexdsp 常用）
FILTER = 800         # 滤波器长度（20ms 帧 × 40 阶左右，speex 默认 ~800）

# ---- 合成信号 ----
rng = np.random.default_rng(42)
n_frames = 300       # 6 秒

def make_voice(duration, f_base=180.0, f_vib=0.5):
    """类语音信号：基频 + 谐波 + 慢调制（不是纯正弦，像真声一点）"""
    t = np.arange(duration * SR) / SR
    f0 = f_base + 30 * np.sin(2 * np.pi * f_vib * t)     # 音高抖动
    v = (np.sin(2 * np.pi * f0 * t)
         + 0.5 * np.sin(2 * np.pi * 2 * f0 * t)
         + 0.25 * np.sin(2 * np.pi * 3 * f0 * t))
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 2.2 * t)        # 音节包络
    return (v * env * 2000).astype(np.int16)

far = make_voice(6.0, f_base=220.0, f_vib=0.7)           # 她在播的声音（女声基频偏高）
echo_delay = int(0.05 * SR)                              # 50ms 声学延迟（音箱→麦克风）
echo = np.zeros(len(far), dtype=np.int16)
echo[echo_delay:] = (far[:-echo_delay].astype(np.float64) * 0.3).astype(np.int16)  # -10dB 回声
speech = np.zeros(len(far), dtype=np.int16)              # 你本人声：只在后 3 秒说话
speech[3 * SR:] = make_voice(3.0, f_base=140.0, f_vib=0.4)  # （半双工：她播时先监听，后插话）
near = np.clip(speech.astype(np.int64) + echo.astype(np.int64), -32768, 32767).astype(np.int16)

# ---- 跑 AEC（流式逐帧） ----
aec = pyaec.Aec(FRAME, FILTER, SR, enable_preprocess=True)
out_all = []
for i in range(0, len(near) - FRAME + 1, FRAME):
    rec = near[i:i + FRAME]
    ref = far[i:i + FRAME]          # 理想对齐（无漂移的基准场景）
    out_all.append(aec.cancel_echo(rec, ref))
out = np.concatenate(out_all, dtype=np.int16)

# ---- 评估（分段：前 3 秒纯回声=监听态，后 3 秒双讲=插话态） ----
def db(x):
    if len(x) == 0:
        return -99.0
    rms = np.sqrt(np.mean(x.astype(np.float64) ** 2))
    return 20 * np.log10(rms + 1e-9)

# 前 3 秒（收敛后取后 1.5 秒）：纯回声场景——回声消了多少（AEC 的目标场景）
c_end = 3 * SR
echo_in_db = db(echo[FRAME:c_end])
echo_out_db = db(out[FRAME:c_end])
atten = echo_in_db - echo_out_db

# 后 3 秒：双讲——本人声保留多少（插话瞬间别误伤）
s_start = 3 * SR + echo_delay
keep = db(out[s_start:]) - db(speech[s_start:])

print(f"[前3秒·监听态] 回声处理前 {echo_in_db:.1f} dB → 处理后 {echo_out_db:.1f} dB（衰减 {atten:.1f} dB）")
print(f"[后3秒·插话态] 本人声保留 {keep:+.1f} dB（越接近 0 越好）")

# 双讲里本人的可懂度粗判：处理后输出与本人声的相关性（远高于与回声的相关性 = 没被消掉）
a, b = out[s_start:], speech[s_start:]
corr_speech = float(np.corrcoef(a, b)[0, 1])
corr_echo = float(np.corrcoef(a, far[:len(a)])[0, 1])
print(f"插话段输出与本人声相关 {corr_speech:.2f} / 与回声相关 {corr_echo:.2f}")

ok = atten > 12 and keep > -8 and corr_speech > 0.5 and corr_speech > corr_echo
print(f"\n结论：{'✅ AEC 有效（监听态消回声 12dB+，插话态不误伤本人声）' if ok else '❌ 未达标准'}")
sys.exit(0 if ok else 1)
