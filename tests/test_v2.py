# ============================================
# 小李 2.0 大版本 单元测试（2026-08-21）
# 测：E3 情绪转移约束表 / E2 三阶段哄 / E1 分类器兜底 /
#     M1 三信号召回 / M2 半衰期遗忘 / M5 躯体标记 /
#     M3 增量摘要解析 / M4 反思解析（LLM 调用用 mock，不花钱）
# ============================================

import json
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import heart
from xiaoli import memory
from xiaoli import context
from xiaoli import tts_api
from xiaoli import voice
from xiaoli import proactive


class TestE3TransitionBan(unittest.TestCase):
    """情绪转移约束表：气头上/难过不能一步跳开心"""

    def _heart(self, mood):
        h = heart.default_heart()
        h["mood"]["primary"] = mood
        return h

    def test_angry_cannot_jump_happy(self):
        h = self._heart("angry")
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "happy", "intensity_delta": 0}})
        self.assertEqual(h["mood"]["primary"], "angry")

    def test_sad_cannot_jump_happy(self):
        h = self._heart("sad")
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "happy", "intensity_delta": 0}})
        self.assertEqual(h["mood"]["primary"], "sad")

    def test_sad_can_become_content(self):
        h = self._heart("sad")
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "content", "intensity_delta": 0}})
        self.assertEqual(h["mood"]["primary"], "content")

    def test_melancholy_cannot_jump_happy(self):
        h = self._heart("melancholy")
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "happy", "intensity_delta": 0}})
        self.assertEqual(h["mood"]["primary"], "melancholy")

    def test_content_can_become_angry(self):
        # 中性时可以转生气（他放鸽子等场景，LLM 补通道）
        h = self._heart("content")
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "angry", "intensity_delta": 5}})
        self.assertEqual(h["mood"]["primary"], "angry")

    def test_intensity_clamped(self):
        """非程序轮强度增量钳制 ±15：LLM 自报 +99 只能 +15"""
        h = self._heart("content")
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "happy", "intensity_delta": 99}})
        self.assertEqual(h["mood"]["intensity"], 50 + heart.MOOD_INTENSITY_CLAMP)

    def test_program_round_still_absolute(self):
        """程序判定轮（temper_event）仍独占：LLM 自报被拒"""
        h = self._heart("jealous")
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "happy"}}, temper_event=("jealous", "x"))
        self.assertEqual(h["mood"]["primary"], "jealous")


class TestE2Comfort(unittest.TestCase):
    """ESConv 三阶段哄：探索→共情→行动，逐轮推进；转好结束"""

    def _heart(self):
        return heart.default_heart()

    def test_starts_explore(self):
        h = self._heart()
        stage = heart.advance_comfort(h, "sad")
        self.assertEqual(stage, "explore")
        self.assertIn("先温柔问怎么了", heart.comfort_guide(h))

    def test_progresses_explore_to_empathize_to_act(self):
        h = self._heart()
        self.assertEqual(heart.advance_comfort(h, "sad"), "explore")
        self.assertEqual(heart.advance_comfort(h, "anxious"), "empathize")
        self.assertEqual(heart.advance_comfort(h, "frustrated"), "act")
        self.assertEqual(heart.advance_comfort(h, "sad"), "act")  # act 保持

    def test_positive_ends_comfort(self):
        h = self._heart()
        heart.advance_comfort(h, "sad")
        stage = heart.advance_comfort(h, "happy")
        self.assertIsNone(stage)
        self.assertEqual(heart.comfort_guide(h), "")

    def test_neutral_keeps_stage(self):
        h = self._heart()
        heart.advance_comfort(h, "sad")
        stage = heart.advance_comfort(h, "neutral")
        self.assertEqual(stage, "explore")

    def test_guide_covers_all_stages(self):
        for s in ("explore", "empathize", "act"):
            self.assertIn(s, heart.comfort_guide({"comfort_stage": s}))

    def test_no_stage_empty_guide(self):
        self.assertEqual(heart.comfort_guide(heart.default_heart()), "")


class TestE1Classifier(unittest.TestCase):
    """独立情绪分类器：白名单过滤 + 失败兜底 neutral"""

    @patch("xiaoli.llm.get_client")
    def test_returns_label(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.return_value = \
            MagicMock(choices=[MagicMock(message=MagicMock(content=" Sad "))])
        self.assertEqual(context.classify_user_mood("今天好烦"), "sad")

    @patch("xiaoli.llm.get_client")
    def test_unknown_label_becomes_neutral(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.return_value = \
            MagicMock(choices=[MagicMock(message=MagicMock(content="depressed"))])
        self.assertEqual(context.classify_user_mood("test"), "neutral")

    @patch("xiaoli.llm.get_client")
    def test_failure_falls_back_neutral(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.side_effect = Exception("网络炸了")
        self.assertEqual(context.classify_user_mood("test"), "neutral")

    def test_empty_text_neutral(self):
        self.assertEqual(context.classify_user_mood(""), "neutral")


class TestM1ThreeSignalRecall(unittest.TestCase):
    """三信号加权召回：新近度影响排序（刚想起的 > 很久没提的）"""

    def _mk(self, content, importance=5, days_ago=0, valence="neutral"):
        created = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")
        return {"id": content[:2], "content": content, "category": "喜好",
                "importance": importance, "confidence": 0.8,
                "createdAt": created, "lastRecalled": created, "valence": valence}

    def test_recency_breaks_tie(self):
        # 相关度相同、重要度相同 → 刚想起过的排前面（recency 信号）
        facts = [
            self._mk("他喜欢喝奶茶", days_ago=30),
            self._mk("他喜欢喝咖啡", days_ago=0),
        ]
        hits = memory.recall(facts, "他跟我说他喜欢喝奶茶也喜欢喝咖啡")
        self.assertEqual(hits[0]["content"], "他喜欢喝咖啡")

    def test_importance_still_wins(self):
        # 重要度差距大 → 重要度压过新近度
        facts = [
            self._mk("他怕高", importance=9, days_ago=30),
            self._mk("他怕黑", importance=3, days_ago=0),
        ]
        hits = memory.recall(facts, "他说他怕黑也怕高")
        self.assertEqual(hits[0]["content"], "他怕高")

    def test_recall_refreshes_last_recalled(self):
        facts = [self._mk("他喜欢喝奶茶", days_ago=10)]
        memory.recall(facts, "奶茶")
        self.assertLess(
            (datetime.now() - datetime.strptime(facts[0]["lastRecalled"], "%Y-%m-%d %H:%M")).total_seconds(),
            60)  # 召回回血：lastRecalled 刷新到现在

    def test_unrelated_still_empty(self):
        facts = [self._mk("他喜欢喝奶茶")]
        self.assertEqual(memory.recall(facts, "今天工作好累啊"), [])


class TestM2HalfLife(unittest.TestCase):
    """半衰期遗忘：重要度决定忘得多慢；高情绪记忆 ×1.5"""

    def test_half_life_scales_with_importance(self):
        f5 = {"importance": 5}
        f10 = {"importance": 10}
        self.assertEqual(memory.half_life_hours(f10), 2 * memory.half_life_hours(f5))

    def test_high_emotion_half_life_longer(self):
        f = {"importance": 5, "valence": "jealous"}
        self.assertAlmostEqual(memory.half_life_hours(f), memory.half_life_hours({"importance": 5}) * 1.5)

    def test_recall_count_extends_half_life(self):
        """v2 M7 记忆频率：被想起过 → 忘得慢（每次 ×1.15，封顶 ×2）"""
        base = memory.half_life_hours({"importance": 5})
        f1 = memory.half_life_hours({"importance": 5, "recallCount": 1})
        f5 = memory.half_life_hours({"importance": 5, "recallCount": 5})
        f9 = memory.half_life_hours({"importance": 5, "recallCount": 9})
        self.assertAlmostEqual(f1, base * 1.15)
        self.assertAlmostEqual(f5, base * 2.0)  # 1.15^5 ≈ 2.01 → 封顶 2.0
        self.assertEqual(f9, f5)                # 到顶后不再涨

    def test_recall_increments_count(self):
        """召回命中的记忆 recallCount +1（旧数据没有字段 → 从 0 起）"""
        facts = [{"id": "a", "content": "他喜欢猫", "category": "喜好", "importance": 5,
                  "confidence": 0.8, "createdAt": "2026-08-20 10:00",
                  "lastRecalled": "2026-08-20 10:00", "valence": "neutral"}]
        hits = memory.recall(facts, "他喜欢猫")
        self.assertEqual(len(hits), 1)
        self.assertEqual(facts[0]["recallCount"], 1)
        hits = memory.recall(facts, "他喜欢猫")
        self.assertEqual(facts[0]["recallCount"], 2)

    def test_merge_defaults_recall_count_zero(self):
        facts = []
        memory.merge_fact(facts, "他喜欢猫")
        self.assertEqual(facts[0]["recallCount"], 0)

    def test_old_fact_still_kept_within_half_lives(self):
        """30 天没提（重要度5→半衰期35天）：没到 5 半衰期 → 保留"""
        old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
        facts = [{"id": "a", "content": "他喜欢猫", "category": "喜好", "importance": 5,
                  "confidence": 0.8, "createdAt": old, "lastRecalled": old, "valence": "neutral"}]
        kept, removed = memory.decay(facts)
        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, 0)


class TestM5SomaticMarker(unittest.TestCase):
    """躯体标记：valence 存进档案 + 提示块带出感受"""

    def test_merge_stores_valence(self):
        facts = []
        memory.merge_fact(facts, "他提过女同事", importance=6, valence="jealous")
        self.assertEqual(facts[0]["valence"], "jealous")

    def test_default_valence_neutral(self):
        facts = []
        memory.merge_fact(facts, "他喜欢猫")
        self.assertEqual(facts[0]["valence"], "neutral")

    def test_describe_shows_feeling(self):
        s = memory.describe_facts([{"content": "他提过女同事", "category": "关系", "valence": "jealous"}])
        self.assertIn("酸劲", s)


class TestE4EmotionTTS(unittest.TestCase):
    """情绪化 TTS（2026-08-22 修正）：情绪 → 语音参数（语速/音量/音调）。
    废弃指令式——火山实测会把 "<整体情绪:…>" 当正文念出来（时长证据：
    无指令 4.22s vs 带指令 5.96s，该快反而变长 = 在念指令文字）"""

    def test_angry_speaks_fast(self):
        # 生气 → 语速加快（快是自然的，真人生气语速快）
        p = tts_api.emotion_params("angry")
        self.assertGreater(p["speech_rate"], 0)

    def test_jealous_is_soft_not_fighting(self):
        # 吃醋是委屈不是吵架（人设：吃醋酸劲）→ 音量压低
        p = tts_api.emotion_params("jealous")
        self.assertLess(p["loudness"], 0)

    def test_unknown_emotion_silent(self):
        # 未知情绪 → 无参数覆盖（用默认音色）
        self.assertIsNone(tts_api.emotion_params("bored"))

    def test_synthesize_sends_pure_text(self):
        # 送给火山的文本必须是纯台词——指令已从文本层根除（念出教训的回归防线）
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["text"] = json.loads(req.data.decode("utf-8"))["req_params"]["text"]
            raise Exception("stop")  # 不真调

        with unittest.mock.patch("xiaoli.config.VOLC_API_KEY", "fake"):
            with unittest.mock.patch("xiaoli.tts_api.urllib.request.urlopen", fake_urlopen):
                tts_api.synthesize("宝贝，你说", emotion="angry")
                self.assertEqual(captured["text"], "宝贝，你说")
                self.assertNotIn("整体情绪", captured["text"])


class TestSpeakingFlag(unittest.TestCase):
    """口型同步标记（2026-08-22）：speaking_until = 说话截止时间戳。
    合成成功 → 标记；播完/打断/失败 → 归零（网页靠它驱动 Live2D 嘴巴）"""

    def _wait_done(self, timeout=3.0):
        """等后台合成/播放线程收工（2026-08-22 流式朗读后合成也在后台线程：
        不在 with 块内等它跑完，patch 解除后线程会真调火山 → 污染后续测试）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not voice.is_playing() and voice._tts_queue.empty():
                time.sleep(0.05)  # 给收尾（句间停顿/归零）留时间
                return True
            time.sleep(0.02)
        return False

    def test_success_marks_and_clears(self):
        gate = threading.Event()
        # tts_local 也要 mock：真实模型加载 6s > 测试轮询窗口，且本测试只验证火山路径
        with patch("xiaoli.voice.tts_api.synthesize",
                   return_value=b"\x00" * 96000), \
             patch("xiaoli.voice.tts_local.synthesize", return_value=None), \
             patch("xiaoli.voice.sd.play", lambda *a, **k: None), \
             patch("xiaoli.voice.sd.wait", gate.wait):
            voice.play_speech("测试说话")
            # 合成线程异步写入标记——轮询等它出现（最多 2 秒）
            deadline = time.time() + 2
            while time.time() < deadline and voice.speaking_until() == 0:
                time.sleep(0.02)
            self.assertGreater(voice.speaking_until(), time.time())  # 说话中
            gate.set()  # 播完
            # 播放线程里 sd.wait() 返回后才归零——轮询等它（最多 2 秒）
            deadline = time.time() + 2
            while time.time() < deadline and voice.speaking_until() != 0:
                time.sleep(0.02)
            self.assertEqual(voice.speaking_until(), 0)  # 说完归零
            self.assertTrue(self._wait_done())

    def test_failure_clears(self):
        # 火山失败重试后仍失败 + edge 也失败 → 归零（网页不会以为她在说话）
        # tts_local 同样 mock 掉（真实模型加载 6s 超测试窗口，且本测试只验证失败路径）
        with patch("xiaoli.voice.tts_api.synthesize", return_value=None), \
             patch("xiaoli.voice.tts_local.synthesize", return_value=None), \
             patch("xiaoli.voice._edge_pcm", return_value=None), \
             patch("xiaoli.voice.sd.play", lambda *a, **k: None):
            voice.play_speech("测试")
            self.assertEqual(voice.speaking_until(), 0)
            self.assertTrue(self._wait_done())

    def test_silent_return_clears(self):
        # speak=False（语音关）→ 不标记
        with patch("xiaoli.voice.tts_api.synthesize",
                   return_value=b"\x00" * 48000), \
             patch("xiaoli.voice.sd.play", lambda *a, **k: None), \
             patch("xiaoli.voice.sd.wait", lambda: None):
            voice.play_speech("测试", speak=False)
            time.sleep(0.05)
            self.assertEqual(voice.speaking_until(), 0)

    def test_voice_play_speech_passes_emotion_to_volcano(self):
        # play_speech 把 emotion 透传给 tts_api（edge 降级路径收到纯文本）
        captured = {}
        with unittest.mock.patch("xiaoli.voice.speakable", side_effect=lambda t: t):
            with unittest.mock.patch("xiaoli.voice.tts_local.synthesize",
                                     return_value=None), \
                 unittest.mock.patch("xiaoli.voice.tts_api.synthesize") as syn:
                syn.return_value = b"\x00\x00" * 100
                with unittest.mock.patch("xiaoli.voice.sd.play"), \
                     unittest.mock.patch("xiaoli.voice.sd.wait"):
                    voice.play_speech("宝贝", emotion="sad")
                    # 等后台线程在 mock 下收工（否则 patch 解除后真调火山 → 污染后续测试）
                    self.assertTrue(self._wait_done())
        captured["emotion"] = syn.call_args.kwargs.get("emotion")
        self.assertEqual(captured["emotion"], "sad")

    def test_local_success_skips_volcano(self):
        """三层降级链①：本地克隆成功 → 火山不调用（0 元/月优先）"""
        with patch("xiaoli.voice.tts_local.synthesize",
                   return_value=(24000, b"\x00" * 48000)), \
             patch("xiaoli.voice.tts_api.synthesize") as syn, \
             patch("xiaoli.voice.sd.play", lambda *a, **k: None), \
             patch("xiaoli.voice.sd.wait", lambda: None):
            voice.play_speech("宝贝你回来啦")
            self.assertTrue(self._wait_done())
        syn.assert_not_called()  # 本地成功 → 火山没被碰

    def test_local_failure_falls_back_to_volcano(self):
        """三层降级链②：本地失败（模型没就绪）→ 自动降级火山（永远有声音）"""
        with patch("xiaoli.voice.tts_local.synthesize", return_value=None), \
             patch("xiaoli.voice.tts_api.synthesize",
                   return_value=b"\x00" * 48000) as syn, \
             patch("xiaoli.voice.sd.play", lambda *a, **k: None), \
             patch("xiaoli.voice.sd.wait", lambda: None):
            voice.play_speech("宝贝你回来啦")
            self.assertTrue(self._wait_done())
        self.assertTrue(syn.called)  # 火山被叫了


class TestO5Anniversary(unittest.TestCase):
    """纪念日预告：7/3/1/0 天窗口命中；窗口外安静"""

    def _mk(self, content):
        return {"id": "a", "content": content, "category": "关系", "importance": 9,
                "confidence": 0.9, "createdAt": "2026-01-01 10:00",
                "lastRecalled": "2026-01-01 10:00", "valence": "happy"}

    def test_today_hits(self):
        from datetime import datetime
        f = self._mk("我们在一起的纪念日是8月22日")
        hits = memory.find_anniversaries([f], datetime(2026, 8, 22))
        self.assertEqual(hits[0][1], 0)

    def test_seven_days_prior_hits(self):
        from datetime import datetime
        f = self._mk("在一起纪念日 10月1日")
        hits = memory.find_anniversaries([f], datetime(2026, 9, 24))
        self.assertEqual(hits[0][1], 7)

    def test_three_days_prior_hits(self):
        from datetime import datetime
        f = self._mk("纪念日是3月14日")
        hits = memory.find_anniversaries([f], datetime(2026, 3, 11))
        self.assertEqual(hits[0][1], 3)

    def test_far_away_silent(self):
        from datetime import datetime
        f = self._mk("纪念日是3月14日")
        self.assertEqual(memory.find_anniversaries([f], datetime(2026, 6, 1)), [])

    def test_wrap_to_next_year(self):
        """今年已过 → 算明年的（10天后 → 不触发；1天后 → 触发）"""
        from datetime import datetime
        f = self._mk("纪念日 12月31日")
        self.assertEqual(memory.find_anniversaries([f], datetime(2026, 12, 30))[0][1], 1)
        self.assertEqual(memory.find_anniversaries([f], datetime(2026, 12, 20)), [])

    def test_no_date_silent(self):
        from datetime import datetime
        f = self._mk("他喜欢喝奶茶")
        self.assertEqual(memory.find_anniversaries([f], datetime(2026, 8, 22)), [])

    def test_invalid_date_skipped(self):
        from datetime import datetime
        f = self._mk("纪念日 2月30日")
        self.assertEqual(memory.find_anniversaries([f], datetime(2026, 8, 22)), [])


class TestO6WorkingMemory(unittest.TestCase):
    """工作记忆：承诺提取 → 兑现清槽 → 提醒上限 2 次不唠叨"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        proactive.PROMISE_FILE = os.path.join(self._tmp, "promises.json")

    def test_scan_extracts_promise(self):
        self.assertTrue(proactive.promise_scan("待会给你看照片"))
        p = proactive._load_json(proactive.PROMISE_FILE, None)
        self.assertIn("照片", p["keyword"])

    def test_scan_ignores_normal_talk(self):
        self.assertFalse(proactive.promise_scan("今天天气不错"))

    def test_fulfilled_clears(self):
        proactive.promise_scan("回头发你那个文件")
        self.assertIsNone(proactive.promise_hint("文件发你了"))
        self.assertIsNone(proactive._load_json(proactive.PROMISE_FILE, None))

    def test_hint_once_then_clear(self):
        proactive.promise_scan("待会给你看照片")
        h1 = proactive.promise_hint("今天好累")
        self.assertIsNotNone(h1)  # 第一次提醒
        h2 = proactive.promise_hint("在加班")
        self.assertIsNotNone(h2)  # 第二次提醒
        h3 = proactive.promise_hint("好困")
        self.assertIsNone(h3)     # 满 2 次 → 不唠叨，清槽
        self.assertIsNone(proactive._load_json(proactive.PROMISE_FILE, None))

    def test_new_promise_overwrites_old(self):
        proactive.promise_scan("待会给你看照片")
        proactive.promise_scan("回头给你发文件")
        p = proactive._load_json(proactive.PROMISE_FILE, None)
        self.assertIn("文件", p["content"])

    def test_sending_photo_fulfills(self):
        """承诺"给你看照片"→ 他真发图片路径 → 兑现清槽（路径里没有"照片"两字，
        2026-08-23 修复：按关键词判不到 → 加图片路径检测）"""
        proactive.promise_scan("待会给你看照片")
        # 造一张临时图片，路径当用户输入
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8x")
            img = f.name
        try:
            self.assertIsNone(proactive.promise_hint(img))
            self.assertIsNone(proactive._load_json(proactive.PROMISE_FILE, None))
        finally:
            os.remove(img)


class TestO7InterruptResume(unittest.TestCase):
    """打断续说：被打断时没说完的话记进 _unfinished"""

    def test_interrupted_saves_remaining(self):
        from xiaoli import chat
        old_interrupted = chat._interrupted
        chat._unfinished = []
        chat._interrupted = True  # 模拟"被打断"状态
        chat._say_continuations(["还有一句没说完", "再说一句"])
        self.assertEqual(len(chat._unfinished), 2)
        chat._interrupted = old_interrupted
        chat._unfinished = []


class TestO2CallMode(unittest.TestCase):
    """通话模式：识别回调链路 + 开口阈值 + 开关状态机（不碰真麦克风/网络）"""

    def test_recognize_pcm_guards(self):
        """空 PCM / 未配 key → None（不开连接）"""
        from xiaoli import stt
        self.assertIsNone(stt.recognize_pcm(b""))
        self.assertIsNone(stt.recognize_pcm(None))

    def test_finish_recognizes_and_forwards(self):
        """一段话说完了 → 识别 → 回调；不足 0.3s 的爆音 → 丢弃"""
        from xiaoli import stt, call_mode
        m = call_mode.CallMode()
        got = []
        m.on_text = got.append
        old = stt.recognize_pcm
        stt.recognize_pcm = lambda pcm: "宝贝我到家了"
        try:
            m._finish([b"\x00" * 3200], 5)       # 0.5s 有声 → 识别回调
            self.assertEqual(got, ["宝贝我到家了"])
            m._finish([b"\x00" * 3200], 2)       # 0.2s（< MIN_VOICE_BLOCKS）→ 丢弃
            self.assertEqual(len(got), 1)
            m._finish([], 10)                    # 空缓冲 → 什么都不做
            self.assertEqual(len(got), 1)
        finally:
            stt.recognize_pcm = old

    def test_start_stop_state_machine(self):
        """start → active；stop → 不 active；重复 start 幂等"""
        import time
        from xiaoli import call_mode
        m = call_mode.CallMode()
        self.assertFalse(m.active)
        def fake_loop():  # 假监听循环：等到 stop 才退出（不开麦克风）
            while not m._stop.is_set():
                time.sleep(0.01)
        m._loop = fake_loop
        m.start()
        time.sleep(0.05)
        self.assertTrue(m.active)
        m.start()  # 重复 start：不崩、不重复开线程
        m.stop()
        time.sleep(0.05)
        self.assertFalse(m.active)

    def test_voice_gate_flag(self):
        """半双工门控：is_playing 初始关闭；静默播放路径后仍关闭"""
        from xiaoli import voice
        self.assertFalse(voice.is_playing())
        voice.play_speech("测试", speak=False)  # 静默路径：不置位
        self.assertFalse(voice.is_playing())


class TestM3SummarizeParse(unittest.TestCase):
    """增量摘要两段解析：正常 + 标记缺失兜底"""

    @patch("xiaoli.llm.get_client")
    def test_two_sections_parsed(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.return_value = \
            MagicMock(choices=[MagicMock(message=MagicMock(content="【记忆】他喜欢奶茶。\n【情绪线】（8/20 吵架和好）"))])
        summary, emotion = context._summarize([], "")
        self.assertIn("他喜欢奶茶", summary)
        self.assertIn("吵架和好", emotion)

    @patch("xiaoli.llm.get_client")
    def test_missing_marker_whole_as_summary(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.return_value = \
            MagicMock(choices=[MagicMock(message=MagicMock(content="他只输出了记忆没给标记"))])
        summary, emotion = context._summarize([], "")
        self.assertIn("只输出了记忆", summary)
        self.assertEqual(emotion, "")


class TestM4ReflectParse(unittest.TestCase):
    """睡前反思：格式解析 + 白名单过滤 + 失败兜底"""

    @patch("xiaoli.llm.get_client")
    def test_parses_lines(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.return_value = \
            MagicMock(choices=[MagicMock(message=MagicMock(content="他加班多，晚上要多心疼他|sad\n- 他怕黑|neutral"))])
        diary = {"messages": [{"role": "user", "content": "好累", "time": datetime.now().strftime("%Y-%m-%d %H:%M")}
                              for _ in range(8)]}
        r = context.reflect(diary)
        self.assertGreaterEqual(len(r), 1)
        self.assertEqual(r[0][1], "sad")

    @patch("xiaoli.llm.get_client")
    def test_bad_valence_skipped(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.return_value = \
            MagicMock(choices=[MagicMock(message=MagicMock(content="他喜欢猫|angry|extra"))])
        diary = {"messages": [{"role": "user", "content": "hi", "time": datetime.now().strftime("%Y-%m-%d %H:%M")}
                              for _ in range(8)]}
        r = context.reflect(diary)
        self.assertEqual(r, [])

    def test_too_few_messages_skips(self):
        diary = {"messages": [{"role": "user", "content": "hi", "time": "2026-08-21 10:00"}]}
        self.assertEqual(context.reflect(diary), [])

    @patch("xiaoli.llm.get_client")
    def test_failure_returns_empty(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.side_effect = Exception("API挂了")
        diary = {"messages": [{"role": "user", "content": "hi", "time": datetime.now().strftime("%Y-%m-%d %H:%M")}
                              for _ in range(8)]}
        self.assertEqual(context.reflect(diary), [])


class TestM6DailyWorkbench(unittest.TestCase):
    """每日一句话注入工作台 + emotion_line"""

    def test_daily_injected(self):
        diary = {"summary": "", "daily": {"2026-08-20": "聊了他加班和想喝奶茶的事"},
                 "emotion_line": "", "messages": []}
        msgs = context.build_workbench("P", diary, "hi", now=datetime(2026, 8, 21, 12, 0))
        joined = " ".join(m["content"] for m in msgs)
        self.assertIn("近日回顾", joined)
        self.assertIn("08-20", joined)  # 注入格式：MM-DD：一句话

    def test_emotion_line_injected(self):
        diary = {"summary": "", "daily": {}, "emotion_line": "（8/20 吵架和好）", "messages": []}
        msgs = context.build_workbench("P", diary, "hi")
        joined = " ".join(m["content"] for m in msgs)
        self.assertIn("情绪线", joined)

    def test_old_diary_compat(self):
        """v1 旧日记本（无 daily/emotion_line）不炸"""
        diary = {"summary": "", "messages": []}
        msgs = context.build_workbench("P", diary, "hi")
        self.assertEqual(msgs[-1]["content"], "hi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
