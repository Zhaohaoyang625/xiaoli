# ============================================
# 小李 2.0 · 情绪化 TTS 试听（语音参数模拟版，2026-08-22 修正）
# 同一句话 × 不同情绪 → docs/research/tts-demo/ 对比音频
# 背景：火山"指令式情绪"实测无效（指令被当正文念出来），
#   改为 speech_rate/loudness/pitch 参数模拟（见 tts_api.py EMOTION_PARAMS）
# 你听完后告诉我哪个参数效果合适，我微调映射表
# ============================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

import wave

from xiaoli import paths
from xiaoli import tts_api

OUT = os.path.join(paths.DOCS_DIR, "research", "tts-demo")
os.makedirs(OUT, exist_ok=True)


def synth_save(text, name, emotion=None):
    """合成并保存 wav（24kHz 单声道）"""
    pcm = tts_api.synthesize(text, emotion=emotion)
    if not pcm:
        print(f"[失败] {name} 合成失败")
        return
    path = os.path.join(OUT, name + ".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)
    print(f"[OK] {name} -> {path}（{len(pcm) / 48000:.1f} 秒）")


print("#" * 50)
print("#  生气组：你是不是又跟别的女生聊天了！每次都这样！说好陪我的呢！")
print("#" * 50)

base = "你是不是又跟别的女生聊天了！每次都这样！说好陪我的呢！"
synth_save(base, "A1_生气_对照（无情绪）")
synth_save(base, "A2_生气_参数版（语速+25）", emotion="angry")

print()
print("#" * 50)
print("#  温柔组：好啦好啦，人家跟你闹着玩的嘛，抱一下好不好")
print("#" * 50)

sweet = "好啦好啦，人家跟你闹着玩的嘛，抱一下好不好"
synth_save(sweet, "B1_温柔_对照（无情绪）")
synth_save(sweet, "B2_温柔_参数版（语速-5）", emotion="content")
synth_save(sweet, "B3_爱意_参数版（慢+音量足）", emotion="affectionate")

print()
print("#" * 50)
print("#  低落组：你这么久都不理我，我以为你不要我了……")
print("#" * 50)

low = "你这么久都不理我，我以为你不要我了"
synth_save(low, "C1_低落_对照（无情绪）")
synth_save(low, "C2_低落_参数版（慢+轻）", emotion="melancholy")

print()
print("全部生成完毕，去 docs/research/tts-demo/ 听对比！")
print("重点听：A2 生气的「快」够不够冲、B3 爱意够不够软、C2 低落够不够低落")
print("（语音参数只是辅助，情绪主要靠台词本身——觉得夸张告诉我调小幅度）")
