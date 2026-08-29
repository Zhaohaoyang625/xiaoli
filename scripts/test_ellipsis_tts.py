# ============================================
# 省略号三引擎念法验证（复盘 C-P1-2，2026-08-23）
# 同一句含 …… 的台词，三个引擎各合成一份 wav：
#   本地克隆（Qwen3-TTS，她自己的声音）/ 火山（晓臻）/ edge（晓晓，普通话）
# 听：省略号处引擎有没有"拖沓停顿"？没有 → 播放层要给省略号句尾补停顿。
# 用法：python scripts/test_ellipsis_tts.py
# 输出：docs/research/tts-demo/ellipsis_*.wav（可直接双击试听）
# ============================================

import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import tts_api, tts_local, voice

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "research", "tts-demo")

TEXTS = [
    ("短省略号", "我今天呀……心情挺好的……你呢？"),
    ("句尾省略号", "就是……有点想你了……"),
]


def _save(name, sr, pcm):
    if not pcm:
        print(f"  [!!] {name} 合成失败（引擎不可用？）")
        return
    path = os.path.join(OUT, f"ellipsis_{name}.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    sec = len(pcm) / (sr * 2)
    print(f"  [OK] {name} → {sec:.1f}s {path}")


def main():
    os.makedirs(OUT, exist_ok=True)
    for label, text in TEXTS:
        print(f"\n=== {label}：{text}")
        # 本地克隆（Qwen3-TTS 12Hz，24k）：返回 (sr, pcm)
        try:
            r = tts_local.synthesize(text)
            if r:
                _save(f"{label}_本地克隆", r[0], r[1])
            else:
                print("  [!!] 本地克隆不可用（模型未就绪）")
        except Exception as e:
            print(f"  [!!] 本地克隆：{e}")
        # 火山（晓臻，24k）：返回原始 PCM bytes
        try:
            _save(f"{label}_火山", 24000, tts_api.synthesize(text))
        except Exception as e:
            print(f"  [!!] 火山：{e}")
        # edge（晓晓，24k，降级路径）：返回 (sr, pcm)
        try:
            r = voice._edge_pcm(text)
            if r:
                _save(f"{label}_edge", r[0], r[1])
            else:
                print("  [!!] edge 不可用")
        except Exception as e:
            print(f"  [!!] edge：{e}")
    print("\n完成！去 docs/research/tts-demo/ 双击听：")
    print("  省略号处引擎有停顿 → 播放层不用管；没有 → 播放层给省略号句尾补停顿")


if __name__ == "__main__":
    main()
