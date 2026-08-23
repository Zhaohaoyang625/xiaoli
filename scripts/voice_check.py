# ============================================
# 一键声音验收（2026-08-23，用户自测用）
# 帮你把"听"的部分自动化：合成 3 句测试语料 + 4 个拟声试听
# 你只需要：开着声音，听，然后看输出判断
# 用法：python scripts/voice_check.py
# 输出用 [OK]/[!!]（GBK 终端 emoji 会崩）
# ============================================

import sys
import time
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import sounddevice as sd

from xiaoli import tts_local, sfx, whisper_stt, embed

def _mark(ok_):
    return "[OK]" if ok_ else "[!!]"

def _play_tts(text, label):
    """合成一句话并播放，返回耗时"""
    t0 = time.time()
    print(f"\n▶ {label}")
    print(f"  说：「{text}」")
    got = tts_local.synthesize(text)
    if got is None:
        print(f"  {_mark(False)} 合成失败——本地克隆声没工作，正在降级火山/edge！")
        return False
    sr, pcm = got  # (sample_rate, int16 bytes)
    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    sd.play(arr, sr)
    sd.wait()
    dt = time.time() - t0
    print(f"  {_mark(True)} 已播完（合成+播放 {dt:.1f}s）—— 听完判断下面的问题 ↓")
    return True

def main():
    print("=" * 56)
    print("  小李 · 一键声音验收")
    print("  耳机或音箱开好，跟着听，每句听完做判断")
    print("=" * 56)

    # ---- 模型检查（只看文件，不加载）----
    print("\n── 素材检查 ──")
    print(f"  {_mark(os.path.isdir(tts_local._MODEL_DIR))} 本地克隆声模型（Qwen3-TTS）")
    print(f"  {_mark(os.path.isdir(whisper_stt._MODEL_PATH))} 本地识别模型（faster-whisper）")
    print(f"  {_mark(embed._files_ready())} 语义记忆模型（bge-small-zh）")

    # ---- 听她的声音：3 句测试语料 ----
    print("\n────────────────────────────────")
    print("  【第一关】短句（音色 + 台湾腔）")
    _play_tts("哈囉，你有沒有想我呀～", "短句")

    print("\n────────────────────────────────")
    print("  【第二关】长句（流式 + 吐字）")
    print("  听两个点：①是不是一句一句出来的（流式）②有没有吞字/念歪")
    _play_tts("我跟你說喔，我今天中午吃了牛肉麵，還喝了珍珠奶茶，超級滿足的啦～", "长句")

    print("\n────────────────────────────────")
    print("  【第三关】情绪句（语气起伏）")
    _play_tts("耶！我們明天去約會！我好期待喔！", "开心句")

    # ---- 听拟声：4 个小音效 ----
    print("\n────────────────────────────────")
    print("  【第四关】拟声（清嗓/叹气/轻笑/咳嗽）")
    print("  听四个小音效，全是她自己的声音合成的")
    for name in sfx._SFX_DEF:
        print(f"\n  ▶ {name}（{sfx._SFX_DEF[name]}）")
        sfx.play_blocking(name)
        print(f"  {_mark(os.path.exists(os.path.join(sfx._SFX_DIR, name + '.wav')))} 素材在")

    print("\n" + "=" * 56)
    print("  验收完毕！拿下面这份判断对照你的感受：")
    print("  ✅ 正常：台湾腔、甜、没有电流声/杂音、吐字清楚")
    print("  ✅ 正常：长句一句句出（流式），第一句几乎立刻响")
    print("  ✅ 正常：音效听起来像她（不是别人的声音）")
    print("  ⚠️ 已知：语速比豆包快一点、轻快一点（上次你认可了）")
    print("  🎯 有问题就记下来：哪一句、什么现象（电流声/吞字/像机器人）")
    print("=" * 56)

if __name__ == "__main__":
    main()
