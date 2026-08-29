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

    def test_comma_not_split_short(self):
        """短句（≤16 字）逗号不切：保持自然语流"""
        r = split_sentences("宝贝，想你了？")
        assert len(r) == 1

    def test_comma_splits_long_sentence(self):
        """长句（>16 字）按逗号切（2026-08-23 实测本地合成仅 0.53x 实时：
        长句不切成短段，句间空等 4s+ 像卡住；切短后空等 1.4~2.3s=她想了一下）"""
        r = split_sentences("宝贝，人家好想你喔，今天有没有想我？")
        assert len(r) == 2  # "宝贝，人家好想你喔，" + "今天有没有想我？"
        assert r[0].endswith("，")

    def test_empty_and_whitespace(self):
        assert split_sentences("") == []
        assert split_sentences("   ") == []

    def test_long_sentence_hard_split(self):
        """超长无标点句硬切（>16 字）：不切的话第一声要等整句合成完。
        48→24→16（2026-08-23 实测本地合成只有 0.53x 实时：短段流水线
        首声=第一段（快）、句间空等 1.4~2.3s（"想了想"），24 字段空等 4s+）"""
        long = "今天学了一首超好听的歌" * 8  # 88 字
        r = split_sentences(long)
        assert len(r) == 6
        assert all(len(s) <= 16 for s in r)
        assert "".join(r) == long  # 硬切不丢字

    def test_quote_inside(self):
        """引号内的话跟着句号一起切"""
        r = split_sentences("他在信里写：我爱你。就这样。")
        assert r == ["他在信里写：我爱你。", "就这样。"]

    def test_ellipsis_not_split(self):
        """2026-08-23 用户实测"省略号截止得很突然"：
        省略号是话没说完的延续，切在它后面语流断裂 → 必须整句进 TTS"""
        assert split_sentences("我跟你說喔……今天超開心的") == ["我跟你說喔……今天超開心的"]
        assert split_sentences("……嗯……好吧") == ["……嗯……好吧"]

    def test_ellipsis_then_terminator_splits(self):
        """省略号后面真句号 → 在句号处切（省略号留在前句尾）"""
        r = split_sentences("我跟你说喔……今天超开心。明天见！")
        assert r == ["我跟你说喔……今天超开心。", "明天见！"]

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
        assert voice._protect_pauses("齁～好想你<pause/>") == "齁～好想你PH"

    def test_protect_with_sec(self):
        assert voice._protect_pauses("齁～<pause:1.5>你听我说") == "齁～PH1500你听我说"

    def test_protect_case_insensitive(self):
        assert voice._protect_pauses("好<Pause/>啦") == "好PH啦"

    def test_parse_default_08(self):
        clean, pause = voice._parse_pauses("齁～好想你PH")
        assert clean == "齁～好想你" and pause == 0.8

    def test_parse_with_sec(self):
        clean, pause = voice._parse_pauses("齁～PH1500你听我说")
        assert clean == "齁～你听我说" and pause == 1.5

    def test_placeholder_short_immune_to_hard_split(self):
        """2026-08-23 修 bug：原 13 字符占位符 PAUSEHOLD1500 会被 16 字硬切
        从中切开（停顿错位+碎片句）。单字符 PH 占位符 + 16 字硬切 → 占位符完整"""
        p = voice._protect_pauses("我跟你說喔，齁～<pause:1.5>你聽我說，這個真的很重要。")
        segs = voice.split_sentences(p)
        assert len(segs) == 3
        # 中段停顿所在句：占位符完整、clean 正确、停顿 1.5s
        mid = [voice._parse_pauses(s) for s in segs]
        assert ("齁～你聽我說，", 1.5) in mid

    def test_parse_clamp(self):
        """秒数钳制 0.3~2.5（太短无感、太长像卡死）"""
        _, p1 = voice._parse_pauses("PH100")   # 0.1 秒 → 钳到下限 0.3
        _, p2 = voice._parse_pauses("PH3000")  # 3 秒 → 钳到上限 2.5
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
                                None, voice._gen)
        items = []
        while True:
            it = voice._tts_queue.get()
            if it[1] is None:  # 6A：队列项带代际 (gen, item)
                break
            items.append(it[1])
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
        """句中标签：剥掉标签合成，句后插静音（长句被逗号切成 3 段，
        停顿所在段播放后插 1.5s 静音）"""
        fake = b"\x00\x00" * 4800
        items = self._run_synth("我跟你說喔，齁～<pause:1.5>你聽我說，這個真的很重要。", fake)
        # 段1(说) + 段2(说) + 段2后静音 + 段3(说) = 4 项
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0][1], fake)
        self.assertEqual(items[1][1], fake)
        self.assertEqual(len(items[2][1]), int(24000 * 1.5) * 2)  # 1.5s 静音
        self.assertEqual(items[3][1], fake)

    def test_no_pause_no_silence(self):
        fake = b"\x00\x00" * 4800
        items = self._run_synth("就是普通一句。", fake)
        self.assertEqual(len(items), 1)  # 无静音段

    def test_items_have_kind_and_gap(self):
        """2026-08-23 (sr, pcm, kind, gap) 四元组：句子带标点感知停顿、静音段 kind=silence"""
        fake = b"\x00\x00" * 4800
        items = self._run_synth("好想你。宝贝，", fake)  # 逗号尾 = 弱标点档
        # 切句 → ["好想你。", "宝贝，"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0][2], "speech")
        self.assertEqual(items[0][3], voice._GAP_TERMINATOR)  # 句号尾 0.30s
        self.assertEqual(items[1][2], "speech")
        self.assertEqual(items[1][3], voice._GAP_HARD_CUT)    # 弱标点尾 0.15s（别打断语流）
        self.assertEqual(len(items[1][1]), 9600)  # 音频数据还在原位置，兼容旧索引（4800×2字节）

    def test_items_gap_no_punct_after_speakable(self):
        """无标点尾句（speakable 后仍无标点）→ 0.50s 档（机关枪听感重灾区）"""
        fake = b"\x00\x00" * 4800
        items = self._run_synth("好想你。宝贝", fake)
        self.assertEqual(items[1][3], voice._GAP_NO_PUNCT)


class TestGapFor:
    """_gap_for 标点感知句间停顿查表"""

    def test_terminator_tails(self):
        for tail in ["。", "！", "？", ";"]:
            assert voice._gap_for(f"我好想你{tail}") == voice._GAP_TERMINATOR

    def test_weak_break_tails(self):
        assert voice._gap_for("今天吃了苹果，") == voice._GAP_HARD_CUT

    def test_no_punct_tail(self):
        assert voice._gap_for("宝贝") == voice._GAP_NO_PUNCT

    def test_whitespace_only(self):
        """空串/纯空白：无引擎可依 → 播放层兜底 0.5s（空 tail 不能落进终结符分支）"""
        assert voice._gap_for("   ") == voice._GAP_NO_PUNCT
        assert voice._gap_for("") == voice._GAP_NO_PUNCT


class TestInterruptedTail:
    """A-P1-3 打断续说（2026-08-23）：播放线程跟踪"正播到哪"，打断时按
    已播时长比例估算残余文本——她被打断后能自然续上没说完的话"""

    def setUp(self):
        with voice._cur_lock:
            voice._cur_text = ""
            voice._cur_dur = 0.0
            voice._cur_t0 = 0.0

    def _set_playing(self, text, dur_s, played_s):
        import time as _t
        with voice._cur_lock:
            voice._cur_text = text
            voice._cur_dur = dur_s
            voice._cur_t0 = _t.time() - played_s  # 已播 played_s 秒

    def test_ratio_estimate(self):
        """播到 40% → 残余 = 后 60% 文本"""
        self._set_playing("今天天气真不错我们一起去公园吧", 5.0, 2.0)
        tail = voice.interrupted_tail()
        assert "一起去公园吧" in tail and "我们" in tail

    def test_full_text_when_just_started(self):
        """刚开口（played≈0）→ 残余≈整句，也返回（她确实没说出什么）"""
        self._set_playing("今天天气真不错", 5.0, 0.05)
        tail = voice.interrupted_tail()
        assert "今天天气" in tail

    def test_empty_when_almost_done(self):
        """快说完了（残余 < 4 字）→ ""（再带进去是噪音）"""
        self._set_playing("今天天气真好呀", 5.0, 4.9)
        assert voice.interrupted_tail() == ""

    def test_empty_when_not_playing(self):
        """没在播（静音段/收工清空）→ "" """
        assert voice.interrupted_tail() == ""

    def test_queue_item_carries_text(self):
        """5 元组：合成线程 put 的末位带这句的文本（播放线程跟踪用）"""
        fake = b"\x00\x00" * 4800
        voice._tts_queue = queue.Queue()
        with mock.patch("xiaoli.voice.tts_local.synthesize", return_value=None), \
             mock.patch("xiaoli.voice.tts_api.synthesize", return_value=fake), \
             mock.patch("xiaoli.voice._edge_pcm", return_value=None):
            voice._synth_worker(voice.speakable(voice._protect_pauses("好想你。")),
                                None, voice._gen)
        it = voice._tts_queue.get()[1]  # 5 元组 (sr, pcm, kind, gap, text)（队列项带代际）
        assert len(it) == 5
        assert it[4] == "好想你。"
        voice._tts_queue.get()  # 清哨兵
        voice._tts_queue = queue.Queue()
