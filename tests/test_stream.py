# ============================================
# 流式朗读切句器单测（2026-08-22）
# split_sentences：句号级终结符切分 + 碎片过滤 + 超长硬切（移植 dsh sentences.ts）
# 不碰真实合成/播放（那部分靠手工验收听效果）
# ============================================

import pytest

from xiaoli.voice import split_sentences


class TestSplitSentences:
    def test_multiple_sentences(self):
        """多句切分：句号/问号/感叹号各成一句"""
        r = split_sentences("我好想你。我也想你！今天过得好吗？")
        assert r == ["我好想你。", "我也想你！", "今天过得好吗？"]

    def test_no_trailing_punct(self):
        """尾部无标点：整段都要播（小李整段拿到文本，没有"生成中 partial"）"""
        assert split_sentences("我好想你") == ["我好想你"]

    def test_single_char_kept(self):
        """单字回应是完整句，要播"""
        r = split_sentences("嗯！")
        assert r == ["嗯！"]

    def test_trivial_filtered(self):
        """纯标点碎片（！！！/…）不播——绝不送 TTS"""
        assert split_sentences("！！！") == []
        assert split_sentences("……") == []
        assert split_sentences("我好想你！！！！") == ["我好想你！"]

    def test_comma_not_split(self):
        """逗号不切：保持自然语流（切了会碎成机关枪）"""
        r = split_sentences("宝贝，人家好想你喔，今天有没有想我？")
        assert len(r) == 1
        assert r[0].startswith("宝贝")

    def test_empty_and_whitespace(self):
        assert split_sentences("") == []
        assert split_sentences("   ") == []

    def test_long_sentence_hard_split(self):
        """超长无标点句硬切（>48 字）：不切的话第一声要等整句合成完"""
        long = "今天学了一首超好听的歌" * 8  # 64 字
        r = split_sentences(long)
        assert len(r) == 2
        assert all(len(s) <= 48 for s in r)
        assert "".join(r) == long  # 硬切不丢字

    def test_quote_inside(self):
        """引号内的话跟着句号一起切"""
        r = split_sentences("他在信里写：我爱你。就这样。")
        assert r == ["他在信里写：我爱你。", "就这样。"]

    def test_enumeration_no_split(self):
        """顿号列举不断句"""
        r = split_sentences("我今天吃了苹果、香蕉、葡萄。")
        assert len(r) == 1
