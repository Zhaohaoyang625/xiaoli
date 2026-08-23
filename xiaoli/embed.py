# ============================================
# 本地语义向量（v2 O3，bge-small-zh-v1.5 ONNX）
# 为什么上真 embedding：字符 bigram 召回"字面"相关，语义相关抓不到
# （他记忆"怕高"，今天说"要去爬山"——无共同字符，旧检索漏掉）。
# 方案：bge-small-zh-v1.5（中文语义模型，512 维，~95MB ONNX）+ onnxruntime 推理，
# 纯 Python WordPiece 分词（不装 transformers/torch，保持项目轻）。
# 优雅降级：模型文件缺失/加载失败 → embed() 返回 None → memory.py 退回字符向量。
# 下载（2026-08-22，hf-mirror 单文件，GitHub/HF 直连不通的替代）：
#   models/bge-small-zh/model.onnx + vocab.txt
# ============================================

import os
from xiaoli import paths  # 统一路径（数据/模型在项目根）
import re

import numpy as np

MODEL_DIR = os.path.join(paths.MODELS_DIR, "bge-small-zh")
MODEL_PATH = os.path.join(MODEL_DIR, "model.onnx")
VOCAB_PATH = os.path.join(MODEL_DIR, "vocab.txt")

# bge 官方建议：查询端加中文指令，检索分数明显更高（文档端不加）
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

MAX_LEN = 64  # 记忆条目和用户输入都是短句，64 足够，推理更快

_session = None  # 惰性加载的 ONNX 会话（全局只加载一次）
_vocab = None
_failed = False


def _files_ready():
    """模型 + 词表是否都在（启动自检用，不加载不占显存）"""
    return os.path.exists(MODEL_PATH) and os.path.exists(VOCAB_PATH)


def _load():
    """加载词表 + ONNX 会话（惰性，只成功一次；失败后不再重试）"""
    global _session, _vocab, _failed
    if _session is not None or _failed:
        return _session is not None
    try:
        if not (os.path.exists(MODEL_PATH) and os.path.exists(VOCAB_PATH)):
            _failed = True
            return False
        import onnxruntime as ort
        _vocab = {}
        with open(VOCAB_PATH, encoding="utf-8") as f:
            for i, line in enumerate(f):
                _vocab[line.strip()] = i
        _session = ort.InferenceSession(
            MODEL_PATH, providers=["CPUExecutionProvider"])
        return True
    except Exception as e:
        _failed = True
        print(f"  [向量模型加载失败，退回字符向量：{e}]")
        return False


# ---------- 纯 Python WordPiece（BERT 中文分词） ----------

def _basic_tokens(text):
    """BasicTokenizer 简化版：中文逐字、英文数字整段、其余忽略"""
    return re.findall(r"[一-鿿]|[A-Za-z0-9]+", text)


def _wordpiece_tokens(text):
    """WordPiece：贪心最长匹配 + ## 前缀（英文数字拆子词，中文单字直接命中）"""
    out = []
    for piece in _basic_tokens(text):
        if not piece:
            continue
        if all("一" <= c <= "鿿" for c in piece):
            # 中文：整字若在词表直接收（词表含常用字与多字词）
            if piece in _vocab:
                out.append(piece)
            else:
                out.append("[UNK]")
            continue
        # 英文/数字：WordPiece 子词切分
        start, chars = 0, list(piece)
        while start < len(chars):
            end = len(chars)
            cur = None
            while start < end:
                sub = "".join(chars[start:end])
                key = sub if start == 0 else "##" + sub
                if key in _vocab:
                    cur = key
                    break
                end -= 1
            if cur is None:
                out.append("[UNK]")
                start += 1
            else:
                out.append(cur)
                start = end
    return out


def tokenize(text):
    """中文文本 → (input_ids, attention_mask)，长度截到 MAX_LEN"""
    ids, mask = [101], [1]  # [CLS]
    for tok in _wordpiece_tokens(text):
        if len(ids) >= MAX_LEN - 1:
            break
        idx = _vocab.get(tok, _vocab.get("[UNK]", 100))
        ids.append(idx)
        mask.append(1)
    ids.append(102)  # [SEP]
    mask.append(1)
    pad = MAX_LEN - len(ids)
    ids += [0] * pad
    mask += [0] * pad
    return np.array([ids], dtype=np.int64), np.array([mask], dtype=np.int64)


def embed(text, is_query=False):
    """文本 → 512 维归一化向量（numpy）。模型不可用 → None（调用方降级）。
    is_query：查询端加 bge 官方中文指令（记忆条目是文档端，不加）"""
    if not _load():
        return None
    try:
        if is_query:
            text = QUERY_INSTRUCTION + text
        ids, mask = tokenize(text)
        # 模型输入三件套：input_ids / attention_mask / token_type_ids（全 0，单句无分段）
        out = _session.run(None, {
            "input_ids": ids,
            "attention_mask": mask,
            "token_type_ids": np.zeros_like(ids),
        })
        vec = out[0][0, 0, :]  # [CLS] → [768]
        n = float(np.linalg.norm(vec))
        if n < 1e-9:
            return None
        return vec / n
    except Exception:
        return None


def cosine(a, b):
    """两个归一化向量的余弦（相等向量 → 1.0）"""
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ok = _load()
    if not ok:
        print("模型未就绪（model.onnx/vocab.txt 缺失）→ 检查 models/bge-small-zh/")
    else:
        for s in ["他怕高", "我今天要去爬山", "他喜欢喝奶茶"]:
            v = embed(s)
            print(f"{s} → dim={v.shape} 范数={float(np.linalg.norm(v)):.4f}")
        q = embed("我今天要去爬山", is_query=True)
        print("怕高 vs 爬山(查询):", round(cosine(q, embed("他怕高")), 4))
        print("怕高 vs 怕黑(查询):", round(cosine(q, embed("他怕黑")), 4))
        print("怕高 vs 奶茶(查询):", round(cosine(q, embed("他喜欢喝奶茶")), 4))
