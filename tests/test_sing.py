# ============================================
# 唱歌演出链回归测试（2026-08-23，Z 节 A 方案=纯放歌+演出链）
# sing：检测"他叫唱歌"→清嗓→报歌名→播歌→"我们的歌"记忆
# 设计纪律：触发归程序管；曲库空不演（不装不念白）；失败静默不影响主流程
# ============================================

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xiaoli import sing


def _speak_immediate(text, on_done=None, **kw):
    """模拟 voice.play_speech：立即触发 on_done（真实实现是播完才触发）——
    否则 _announce 的 _speak_wait 会真等 30 秒超时"""
    if on_done:
        on_done()


class TestTrigger(unittest.TestCase):
    """maybe_sing 的触发判定（触发词/排除词）"""

    def test_triggers(self):
        """他叫"她"唱歌 → 触发"""
        for t in ["唱首歌给我听", "我想听你唱歌", "来一首", "唱给我听",
                  "你唱个歌嘛", "唱一支歌", "来一段嘛"]:
            self.assertIsNotNone(sing.TRIGGER_RE.search(t), f"应触发: {t}")

    def test_excludes(self):
        """他自己唱 → 排除"""
        for t in ["我想唱", "我要去唱K", "咱们一起唱", "我自己唱", "我来唱"]:
            self.assertIsNotNone(sing.EXCLUDE_RE.search(t), f"应排除: {t}")

    @mock.patch("xiaoli.sing._song_list", return_value=[])
    def test_no_songs_no_sing(self, m_list):
        """曲库空 → 不演（persona 自然说没练好，不装）"""
        self.assertIsNone(sing.maybe_sing("唱首歌给我听"))

    @mock.patch("xiaoli.sing._song_list", return_value=[])
    def test_plain_text_no_sing(self, m_list):
        """普通句子 → 不触发"""
        self.assertIsNone(sing.maybe_sing("今天天气不错"))

    @mock.patch("xiaoli.sing._song_list", return_value=[])
    def test_self_sing_no_sing(self, m_list):
        """"我想唱" → 不触发（不是叫她唱）"""
        self.assertIsNone(sing.maybe_sing("我想唱一首歌"))


class TestSingChain(unittest.TestCase):
    """演出链：清嗓→报歌名→播歌→记忆"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = sing.SONGS_DIR
        sing.SONGS_DIR = self._tmp
        with open(os.path.join(self._tmp, "小幸运.wav"), "wb") as f:
            f.write(b"fake")
        with open(os.path.join(self._tmp, "晴天.mp3"), "wb") as f:
            f.write(b"fake")

    def tearDown(self):
        sing.SONGS_DIR = self._orig

    @mock.patch("xiaoli.sing._play_song", return_value=True)
    @mock.patch("xiaoli.sing._announce", return_value=("小幸运", os.path.join("x", "小幸运.wav")))
    @mock.patch("xiaoli.sing.sfx.play_blocking")
    @mock.patch("xiaoli.sing.memory_mod")
    def test_chain_order_and_memory(self, m_mem, m_sfx, m_ann, m_play):
        """顺序：清嗓→报歌名→播歌；记忆写"我们的歌"（importance 8）"""
        out = sing.maybe_sing("唱首歌给我听")
        self.assertEqual(out, "小幸运")
        m_sfx.assert_called_once_with("clear_throat")
        m_ann.assert_called_once()
        m_play.assert_called_once()
        m_mem.load_facts.assert_called_once()
        m_mem.merge_fact.assert_called_once()
        args, kwargs = m_mem.merge_fact.call_args
        self.assertEqual(kwargs.get("importance"), 8)
        self.assertIn("我们俩的歌", args[1])
        m_mem.save_facts.assert_called_once()

    @mock.patch("xiaoli.sing._play_song", return_value=True)
    @mock.patch("xiaoli.sing._announce", side_effect=Exception("LLM挂了"))
    @mock.patch("xiaoli.sing.sfx.play_blocking")
    @mock.patch("xiaoli.sing.memory_mod")
    def test_announce_failure_still_sings(self, m_mem, m_sfx, m_ann, m_play):
        """报歌名 LLM 失败 → 演出不中断（模板兜底）"""
        out = sing.maybe_sing("来一首")
        self.assertIsNotNone(out)
        m_play.assert_called_once()

    @mock.patch("xiaoli.sing._play_song", side_effect=Exception("播放器炸了"))
    @mock.patch("xiaoli.sing._announce", return_value=("小幸运", "x"))
    @mock.patch("xiaoli.sing.sfx.play_blocking")
    def test_play_failure_no_crash(self, m_sfx, m_ann, m_play):
        """播歌失败 → 返回 None 不炸（主流程照常）"""
        self.assertIsNone(sing.maybe_sing("唱首歌"))


class TestAnnounce(unittest.TestCase):
    """_announce：LLM 选歌 + 模糊匹配 + 失败兜底"""

    def test_llm_pick_and_match(self):
        """LLM 报《晴天》→ 匹配到 晴天.mp3；台词用她声音播"""
        songs = [("小幸运", "a.wav"), ("晴天", "b.mp3")]
        r = mock.Mock()
        r.choices = [mock.Mock(message=mock.Mock(content="齁～那我唱《晴天》给你听齁～"))]
        with mock.patch("xiaoli.llm.get_client") as MockOpenAI, \
             mock.patch("xiaoli.sing.voice.play_speech",
                        side_effect=_speak_immediate) as m_speak:
            MockOpenAI.return_value.chat.completions.create.return_value = r
            title, path = sing._announce(songs)
        self.assertEqual(title, "晴天")
        self.assertEqual(path, "b.mp3")
        m_speak.assert_called_once()
        self.assertIn("晴天", m_speak.call_args[0][0])

    def test_llm_failure_fallback(self):
        """LLM 失败 → 模板+第一首，不炸"""
        songs = [("小幸运", "a.wav"), ("晴天", "b.mp3")]
        with mock.patch("xiaoli.llm.get_client",
                        side_effect=Exception("挂了")), \
             mock.patch("xiaoli.sing.voice.play_speech",
                        side_effect=_speak_immediate):
            title, path = sing._announce(songs)
        self.assertEqual(title, "小幸运")
        self.assertEqual(path, "a.wav")

    def test_announce_waits_for_speech_done(self):
        """竞态修复契约：报歌名必须等播完（传 on_done）才开唱——
        否则 sd.play 是替换语义，歌名被歌顶掉/歌被歌名顶掉开头"""
        songs = [("小幸运", "a.wav")]
        r = mock.Mock()
        r.choices = [mock.Mock(message=mock.Mock(content="齁～那我唱《小幸运》给你听齁～"))]
        got_on_done = {}

        def _capture(text, on_done=None, **kw):
            got_on_done["on_done"] = on_done
            on_done()  # 立即播完

        with mock.patch("xiaoli.llm.get_client") as MockOpenAI, \
             mock.patch("xiaoli.sing.voice.play_speech", side_effect=_capture):
            MockOpenAI.return_value.chat.completions.create.return_value = r
            sing._announce(songs)
        self.assertIsNotNone(got_on_done.get("on_done"), "报歌名必须挂 on_done 等待播完")

    def test_speak_wait_timeout_no_hang(self):
        """on_done 永不触发（播放系统炸了）→ 超时返回不卡死"""
        with mock.patch("xiaoli.sing.ANNOUNCE_WAIT_TIMEOUT", 0.05), \
             mock.patch("xiaoli.sing.voice.play_speech") as m_speak:  # 不触发 on_done
            sing._speak_wait("齁～那我唱《晴天》给你听齁～")
        m_speak.assert_called_once()

    def test_llm_bad_name_fallback_first(self):
        """LLM 报的名字匹配不到歌 → 用第一首"""
        songs = [("小幸运", "a.wav")]
        r = mock.Mock()
        r.choices = [mock.Mock(message=mock.Mock(content="齁～那我唱《不存在》齁"))]
        with mock.patch("xiaoli.llm.get_client") as MockOpenAI, \
             mock.patch("xiaoli.sing.voice.play_speech",
                        side_effect=_speak_immediate):
            MockOpenAI.return_value.chat.completions.create.return_value = r
            title, path = sing._announce(songs)
        self.assertEqual(title, "小幸运")


class TestPlaySong(unittest.TestCase):
    """_play_song：miniaudio 解码 + sd 播放"""

    def test_play_ok(self):
        import numpy as np
        dec = mock.Mock()
        dec.samples = np.zeros(8000, dtype=np.float32).tobytes()
        dec.nchannels = 1
        dec.sample_rate = 16000
        with mock.patch("xiaoli.sing.miniaudio.decode_file", return_value=dec), \
             mock.patch("xiaoli.sing.sd.play"), \
             mock.patch("xiaoli.sing.sd.wait"):
            self.assertTrue(sing._play_song("x.wav"))

    def test_play_fail(self):
        with mock.patch("xiaoli.sing.miniaudio.decode_file",
                        side_effect=Exception("坏文件")):
            self.assertFalse(sing._play_song("x.wav"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
