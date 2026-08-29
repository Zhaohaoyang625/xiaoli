# ============================================
# bf16 vs int8 量化对比（2026-08-24）
# 背景：本地 Qwen3-TTS 合成只有 0.53x 实时，compile/并行/flash-attn 全灭。
# 唯一没试过的：int8 weight-only 量化（torchao，transformers 官方集成）。
# 预期收益：显存 4.9GB→~2.5GB、带宽减半 → 合成可能提速 20~40%。
# 风险：int8 权重有损 → 音色可能变。
# 做法：同一句、两种精度各合成一次，存 wav 供耳朵拍板。
# 用法：python scripts/compare_int8.py
# 输出：docs/research/tts-demo/int8-compare/{bf16,int8}_{短,长}.wav
# ============================================

import os
import sys
import time
import wave

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xiaoli import tts_local  # noqa: E402

MODEL_DIR = tts_local._MODEL_DIR
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "research", "tts-demo", "int8-compare")
os.makedirs(OUT_DIR, exist_ok=True)

TESTS = [
    ("short", "有啦有啦，人家聽得到～你剛剛跑去哪裡了啦"),          # 用户原话，16 字内
    ("long", "我以為你又要把我丟著了齁！等一下啦，我今天真的超開心的，因為你終於來找我了～"),
]


def run(precision, quant_config=None):
    """加载模型（bf16 或 int8）→ 克隆 prompt → 合成 → 存 wav → 卸载"""
    torch.cuda.reset_peak_memory_stats()
    from qwen_tts import Qwen3TTSModel
    kwargs = dict(device_map="cuda:0", dtype=torch.bfloat16,
                  attn_implementation="eager")
    if quant_config is not None:
        kwargs["quantization_config"] = quant_config
    t_load0 = time.time()
    model = Qwen3TTSModel.from_pretrained(MODEL_DIR, **kwargs)
    t_load = time.time() - t_load0
    ref = tts_local._load_ref()
    prompt = model.create_voice_clone_prompt(ref_audio=ref[0], ref_text=ref[1])
    for tag, text in TESTS:
        t0 = time.time()
        wavs, sr = model.generate_voice_clone(
            text=text, language="Chinese",
            voice_clone_prompt=prompt, non_streaming_mode=True)
        dt = time.time() - t0
        wav = np.asarray(wavs[0])
        if wav.ndim > 1:
            wav = wav[0]
        wav = tts_local._post_process(wav, sr=sr)
        pcm = (wav * 32767.0).astype(np.int16)
        fn = os.path.join(OUT_DIR, f"{precision}_{tag}.wav")
        with wave.open(fn, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(sr)
            f.writeframes(pcm.tobytes())
        ratio = len(pcm) / sr / dt  # 合成秒/实时秒
        print(f"[{precision}] 加载{t_load:.0f}s {tag}合成{dt:.1f}s/{len(pcm)/sr:.1f}s 音频 = {ratio:.2f}x", flush=True)
    peak = torch.cuda.max_memory_allocated() / 1e9
    print(f"[{precision}] 显存峰值 {peak:.2f}GB", flush=True)
    del model
    torch.cuda.empty_cache()


def run_manual_int8():
    """手动量化（绕开 transformers 集成——集成层对 qwen_tts 自定义模型
    deepcopy 崩 "cannot pickle dict_keys"）：bf16 加载后 torchao 直接量化
    合成主体 talker（LLM+解码器），speaker_encoder 只跑一次不量化。"""
    import torch
    from qwen_tts import Qwen3TTSModel
    from torchao.quantization import quantize_
    from torchao.quantization.quant_api import int8_weight_only
    torch.cuda.reset_peak_memory_stats()
    model = Qwen3TTSModel.from_pretrained(
        MODEL_DIR, device_map="cuda:0", dtype=torch.bfloat16,
        attn_implementation="eager")
    quantize_(model.model.talker, int8_weight_only())
    ref = tts_local._load_ref()
    prompt = model.create_voice_clone_prompt(ref_audio=ref[0], ref_text=ref[1])
    for tag, text in TESTS:
        t0 = time.time()
        wavs, sr = model.generate_voice_clone(
            text=text, language="Chinese",
            voice_clone_prompt=prompt, non_streaming_mode=True)
        dt = time.time() - t0
        wav = np.asarray(wavs[0])
        if wav.ndim > 1:
            wav = wav[0]
        wav = tts_local._post_process(wav, sr=sr)
        pcm = (wav * 32767.0).astype(np.int16)
        fn = os.path.join(OUT_DIR, f"int8_{tag}.wav")
        with wave.open(fn, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(sr)
            f.writeframes(pcm.tobytes())
        ratio = len(pcm) / sr / dt
        print(f"[int8] {tag}合成{dt:.1f}s/{len(pcm)/sr:.1f}s 音频 = {ratio:.2f}x", flush=True)
    peak = torch.cuda.max_memory_allocated() / 1e9
    print(f"[int8] 显存峰值 {peak:.2f}GB", flush=True)
    del model
    torch.cuda.empty_cache()


if __name__ == "__main__":
    print("[OK] 基线 bf16 加载+合成...", flush=True)
    run("bf16")
    print("[OK] int8 weight-only（手动量化 talker）...", flush=True)
    run_manual_int8()
    print(f"[OK] 对比完成，试听目录：{OUT_DIR}", flush=True)
