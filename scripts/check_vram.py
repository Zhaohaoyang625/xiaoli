# ============================================
# 显存共存验证（2026-08-22）：whisper(int8) + Qwen3-TTS(bf16) 能否共存 8GB
# 模拟 chat.py 真实运行时序：先预热 whisper → 再预热 TTS → 合成一句
# ============================================

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch
from xiaoli import tts_local, whisper_stt


def vram(tag):
    used = torch.cuda.memory_allocated() / 1e9
    print(f"  [{tag}] 显存占用 {used:.2f}GB（峰值 {torch.cuda.max_memory_allocated()/1e9:.2f}GB）")


def main():
    torch.cuda.reset_peak_memory_stats()
    print("1) 加载 whisper（int8）…")
    t0 = time.time()
    ok_w = whisper_stt._load()
    print(f"   whisper 就绪，耗时 {time.time()-t0:.1f}s" if ok_w else "   whisper 加载失败！")
    vram("whisper 常驻")

    print("2) 加载 Qwen3-TTS（bf16）…")
    t0 = time.time()
    ok = tts_local._load()
    print(f"   TTS 就绪，耗时 {time.time()-t0:.1f}s")
    vram("whisper+TTS 常驻")
    if not ok:
        print("TTS 加载失败！")
        return 1

    print("3) 克隆合成一句…")
    t0 = time.time()
    r = tts_local.synthesize("寶貝，人家好想你喔～今天有沒有想我呀？")
    print(f"   合成耗时 {time.time()-t0:.1f}s")
    vram("合成后")
    if r is None:
        print("合成失败！")
        return 1

    print("4) 再合成一句（验证连续推理无 OOM）…")
    r2 = tts_local.synthesize("你今天有沒有吃飯？人家煮了火鍋等你回來耶～")
    print("   第二句 OK" if r2 else "   第二句失败！")

    total = torch.cuda.memory_allocated() / 1e9
    peak = torch.cuda.max_memory_allocated() / 1e9
    print(f"\n结论：当前占用 {total:.2f}GB / 峰值 {peak:.2f}GB / 8GB 显存")
    if peak > 7.5:
        print("⚠️ 峰值接近上限，半双工时序下可能 OOM → 降级链兜底")
    else:
        print("✅ 共存安全（半双工错峰推理时更宽裕）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
