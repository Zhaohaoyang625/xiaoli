# ============================================
# 本地合成实测（2026-08-22）：Qwen3-TTS 声音克隆
# 用法：python scripts/test_local_tts.py
# 把一句"小李的话"用本地克隆音色合成 → data/tts_local_demo.wav（可播放对比火山版）
# 顺便打印：加载耗时/合成耗时/显存占用（验证 RTX 5060 8GB 是否扛得住）
# ============================================

import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaoli import paths, tts_local

TEXT = "寶貝，人家好想你喔～今天有沒有想我呀？"


def main():
    t0 = time.time()
    print("加载模型（第一次会慢，后续预热后快）…")
    ok = tts_local._load()
    print(f"加载耗时 {time.time()-t0:.1f}s")

    if not ok:
        print("加载失败（自动降级火山，聊天不受影响）")
        return 1

    t1 = time.time()
    r = tts_local.synthesize(TEXT)
    print(f"合成耗时 {time.time()-t1:.1f}s")

    if r is None:
        print("合成失败（自动降级火山）")
        return 1

    sr, pcm = r
    out = os.path.join(paths.DATA_DIR, "tts_local_demo.wav")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    print(f"✅ 合成成功：{len(pcm)/(sr*2):.1f}s @ {sr}Hz → {out}")
    print("（对比火山版：python -X utf8 -c \"from xiaoli import tts_api,wave; "
          "pcm=tts_api.synthesize('同款文本'); f=wave.open('data/tts_volcano_demo.wav','wb'); "
          "f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000); f.writeframes(pcm)\"）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
