# ============================================
# sherpa-onnx 流式识别验证（2026-08-24 研究阶段）
# 用 gpt-sovits-corpus 的 80 句（有标准文本）测：
#   1. RTF（实时率，CPU 流式）
#   2. 流式 partial 行为（半句输出，用长句演示）
#   3. 与 faster-whisper 的文本对比（粗略准确率）
# 用法：python scripts/sherpa_stream_test.py [--n 5]
# ============================================
import argparse
import glob
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# scripts/ 下运行也能 import xiaoli 包（whisper 对照用）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import opencc
import sherpa_onnx

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models", "sherpa",
                         "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30")
CORPUS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "gpt-sovits-corpus")


def _find(d, *patterns):
    """容错找文件：encoder.onnx / encoder.int8.onnx 都行"""
    for pat in patterns:
        for f in os.listdir(d):
            import fnmatch
            if fnmatch.fnmatch(f, pat):
                return os.path.join(d, f)
    return None


def load_model():
    d = MODEL_DIR
    # 模型文件：encoder/decoder/joiner 是 transducer 结构（int8 版 encoder/joiner 带 .int8）
    enc = _find(d, "encoder*.onnx")
    dec = _find(d, "decoder*.onnx")
    join = _find(d, "joiner*.onnx")
    tok = os.path.join(d, "tokens.txt")
    if enc and dec and join and os.path.exists(tok):
        r = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=tok, encoder=enc, decoder=dec, joiner=join,
            num_threads=4, sample_rate=16000, feature_dim=80,
            decoding_method="greedy_search")
        return r, "transducer"
    # 可能只有一个 model.onnx（zipformer2-ctc）
    m = os.path.join(d, "model.onnx")
    if os.path.exists(m) and os.path.exists(tok):
        r = sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(
            tokens=tok, model=m, num_threads=4,
            sample_rate=16000, feature_dim=80)
        return r, "zipformer2-ctc"
    # paraformer
    pe = os.path.join(d, "encoder.int8.onnx")
    pd = os.path.join(d, "decoder.int8.onnx")
    if os.path.exists(pe) and os.path.exists(pd) and os.path.exists(tok):
        r = sherpa_onnx.OnlineRecognizer.from_paraformer(
            tokens=tok, encoder=pe, decoder=pd, num_threads=4,
            sample_rate=16000, feature_dim=80)
        return r, "paraformer"
    raise SystemExit(f"模型文件不全：{d}")


def read_wav(path):
    import wave
    with wave.open(path) as f:
        assert f.getnchannels() == 1 and f.getsampwidth() == 2
        samples = f.readframes(f.getnframes())
        x = np.frombuffer(samples, dtype=np.int16).astype(np.float32) / 32768
        return x, f.getframerate()


def stream_decode(recognizer, samples, sample_rate, chunk=0.1, show_partial=False):
    """流式喂入，返回 (final_text, partials)"""
    stream = recognizer.create_stream()
    partials = []
    step = int(chunk * sample_rate)
    for i in range(0, len(samples), step):
        stream.accept_waveform(sample_rate, samples[i:i + step])
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        if show_partial and i % (step * 10) == 0:
            p = recognizer.get_result(stream)
            if p:
                partials.append(p)
    # 官方示例：末尾喂 0.66s 静音 padding 再 input_finished（没有它句尾字会丢）
    stream.accept_waveform(sample_rate, np.zeros(int(0.66 * sample_rate), dtype=np.float32))
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    return recognizer.get_result(stream).strip(), partials


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="测几条（默认 5）")
    ap.add_argument("--long", action="store_true", help="演示长句流式 partial")
    args = ap.parse_args()

    print("[OK] 模型目录：", MODEL_DIR, "存在" if os.path.isdir(MODEL_DIR) else "缺失")
    recognizer, kind = load_model()
    print(f"[OK] 模型加载成功（{kind}，num_threads=4）")

    wavs = sorted(glob.glob(os.path.join(CORPUS_DIR, "wavs", "*.wav")))[: args.n]
    if not wavs:
        raise SystemExit(f"没找到语料 wav：{CORPUS_DIR}/wavs/")
    refs = {}
    for t in glob.glob(os.path.join(CORPUS_DIR, "wavs", "*.txt")):
        n = os.path.splitext(os.path.basename(t))[0]
        with open(t, encoding="utf-8") as f:
            refs[n] = f.read().strip()

    print(f"\n=== RTF 实测（{len(wavs)} 条，流式 0.1s chunk）===")
    total_audio = total_time = 0.0
    n_whisper = 0
    for w in wavs:
        name = os.path.splitext(os.path.basename(w))[0]
        samples, sr = read_wav(w)
        t0 = time.time()
        text, partials = stream_decode(recognizer, samples, sr)
        dt = time.time() - t0
        total_audio += len(samples) / sr
        total_time += dt
        dur = len(samples) / sr
        ref = refs.get(name, "?")
        if ref:
            # 归一化：去标点 + 繁转简（两头对到简体）。剩余差异才是
            # 真听错或口语词（喔/哦）——ratio>0.85 视为字形/口语词差异
            import difflib
            cc = opencc.OpenCC("t2s")
            nref = cc.convert("".join(ch for ch in ref if "一" <= ch <= "鿿" or ch.isalnum()))
            ntxt = "".join(ch for ch in text if "一" <= ch <= "鿿" or ch.isalnum())
            if nref == ntxt:
                ok = "[MATCH]"
            elif difflib.SequenceMatcher(None, nref, ntxt).ratio() > 0.85:
                ok = "[一致·字形/口语词]"
            else:
                ok = "[diff]"
        else:
            ok = "?"
        print(f"  {name}  {dur:4.1f}s 识别{dt:4.2f}s  RTF={dt/dur:.3f}  {ok}")
        if ok == "[diff]":
            print(f"      ref : {ref}")
            print(f"      got : {text}")
        # whisper 对照（每条都跑，GPU；语料 24k → 线性重采样到 16k 喂 whisper）
        try:
            from xiaoli import whisper_stt
            import wave as _wave
            with _wave.open(w) as f:
                sr = f.getframerate()
                raw = f.readframes(f.getnframes())
            x16 = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            n_out = int(len(x16) * 16000 / sr)
            xr = np.interp(np.linspace(0, len(x16) - 1, n_out), np.arange(len(x16)), x16)
            wtext = whisper_stt.transcribe(xr.astype(np.int16).tobytes())
            print(f"      whisper对照: {wtext}")
        except Exception as e:
            print(f"      [whisper对照失败：{e}]")

    print(f"\n合计：音频 {total_audio:.1f}s，识别 {total_time:.2f}s，RTF={total_time/total_audio:.3f}（CPU 流式）")

    if args.long:
        print("\n=== 流式 partial 演示（半句出字）===")
        # 找一条最长的语料
        best = max(wavs, key=lambda w: os.path.getsize(w))
        samples, sr = read_wav(best)
        text, partials = stream_decode(recognizer, samples, sr, show_partial=True)
        print(f"  音频：{len(samples)/sr:.1f}s  最终：{text}")
        for i, p in enumerate(partials):
            print(f"    partial{i}: {p}")


if __name__ == "__main__":
    main()
