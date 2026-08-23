# ============================================
# WSOLA 时间拉伸（2026-08-22）
# 为什么有它：librosa 的 phase vocoder 变速在辅音/重音起音处产生相位模糊
# （用户听感："重音延迟"）→ 换波形相似性重叠相加（WSOLA）：
#   变慢时不是"拉伸频谱"，而是从原波形里挑"最像上一段"的片段拼出来——
#   局部波形（含瞬态起音）被原样保留，只有节奏变慢。
# 实现：帧长 40ms（语音标准），输出每帧前进 W/alpha，
#   每个新帧在搜索窗（±30ms）内找与输出重叠区最相似的输入片段，
#   交叉淡化叠入。纯 numpy，无第三方依赖。
# 用法：wsola.time_stretch(x, alpha, sr)，alpha > 1 = 变慢。
# 参考：教科书 WSOLA（W. Verhelst & M. Roelands, 1993）。
# ============================================

import numpy as np

_FRAME = 0.040    # 帧长 40ms
_DELTA = 0.030     # 搜索窗半径 30ms


def time_stretch(x, alpha, sr):
    """WSOLA 时间拉伸。alpha > 1 → 变慢（输出 ≈ 输入 × alpha）。
    alpha == 1.0 → 原样拷贝。输入 float32 数组。"""
    x = np.asarray(x, dtype=np.float32)
    n = len(x)
    if n == 0 or alpha <= 0:
        return x
    if alpha == 1.0:
        return x.copy()
    if alpha < 1.0:
        # 变快需要不同的实现（帧间有 gap 而非重叠）——调用方（_post_process）
        # 对变快会用 librosa phase vocoder 回退，这里只保证不崩
        return x

    W = int(_FRAME * sr)              # 帧长（样本）
    Hs_out = int(W / alpha)           # 输出帧前进（重叠 = W - Hs_out）
    if Hs_out <= 0 or Hs_out >= W:
        return x                      # 参数异常：保底原样返回
    Hs_in = max(1, int(Hs_out / alpha))  # 输入帧前进（≠ Hs_out！混用会让音调随 alpha 偏移）
    ov = W - Hs_out                   # 相邻输出帧的重叠区
    delta = int(_DELTA * sr)          # 搜索窗半径

    out_len = int(n * alpha) + W
    out = np.zeros(out_len, dtype=np.float32)

    # 初帧：直接取头 W 个样本
    out[:W] = x[:W]
    pos = Hs_out      # 输出当前位置（第 2 帧起点）
    k = Hs_in         # 输入理想起点

    # 交叉淡化窗（线性；重叠区新片段淡入、旧内容淡出）
    fade_in = np.linspace(0.0, 1.0, ov, dtype=np.float32)
    fade_out = 1.0 - fade_in

    while k < n and pos + W <= out_len:
        lo = max(0, k - delta)
        hi = min(n - W, k + delta)
        ref = out[pos - ov:pos]
        # 在搜索窗 [lo, hi] 内找与输出重叠区最相似的输入片段（向量化）。
        # 坑：x[lo:hi+W] 的滑动窗口候选起点会到 hi+Hs（越出搜索上限，片段不够长）→
        # 窗口只取到 hi+ov，候选起点正好落在 [lo, hi]
        if hi > lo:
            cands = np.lib.stride_tricks.sliding_window_view(x[lo:hi + ov], ov)
            errs = ((cands - ref) ** 2).sum(axis=1)
            best_q = lo + int(np.argmin(errs))
        else:
            best_q = min(max(k, 0), n - W)  # 搜索窗为空（输入尾部）→ 理想位置
        seg = x[best_q:best_q + W]
        # 重叠区交叉淡化 + 尾部直通
        out[pos:pos + ov] = out[pos:pos + ov] * fade_out + seg[:ov] * fade_in
        out[pos + ov:pos + W] = seg[ov:]
        pos += Hs_out
        k += Hs_in

    return out[:pos]  # 裁到实际写入位置（尾部零填充剪掉）


if __name__ == "__main__":
    # 自测：正弦波变慢后音调保持（过零率不变 = 频率不变）
    sr = 24000
    t = np.arange(int(sr * 2.0)) / sr
    x = np.sin(2 * np.pi * 220 * t).astype(np.float32)  # 220Hz A3
    a = 1.15
    y = time_stretch(x, a, sr)
    zc = np.sum(np.abs(np.diff(np.sign(y))) > 0) / len(y) * sr / 2
    print(f"alpha={a}: 时长 {len(x)/sr:.1f}s→{len(y)/sr:.1f}s "
          f"频率 {zc:.1f}Hz（原 220Hz，偏差 {(zc-220)/220*100:+.1f}%）")
    assert abs(len(y) / sr - 2.3) < 0.1, "时长≈输入×1.15"
    assert abs(zc - 220) < 5, "频率保持（WSOLA 保音调）"
    print("WSOLA 自测通过")
