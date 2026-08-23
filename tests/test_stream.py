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


# ============================================
# <pause/> 停顿标签（2026-08-23，学自 OLLVT [pause]）：
#   LLM 句尾写 <pause/>（或 <pause:1.5>）→ 程序转静音段入队播放
# ============================================
import queue
import unittest
from unittest import mock

from xiaoli import voice


class TestPauseTags:
    """_protect_pauses / _parse_pauses / _silence_pcm（纯函数）"""

    def test_protect_default(self):
        assert voice._protect_pauses("齁～好想你<pause/>") == "齁～好想你PAUSEHOLD"

    def test_protect_with_sec(self):
        assert voice._protect_pauses("齁～<pause:1.5>你听我说") == "齁～PAUSEHOLD1500你听我说"

    def test_protect_case_insensitive(self):
        assert voice._protect_pauses("好<Pause/>啦") == "好PAUSEHOLD啦"

    def test_parse_default_08(self):
        clean, pause = voice._parse_pauses("齁～好想你PAUSEHOLD")
        assert clean == "齁～好想你" and pause == 0.8

    def test_parse_with_sec(self):
        clean, pause = voice._parse_pauses("齁～PAUSEHOLD1500你听我说")
        assert clean == "齁～你听我说" and pause == 1.5

    def test_parse_clamp(self):
        """秒数钳制 0.3~2.5（太短无感、太长像卡死）"""
        _, p1 = voice._parse_pauses("PAUSEHOLD100")   # 0.1 秒 → 钳到下限 0.3
        _, p2 = voice._parse_pauses("PAUSEHOLD3000")  # 3 秒 → 钳到上限 2.5
        assert p1 == 0.3 and p2 == 2.5

    def test_parse_no_tag(self):
        clean, pause = voice._parse_pauses("就是普通一句")
        assert clean == "就是普通一句" and pause == 0.0

    def test_silence_pcm_length(self):
        pcm = voice._silence_pcm(1.0)
        assert len(pcm) == 24000 * 2  # 1 秒 24k int16


class TestPausePipeline(unittest.TestCase):
    """管线级：含 <pause/> 的句子 → 句子音频后插入静音段"""

    def setUp(self):
        # 保存全局状态，tearDown 恢复——防污染后续测试（test_v2 的说话标志测试）
        self._orig_queue = voice._tts_queue
        self._orig_speaking = voice._speaking_until

    def tearDown(self):
        voice._tts_queue = self._orig_queue
        voice._speaking_until = self._orig_speaking

    def _run_synth(self, text, fake_pcm):
        """mock 掉三层合成，跑 _synth_worker，收集队列项"""
        voice._tts_queue = queue.Queue()
        with mock.patch("xiaoli.voice.tts_local.synthesize", return_value=None), \
             mock.patch("xiaoli.voice.tts_api.synthesize", return_value=fake_pcm), \
             mock.patch("xiaoli.voice._edge_pcm", return_value=None):
            voice._synth_worker(voice.speakable(voice._protect_pauses(text)),
                                None, voice._gen, None)
        items = []
        while True:
            it = voice._tts_queue.get()
            if it is None:
                break
            items.append(it)
        return items

    def test_sentence_then_silence(self):
        """句尾 <pause/>：切句器切成"纯停顿尾句"→ 只停不播"""
        fake = b"\x00\x00" * 4800  # 0.1s 24k
        items = self._run_synth("齁～好想你。<pause/>", fake)
        # "齁～好想你。<pause/>" 切句 → ["齁～好想你。", "PAUSEHOLD"]
        # 句1 无标签→正常合成；句2 纯停顿→只入队静音段 → 共 2 项
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0][1], fake)  # 句1 音频
        self.assertEqual(len(items[1][1]), int(24000 * 0.8) * 2)  # 0.8s 静音

    def test_mid_sentence_pause(self):
        """句中标签：剥掉标签合成，句后插静音"""
        fake = b"\x00\x00" * 4800
        items = self._run_synth("齁～<pause:1.5>你听我说。", fake)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0][1], fake)  # 合成的是剥掉标签的干净句
        self.assertEqual(len(items[1][1]), int(24000 * 1.5) * 2)  # 1.5s 静音

    def test_no_pause_no_silence(self):
        fake = b"\x00\x00" * 4800
        items = self._run_synth("就是普通一句。", fake)
        self.assertEqual(len(items), 1)  # 无静音段
