# ============================================
# 语义向量 embed 单测（2026-08-23 补缺口）
# bge-small-zh ONNX：分词/归一化/余弦/降级 全路径
# 真推理测试在模型下载后自动启用（skipUnless）
# ============================================

import os
import unittest
import numpy as np
from unittest import mock

from xiaoli import embed


class TestCosine(unittest.TestCase):
    def test_identical_vectors(self):
        v = embed.cosine(np.array([1.0, 0.0]), np.array([1.0, 0.0]))
        self.assertAlmostEqual(v, 1.0)

    def test_orthogonal_vectors(self):
        v = embed.cosine(np.array([1.0, 0.0]), np.array([0.0, 1.0]))
        self.assertAlmostEqual(v, 0.0, places=6)

    def test_none_inputs_zero(self):
        self.assertEqual(embed.cosine(None, np.array([1.0])), 0.0)
        self.assertEqual(embed.cosine(np.array([1.0]), None), 0.0)


class TestBasicTokens(unittest.TestCase):
    def test_chinese_char_by_char(self):
        self.assertEqual(embed._basic_tokens("你好"), ["你", "好"])

    def test_english_whole_word(self):
        self.assertEqual(embed._basic_tokens("hello"), ["hello"])

    def test_mixed_with_punct_ignored(self):
        self.assertEqual(embed._basic_tokens("怕高！abc 123。"),
                         ["怕", "高", "abc", "123"])


class TestWordPieceTokens(unittest.TestCase):
    """mock 迷你词表测 WordPiece 逻辑（不依赖真词表）"""

    def setUp(self):
        self._saved = embed._vocab
        embed._vocab = {"你": 0, "好": 1, "hello": 2, "##lo": 3, "[UNK]": 4}

    def tearDown(self):
        embed._vocab = self._saved

    def test_known_chinese_chars(self):
        self.assertEqual(embed._wordpiece_tokens("你好"), ["你", "好"])

    def test_unknown_char_unks(self):
        self.assertEqual(embed._wordpiece_tokens("齁"), ["[UNK]"])

    def test_english_subwords(self):
        """'hello' 全词命中；'helloworld' 拆成 hello + ##world(→UNK)"""
        embed._vocab["##world"] = 5
        self.assertEqual(embed._wordpiece_tokens("helloworld"), ["hello", "##world"])

    def test_unknown_english_unks(self):
        """'xyzzy' 不在词表，逐个字符拆都是 [UNK]"""
        self.assertEqual(embed._wordpiece_tokens("xyzzy"), ["[UNK]"] * 5)


class TestTokenize(unittest.TestCase):
    def setUp(self):
        self._saved = embed._vocab
        embed._vocab = {"你": 0, "好": 1, "[UNK]": 2, "[CLS]": 101, "[SEP]": 102}

    def tearDown(self):
        embed._vocab = self._saved

    def test_shape_and_boundaries(self):
        ids, mask = embed.tokenize("你好")
        self.assertEqual(ids.shape, (1, embed.MAX_LEN))
        self.assertEqual(ids[0][0], 101)            # [CLS]
        self.assertEqual(ids[0][1], 0)              # 你
        self.assertEqual(ids[0][2], 1)              # 好
        self.assertEqual(ids[0][3], 102)            # [SEP]
        self.assertEqual(mask[0][:4].tolist(), [1, 1, 1, 1])
        self.assertEqual(mask[0][-1], 0)            # 填充区 mask=0


class TestEmbed(unittest.TestCase):
    def test_model_missing_returns_none(self):
        """模型没下载 → None（调用方降级字符向量）——当前机器就是这个状态"""
        with mock.patch.object(embed, "_load", return_value=False):
            self.assertIsNone(embed.embed("怕高"))
            self.assertIsNone(embed.embed("要去爬山", is_query=True))

    def test_normalized_output(self):
        """假 session 返回固定向量 → 归一化 + is_query 加指令前缀"""
        fake = mock.Mock()
        fake.run.return_value = [np.array([[[3.0, 4.0, 0.0]]])]  # [1,1,3]
        tok_call = {}
        with mock.patch.object(embed, "_load", return_value=True), \
             mock.patch.object(embed, "_session", fake), \
             mock.patch.object(embed, "tokenize",
                               return_value=(np.zeros((1, embed.MAX_LEN), dtype=np.int64),
                                             np.ones((1, embed.MAX_LEN), dtype=np.int64))) as tok_mock:
            vec = embed.embed("测试", is_query=True)
            tok_call["text"] = tok_mock.call_args[0][0]  # 在 patch 作用域内取参数
        self.assertAlmostEqual(float(np.linalg.norm(vec)), 1.0, places=6)
        self.assertAlmostEqual(vec[0], 0.6, places=6)  # 3/5
        self.assertAlmostEqual(vec[1], 0.8, places=6)  # 4/5
        self.assertTrue(tok_call["text"].startswith(embed.QUERY_INSTRUCTION))

    def test_embed_exception_returns_none(self):
        with mock.patch.object(embed, "_load", return_value=True), \
             mock.patch.object(embed, "_session", mock.Mock(
                 side_effect=RuntimeError("推理炸了"))):
            self.assertIsNone(embed.embed("测试"))


@unittest.skipUnless(
    os.path.exists(embed.MODEL_PATH) and os.path.exists(embed.VOCAB_PATH),
    "bge 模型未下载（models/bge-small-zh/）——语义记忆在降级运行")
class TestEmbedReal(unittest.TestCase):
    """真模型推理：v2 记忆引擎的核心承诺——
    '怕高'和'要去爬山'（无共同字符）语义相关，余弦应高于无关句"""

    def test_semantic_beat_character(self):
        a = embed.embed("他记忆里：怕高")
        similar = embed.embed("要去爬山", is_query=True)
        unrelated = embed.embed("今天天气很好", is_query=True)
        self.assertIsNotNone(a)
        self.assertIsNotNone(similar)
        sim = embed.cosine(a, similar)
        unrel = embed.cosine(a, unrelated)
        self.assertGreater(sim, unrel)
        self.assertGreater(sim, 0.3)  # 语义相关阈值

    def test_identical_texts_near_one(self):
        a = embed.embed("他怕高")
        b = embed.embed("他怕高")
        self.assertGreater(embed.cosine(a, b), 0.99)


if __name__ == "__main__":
    unittest.main()


class TestMemoryChainEmbed(unittest.TestCase):
    """2026-08-23 修复回归：memory.py 的语义链路必须真走 bge——
    之前包内裸 `import embed` 静默失败 → _fact_vec 永远 None → 召回一直降级 TF-IDF
    （假成功：模型在、测试也绿，但 memory 链路从没真正用过向量）。"""

    @unittest.skipUnless(
        os.path.exists(embed.MODEL_PATH) and os.path.exists(embed.VOCAB_PATH),
        "bge 模型未下载")
    def test_fact_vec_real_embedding(self):
        """_fact_vec 返回真 512 维向量（不再 None）"""
        from xiaoli import memory
        v = memory._fact_vec("他怕高")
        self.assertIsNotNone(v, "语义链路修复后必须有向量")
        self.assertEqual(len(v), 512)

    def test_fact_vec_cache_hit(self):
        """同内容两次 → embed 只调一次（缓存复用）"""
        from xiaoli import memory
        memory._EMBED_CACHE.clear()
        fake = mock.Mock()
        fake.run.return_value = [np.array([[[1.0, 0.0, 0.0]]])]
        with mock.patch.object(embed, "_load", return_value=True), \
             mock.patch.object(embed, "_session", fake), \
             mock.patch.object(embed, "tokenize",
                               return_value=(np.zeros((1, embed.MAX_LEN), dtype=np.int64),
                                             np.ones((1, embed.MAX_LEN), dtype=np.int64))):
            memory._fact_vec("测试内容")
            memory._fact_vec("测试内容")
        self.assertEqual(fake.run.call_count, 1, "缓存命中不该重复推理")

    def test_cache_evicts_half_not_all(self):
        """缓存满 500 → 淘汰一半（防 facts>500 时每轮全量重算）"""
        from xiaoli import memory
        memory._EMBED_CACHE.clear()
        for i in range(510):
            memory._EMBED_CACHE[f"k{i}"] = None
        memory._fact_vec("新内容")  # 触发淘汰
        self.assertLessEqual(len(memory._EMBED_CACHE), 260, "应淘汰一半而非清空")
        memory._EMBED_CACHE.clear()
