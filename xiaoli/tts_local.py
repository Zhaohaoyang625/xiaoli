# ============================================
# 小李的本地语音合成（2026-08-22）
# Qwen3-TTS-12Hz-1.7B-Base（阿里开源，ModelScope 官方源下载）声音克隆：
#   参考音频 = 火山"甜美台妹"合成的台湾腔台词 → ICL 克隆模式
#   （ECAPA-TDNN 说话人嵌入 + codec token 前置）→ 合成出"她自己的声音"，
#   以后合成全本地，火山 TTS 月费（20-30 元）也省了。
# 多段参考（2026-08-22 用户试听"音色不像"后升级）：
#   data/ref_audio_1~5.wav 覆盖疑问/撒娇/兴奋/温柔/叙述 5 种语气，
#   每段文本在 data/ref_texts.txt 按行对应——小模型吃到的说话人特征越多越像。
#   没有多段 → 回退单段 ref_audio.wav + ref_text.txt。
# 后处理（2026-08-22 用户试听"语速偏急"后加）：
#   合成 → 变速对齐火山节奏（TTS_LOCAL_SPEED，librosa 保音调）→ 响度归一化
#   （TTS_LOCAL_TARGET_RMS，切音色时音量感一致）。
# 设计原则：永远有声音——
#   懒加载 + 启动后台预热（聊天时模型先装好，第一次说话不等）；
#   模型未就绪/加载失败/合成失败 → 返回 None → voice.py 降级火山（体验不断）。
#   显存：TTS bf16 ~4GB 与 whisper int8 ~4GB 并存（8GB 吃紧，但半双工错峰推理；
#   真 OOM → 降级链兜底，语音/识别永不间断）。
# ============================================

import glob
import os
import threading

import numpy as np

from xiaoli import config, paths

_MODEL_DIR = os.path.join(paths.MODELS_DIR, "Qwen3-TTS-12Hz-1.7B-Base")
_REF_AUDIO = os.path.join(paths.DATA_DIR, "ref_audio.wav")  # 单段回退
_REF_TEXT_FILE = os.path.join(paths.DATA_DIR, "ref_text.txt")

_model = None
_clone_prompt = None
_model_lock = threading.Lock()
_loading = False


def _load_ref():
    """加载参考音频 + 对应文本（ICL 模式要求文本与音频完全匹配）。
    优先多段：ref_audio_1~5.wav（5 种语气）**拼接成一条长参考**——
    官方 API 的多段是 batch 语义（一段参考配一句目标文本），不是混合；
    拼接成单段后 ECAPA-TDNN 对整段提取说话人嵌入 → 天然融合多种语气特征，
    ICL 上下文也更长（17s > 10s，克隆更稳）。段间 0.25s 静音防边界爆音。
    没有多段回退单段（ref_audio.wav + ref_text.txt）。
    返回 (audios: List, texts: List) 或 None"""
    multi = sorted(glob.glob(os.path.join(paths.DATA_DIR, "ref_audio_*.wav")))
    if multi:
        texts_file = os.path.join(paths.DATA_DIR, "ref_texts.txt")
        if os.path.isfile(texts_file):
            with open(texts_file, encoding="utf-8") as f:
                texts = [line.strip() for line in f if line.strip()]
            if len(texts) == len(multi):
                import wave
                parts = []
                for fn in multi:
                    with wave.open(fn, "rb") as w:
                        sr = w.getframerate()
                        parts.append(
                            np.frombuffer(w.readframes(w.getnframes()),
                                          dtype=np.int16).astype(np.float32) / 32768.0
                        )
                gap = np.zeros(int(sr * 0.25), dtype=np.float32)
                merged = np.concatenate([x for p in parts for x in (p, gap)][:-1])
                return [(merged, sr)], ["。".join(texts)]
    if os.path.isfile(_REF_AUDIO) and os.path.isfile(_REF_TEXT_FILE):
        with open(_REF_TEXT_FILE, encoding="utf-8") as f:
            return [_REF_AUDIO], [f.read().strip()]
    return None


def _post_process(wav, sr=24000):
    """合成后处理：变速对齐火山节奏 + 响度归一化。
    变速优先 WSOLA（自写，保瞬态/重音清晰——librosa phase vocoder 有
    "重音延迟"听感），WSOLA 不支持变快时回退 librosa。
    输入/输出都是 float32 波形；任何一步失败 → 原样返回（永远有声音）。
    变速后 wav 变长——调用方按 len/sr 算时长，自动适配口型/流式。"""
    try:
        if config.TTS_LOCAL_SPEED != 1.0:
            speed = float(config.TTS_LOCAL_SPEED)
            if speed > 1.0:
                from xiaoli import wsola
                wav = wsola.time_stretch(wav, speed, sr)
            else:
                import librosa
                # 注意：time_stretch 的 rate>1 是变快（时长÷rate），变慢要用倒数——踩过
                wav = librosa.effects.time_stretch(wav, rate=1.0 / speed)
        rms = float(np.sqrt((wav ** 2).mean()))
        if rms > 1e-5:
            wav = wav * (config.TTS_LOCAL_TARGET_RMS / rms)
        return np.clip(wav, -1.0, 1.0)
    except Exception:
        return wav


def _load():
    """加载模型 + 一次性构建克隆 prompt（参考音频只编码一次，之后多次复用，快）。
    并发安全：正在加载时其他线程直接返回 False（降级火山）"""
    global _model, _clone_prompt, _loading
    with _model_lock:
        if _model is not None:
            return True
        if _loading:
            return False
        if not os.path.isdir(_MODEL_DIR):
            print(f"  [本地合成：模型不存在 {_MODEL_DIR}，请先下载]")
            return False
        _loading = True
    try:
        import torch
        from qwen_tts import Qwen3TTSModel
        _model = Qwen3TTSModel.from_pretrained(
            _MODEL_DIR,
            device_map="cuda:0",
            dtype=torch.bfloat16,
            attn_implementation="eager",  # 不装 flash-attn（要编译），eager 够用
        )
        ref = _load_ref()
        if ref:
            audios, texts = ref
            _clone_prompt = _model.create_voice_clone_prompt(
                ref_audio=audios, ref_text=texts,
            )
            print(f"  [本地合成就绪：Qwen3-TTS 声音克隆（小李音色，{len(audios)} 段参考）]")
        else:
            print("  [本地合成就绪（缺参考音频/文本 → 无克隆，用默认音色）]")
        return True
    except Exception as e:
        _model = None
        print(f"  [本地合成模型加载失败：{e}]（自动用火山）")
        return False
    finally:
        _loading = False


def preload():
    """启动时后台预热（不阻塞聊天）"""
    if not config.TTS_LOCAL:
        return
    threading.Thread(target=_load, daemon=True).start()


def synthesize(text):
    """本地克隆合成一段话 → (sample_rate, int16 pcm bytes)；失败/未就绪 → None。
    调用方（voice.py）拿到 None 自动降级火山——本地声音永远不会"没声音" """
    if not config.TTS_LOCAL or not _load():
        return None
    try:
        wavs, sr = _model.generate_voice_clone(
            text=text,
            language="Chinese",
            voice_clone_prompt=_clone_prompt,
            non_streaming_mode=True,
        )
        wav = np.asarray(wavs[0])
        if wav.ndim > 1:
            wav = wav[0]  # (1, T) → (T,)
        wav = _post_process(wav, sr=sr)  # 变速（语速对齐）+ 响度归一化（切音色音量感一致）
        pcm = (wav * 32767.0).astype(np.int16).tobytes()
        # 显存诊断可选（torch 缺失/诊断失败不阻塞合成）。
        # 峰值 = max_memory_allocated（caching allocator 不会自动回落），
        # 不是 allocated - reserved（reserved 是缓存池 ≥ allocated，相减必负——踩过的坑）
        try:
            import torch
            peak = torch.cuda.max_memory_allocated() / 1e9
            now = torch.cuda.memory_allocated() / 1e9
            print(f"  [本地合成OK {len(pcm)/(sr*2):.1f}s 显存当前{now:.1f}GB 峰值{peak:.1f}GB]")
        except Exception:
            print(f"  [本地合成OK {len(pcm)/(sr*2):.1f}s]")
        return sr, pcm
    except Exception as e:
        print(f"  [本地合成失败：{e}]（自动用火山）")
        return None
