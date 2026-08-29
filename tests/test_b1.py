# ============================================
# B.1 情绪层"心" 回归测试
# 测：时间衰减 / 启发式基线 / LLM建议钳制 / 状态描述
# ============================================

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import heart

TMP = tempfile.mkdtemp()


def setUpModule():
    heart.HEART_FILE = os.path.join(TMP, "heart.json")


def _mkheart(**kw):
    h = heart.default_heart()
    h.update(kw)
    # 未显式给 decay_applied 时，与 last_interaction 同刻（代表"还没为这段离开衰减过"）
    if "decay_applied" not in kw:
        h["decay_applied"] = h["last_interaction"]
    return h


class TestTimeDecay(unittest.TestCase):
    def test_48h_affection_decay(self):
        """48小时没互动 → 好感每天-1"""
        now = datetime(2026, 8, 20, 12, 0)
        last = now - timedelta(days=5)
        h = _mkheart(affection=60, last_interaction=last.strftime("%Y-%m-%d %H:%M"))
        heart.apply_time_decay(h, now)
        self.assertEqual(h["affection"], 55)  # 60-5（5天×每天1）

    def test_decay_cap10(self):
        """单次衰减上限 -10"""
        now = datetime(2026, 8, 20, 12, 0)
        last = now - timedelta(days=15)
        h = _mkheart(affection=60, last_interaction=last.strftime("%Y-%m-%d %H:%M"))
        heart.apply_time_decay(h, now)
        self.assertEqual(h["affection"], 50)  # 60-10（封顶）

    def test_72h_melancholy(self):
        """72小时没互动 → 心情变忧郁，强度每天+5上限30"""
        now = datetime(2026, 8, 20, 12, 0)
        last = now - timedelta(days=10)
        h = _mkheart(last_interaction=last.strftime("%Y-%m-%d %H:%M"))
        heart.apply_time_decay(h, now)
        self.assertEqual(h["mood"]["primary"], "melancholy")
        self.assertEqual(h["mood"]["intensity"], 30)  # 封顶

    def test_24h_jealous(self):
        """一整天（24小时+）没理她 → 重逢时酸溜溜吃醋（用户确认触发点）"""
        now = datetime(2026, 8, 20, 12, 0)
        last = now - timedelta(hours=26)
        h = _mkheart(last_interaction=last.strftime("%Y-%m-%d %H:%M"))
        heart.apply_time_decay(h, now)
        self.assertEqual(h["mood"]["primary"], "jealous")
        self.assertTrue(any("没理我" in c for c in h["mood"]["causes"]))

    def test_under_24h_no_jealous(self):
        """不到一整天（23小时）→ 不触发吃醋"""
        now = datetime(2026, 8, 20, 12, 0)
        last = now - timedelta(hours=23)
        h = _mkheart(last_interaction=last.strftime("%Y-%m-%d %H:%M"))
        heart.apply_time_decay(h, now)
        self.assertNotEqual(h["mood"]["primary"], "jealous")

    def test_3d_melancholy_overrides_jealous(self):
        """72小时+ → melancholy 忧郁覆盖吃醋（失落比吃醋更重）"""
        now = datetime(2026, 8, 20, 12, 0)
        last = now - timedelta(days=3, hours=1)
        h = _mkheart(last_interaction=last.strftime("%Y-%m-%d %H:%M"))
        heart.apply_time_decay(h, now)
        self.assertEqual(h["mood"]["primary"], "melancholy")

    def test_fresh_no_decay(self):
        now = datetime(2026, 8, 20, 12, 0)
        h = _mkheart(last_interaction=now.strftime("%Y-%m-%d %H:%M"))
        heart.apply_time_decay(h, now)
        self.assertEqual(h["affection"], 60)  # 没离开过 → 不扣

    def test_decay_once_only(self):
        """每次离开只衰减一次（同一离开期二次调用不重复扣）"""
        now = datetime(2026, 8, 20, 12, 0)
        last = now - timedelta(days=3)
        h = _mkheart(affection=60, last_interaction=last.strftime("%Y-%m-%d %H:%M"))
        heart.apply_time_decay(h, now)
        heart.apply_time_decay(h, now)
        self.assertEqual(h["affection"], 57)  # 只扣3天

    def test_new_absence_decays_again(self):
        """聊完天再离开 → 新离开期正常再次衰减"""
        now = datetime(2026, 8, 20, 12, 0)
        last = now - timedelta(days=3)
        h = _mkheart(affection=57, last_interaction=last.strftime("%Y-%m-%d %H:%M"))
        heart.apply_time_decay(h, now)          # 第一次离开：57→54
        self.assertEqual(h["affection"], 54)
        h["last_interaction"] = now.strftime("%Y-%m-%d %H:%M")  # 聊完天
        later = now + timedelta(days=2)         # 又离开2天
        heart.apply_time_decay(h, later)
        self.assertEqual(h["affection"], 52)    # 54-2


class TestAnalyze(unittest.TestCase):
    def test_positive(self):
        self.assertGreater(heart.analyze_message("我爱你"), 0)

    def test_negative(self):
        self.assertLess(heart.analyze_message("我好烦啊"), 0)

    def test_question(self):
        self.assertGreater(heart.analyze_message("你吃饭了吗"), 0)

    def test_deep(self):
        self.assertGreater(heart.analyze_message("聊聊人生吧"), 0)

    def test_neutral(self):
        self.assertEqual(heart.analyze_message("嗯"), 0)


class TestMergeClamp(unittest.TestCase):
    def test_whitelist_rejected(self):
        """情绪白名单外的畸形建议被拒绝"""
        h = _mkheart()
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "rage", "intensity_delta": 50, "cause": "x"}})
        self.assertEqual(h["mood"]["primary"], "content")  # 未变（默认是 content）

    def test_affection_clamped(self):
        h = _mkheart(affection=60)
        heart.merge_llm_suggestion(h, {"affection_delta": 99})
        self.assertEqual(h["affection"], 65)  # ±5封顶
        heart.merge_llm_suggestion(h, {"affection_delta": -99})
        self.assertEqual(h["affection"], 60)

    def test_intensity_clamped_0_100(self):
        h = _mkheart()
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "happy", "intensity_delta": 999, "cause": "x"}})
        self.assertLessEqual(h["mood"]["intensity"], 100)

    def test_cause_recorded(self):
        h = _mkheart()
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "sad", "intensity_delta": 10, "cause": "他生气了"}})
        self.assertIn("他生气了", h["mood"]["causes"])

    def test_merge_affection_only(self):
        h = _mkheart(affection=60)
        heart.merge_llm_suggestion(h, {"affection_delta": 3})
        self.assertEqual(h["affection"], 63)


class TestDescribe(unittest.TestCase):
    def test_describe_contains_state(self):
        h = _mkheart()
        s = heart.describe(h)
        self.assertIn("心情", s)
        self.assertIn("好感", s)

    def test_describe_not_light_anger(self):
        """教训修复：旧版写死"生气就淡淡的"=教她生气要淡定。新版本要教她嘴硬/酸溜溜。"""
        s = heart.describe(_mkheart())
        self.assertNotIn("生气就淡淡的", s)
        self.assertIn("酸溜溜", s)
        self.assertIn("嘴硬", s)


class TestTemper(unittest.TestCase):
    """小脾气触发检测（B.1 扩展，2026-08-20）：
    程序检测吃醋/哄消气（App 是游戏主持人），LLM 只在既定情绪下发挥"""

    def test_strong_trigger_jealous(self):
        """提"别的女生" → 吃醋"""
        kind, reason = heart.detect_temper("我今天跟别的女生聊了几句工作")
        self.assertEqual(kind, "jealous")

    def test_weak_trigger_pretty(self):
        """夸"漂亮"（无排除语境）→ 吃醋"""
        kind, _ = heart.detect_temper("我们公司那个女生挺漂亮的")
        self.assertEqual(kind, "jealous")

    def test_exclude_scenery(self):
        """"晚霞好漂亮"是夸风景 → 不触发（排除词）"""
        self.assertIsNone(heart.detect_temper("今天的晚霞好漂亮啊"))

    def test_exclude_family(self):
        """提家人不算吃醋（我姐好看 → 不触发）"""
        self.assertIsNone(heart.detect_temper("我姐今天穿得挺好看的"))

    def test_soothe_detected(self):
        """道歉 → 哄"""
        kind, _ = heart.detect_temper("对不起嘛宝贝，我错啦")
        self.assertEqual(kind, "soothe")

    def test_soothe_first(self):
        """"对不起，但那个女生确实好看"→ 哄优先（先消气，不翻账）"""
        kind, _ = heart.detect_temper("对不起啦，虽然那个女生确实挺好看的")
        self.assertEqual(kind, "soothe")

    def test_apply_jealous_sets_mood(self):
        """触发吃醋 → 心情变 jealous，强度不低于 60"""
        h = _mkheart()
        h, event = heart.apply_temper(h, "我今天跟女同事一起吃饭")
        self.assertEqual(event[0], "jealous")
        self.assertEqual(h["mood"]["primary"], "jealous")
        self.assertGreaterEqual(h["mood"]["intensity"], 60)

    def test_escalate_to_angry(self):
        """连续触发（已经吃醋还提）→ 升级真生气"""
        h = _mkheart()
        heart.apply_temper(h, "我跟女同事吃饭")
        h, event = heart.apply_temper(h, "她长得好可爱哦")
        self.assertEqual(event[0], "jealous")
        self.assertEqual(h["mood"]["primary"], "angry")

    def test_soothe_anger_steps(self):
        """2026-08-23 用户实测调整：真生气（强度75）→ 哄一轮大降级（-35 → 40）→ 直接心软。
        （旧版 -25 只降到 50 还酸着，用户"哄了她也原谅了结果还是生气"；85+ 极端情况才要两轮）"""
        h = _mkheart()
        heart.apply_temper(h, "我跟女同事吃饭")
        heart.apply_temper(h, "她好可爱哦")  # 升级 angry（强度 75）
        h, event = heart.apply_temper(h, "对不起宝贝我错了")
        self.assertEqual(event[0], "soothe")
        self.assertEqual(h["mood"]["primary"], "content")  # 一轮哄就心软
        self.assertEqual(h["mood"]["intensity"], 45)
        # 哄到没气了再哄 → 无副作用（不会破坏好心情）；甜话 = 弱哄（2026-08-23 批2）
        h, event = heart.apply_temper(h, "我最喜欢你了，你最好看")
        self.assertEqual(event[0], "soothe_weak")
        self.assertEqual(h["mood"]["primary"], "content")

    def test_soothe_when_happy_no_side_effect(self):
        """心情好时哄她 → 心情不变（无副作用）"""
        h = _mkheart(mood={"primary": "happy", "intensity": 70, "causes": [], "secondary": None})
        _, event = heart.apply_temper(h, "你是最漂亮的！")
        self.assertEqual(event[0], "soothe_weak")  # 甜话系 = 弱哄（2026-08-23 批2）
        self.assertEqual(h["mood"]["primary"], "happy")

    def test_temper_adds_cause(self):
        """吃醋要记原因（她记得自己为什么吃醋）"""
        h = _mkheart()
        _, event = heart.apply_temper(h, "我前女友找我聊天")
        self.assertIn(event[1], h["mood"]["causes"])

    def test_angry_in_whitelist(self):
        """angry/jealous 必须在情绪白名单里（否则 LLM 建议会被拒）"""
        self.assertIn("angry", heart.EMOTIONS)
        self.assertIn("jealous", heart.EMOTIONS)

    def test_llm_suggestion_angry_accepted(self):
        """LLM 建议 angry → merge 接受（白名单生效）"""
        h = _mkheart()
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "angry", "intensity_delta": 10, "cause": "他惹我"}})
        self.assertEqual(h["mood"]["primary"], "angry")

    def test_angry_cannot_jump_to_happy(self):
        """程序判定 angry 时，LLM 不能一步跳回 happy（情绪稳定性：整块不采纳）"""
        h = _mkheart(mood={"primary": "angry", "intensity": 70, "causes": ["他惹我"], "secondary": None})
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "happy", "intensity_delta": 10, "cause": "想通了"}})
        self.assertEqual(h["mood"]["primary"], "angry")  # 情绪被钳住
        self.assertEqual(h["mood"]["intensity"], 70)     # 强度也不动（状态由程序掌控）

    # ===== 2026-08-23 用户实测修复（"哄了还生气"/"夸她可爱却吃醋"） =====

    def test_praise_you_not_jealous(self):
        """夸"你"（你好可爱/你笑起来真好看）是在甜她 → 不算吃醋"""
        self.assertIsNone(heart.detect_temper("你好可爱哦"))
        self.assertIsNone(heart.detect_temper("你笑起来真好看"))
        self.assertIsNone(heart.detect_temper("你今天穿的裙子好漂亮"))

    def test_praise_other_still_jealous(self):
        """提"那个女生/她"还是吃醋（豁免只放行夸"你"）"""
        kind, _ = heart.detect_temper("你看那个女生多漂亮")
        self.assertEqual(kind, "jealous")
        kind, _ = heart.detect_temper("她今天好可爱")
        self.assertEqual(kind, "jealous")

    def test_new_soothe_words(self):
        """2026-08-23 扩充的哄法词都能被识别（旧版只认道歉模板，常见哄法漏判）；
        2026-08-23 批2 强弱分级：道歉系 = soothe（强），甜话系 = soothe_weak（弱）"""
        for text in ("别这样嘛，我错怪你了", "消消气，我带你去吃火锅",
                     "都是我的错，我改"):
            kind, _ = heart.detect_temper(text)
            self.assertEqual(kind, "soothe", f"应识别为强哄：{text}")
        for text in ("么么哒，爱你", "抱抱你，想你了"):
            kind, _ = heart.detect_temper(text)
            self.assertEqual(kind, "soothe_weak", f"应识别为弱哄：{text}")

    def test_soothe_intense_anger_needs_two(self):
        """极端生气（强度85+）→ 哄一轮还酸着 → 再哄必和好（"先嘴硬两句"底线）"""
        h = _mkheart()
        heart.apply_temper(h, "我跟女同事吃饭")
        heart.apply_temper(h, "她好可爱哦")
        h["mood"]["intensity"] = 90  # 极端情况（多次吃醋叠加）
        h, event = heart.apply_temper(h, "对不起宝贝我错了")
        self.assertEqual(h["mood"]["primary"], "jealous")  # 还酸着
        h, event = heart.apply_temper(h, "我最喜欢你了，你最好看")
        self.assertEqual(h["mood"]["primary"], "content")  # 两轮必和好

    def test_passage_soothe_cools_anger(self):
        """非哄轮聊天也消气：聊几轮 angry/jealous 自然降 → 心软（真人气会慢慢消）"""
        h = _mkheart(mood={"primary": "angry", "intensity": 75, "causes": ["他惹我"], "secondary": None})
        for _ in range(3):
            heart.passage_soothe(h)  # 75-18=57 还在气
            self.assertEqual(h["mood"]["primary"], "angry")
        heart.passage_soothe(h)  # 57-6=51
        heart.passage_soothe(h)  # 51-6=45
        heart.passage_soothe(h)  # 45-6=39 ≤40 → 心软
        self.assertEqual(h["mood"]["primary"], "content")
        self.assertIn("气慢慢消了", h["mood"]["causes"][0])

    def test_passage_soothe_happy_noop(self):
        """心情好时 passage_soothe 无副作用"""
        h = _mkheart(mood={"primary": "happy", "intensity": 70, "causes": [], "secondary": None})
        heart.passage_soothe(h)
        self.assertEqual(h["mood"]["primary"], "happy")
        self.assertEqual(h["mood"]["intensity"], 70)

    def test_soothed_cannot_rejealous(self):
        """程序判定"他哄我了"和好 → LLM 不能再自报吃醋推翻和好（e2e 实测教训：
        她嘴硬表演会自报 jealous，不挡的话"哄→和好"闭环永远完成不了）"""
        h = _mkheart(mood={"primary": "content", "intensity": 45,
                           "causes": ["他哄我了（对不起）"], "secondary": None})
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "jealous", "intensity_delta": 20, "cause": "嘴上还要小闹一下"}})
        self.assertEqual(h["mood"]["primary"], "content")  # 被挡
        self.assertEqual(h["mood"]["intensity"], 45)       # 强度也不动

    def test_unsought_can_get_jealous(self):
        """没被程序判定过和好 → LLM 可自报吃醋（补充通道：程序关键词覆盖不到的触发）"""
        h = _mkheart()  # content，causes 空（没被哄过）
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "jealous", "intensity_delta": 15, "cause": "他放我鸽子"}})
        self.assertEqual(h["mood"]["primary"], "jealous")

    def test_jealous_round_locks_relation(self):
        """程序判定轮（吃醋）→ 情绪状态程序独占：LLM 报 sad 被挡、
        报 angry 加深也被挡（否则加深叠加抵消哄的降级，"哄两句就心软"达不到）"""
        h = _mkheart()
        _, ev = heart.apply_temper(h, "我今天跟女同事吃饭")  # 程序触发 jealous
        self.assertEqual(ev[0], "jealous")
        self.assertEqual(h["mood"]["primary"], "jealous")
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "sad", "intensity_delta": 5, "cause": "有点委屈"}}, ev)
        self.assertEqual(h["mood"]["primary"], "jealous")  # 还是吃醋
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "angry", "intensity_delta": 10, "cause": "越想越气"}}, ev)
        self.assertEqual(h["mood"]["primary"], "jealous")  # 加深也被挡
        self.assertEqual(h["mood"]["intensity"], 60)       # 强度程序定

    def test_soothe_round_no_intensify(self):
        """程序判定轮（他哄我了）→ 情绪状态程序独占（LLM 报什么都挡，
        含加深、含其他情绪——否则降级状态被覆盖，"哄两句就心软"达不到）"""
        h = _mkheart(mood={"primary": "jealous", "intensity": 50, "causes": ["他提到了女同事"], "secondary": None})
        ev = ("soothe", "他哄我了（对不起）")
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "angry", "intensity_delta": 30, "cause": "哼还是好气"}}, ev)
        self.assertEqual(h["mood"]["primary"], "jealous")  # 不加深
        self.assertEqual(h["mood"]["intensity"], 50)       # 强度也不动
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "neutral", "intensity_delta": -10, "cause": "..."}}, ev)
        self.assertEqual(h["mood"]["primary"], "jealous")  # 报别的也不改

    def test_jealous_cannot_jump_to_content(self):
        """吃醋时 LLM 不能直接 content——要程序判定哄了才慢慢消"""
        h = _mkheart(mood={"primary": "jealous", "intensity": 60, "causes": ["他提别人"], "secondary": None})
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "content", "intensity_delta": 5, "cause": "心情好"}})
        self.assertEqual(h["mood"]["primary"], "jealous")

    def test_angry_can_stay_or_intensify(self):
        """生气时 LLM 建议 sad（负面间转换）→ 允许（她可以气到难过）"""
        h = _mkheart(mood={"primary": "angry", "intensity": 70, "causes": [], "secondary": None})
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "sad", "intensity_delta": 5, "cause": "越想越委屈"}})
        self.assertEqual(h["mood"]["primary"], "sad")

    def test_angry_decays_overnight(self):
        """气不过夜：生气 12 小时后没人哄 → 降到烦躁（还闷着但不再炸）"""
        now = datetime(2026, 8, 20, 21, 0)
        h = _mkheart(mood={"primary": "angry", "intensity": 80, "causes": ["他惹我"], "secondary": None},
                     last_interaction=(now - timedelta(hours=13)).strftime("%Y-%m-%d %H:%M"))
        heart.apply_time_decay(h, now)
        self.assertEqual(h["mood"]["primary"], "frustrated")
        self.assertLessEqual(h["mood"]["intensity"], 50)

    # ===== 2026-08-23 批1：台词-状态双向同步（锚定/回写/门闩）+ 指数半衰期 =====

    def setUp(self):
        # 门闩是模块级全局（防"一段小脾气认两次账"），每个测试独立重置防污染
        heart._temper_written = False
        heart._temper_rounds = 0

    def test_temper_sets_since(self):
        """吃醋触发 → 记 since（指数衰减起点）；升级 angry 不刷新（同一段气）"""
        h = _mkheart()
        h, _ = heart.apply_temper(h, "我跟女同事吃饭")
        since1 = h["mood"].get("since")
        self.assertIsNotNone(since1)
        h, _ = heart.apply_temper(h, "她好可爱哦")  # 升级 angry
        self.assertEqual(h["mood"]["primary"], "angry")
        self.assertEqual(h["mood"].get("since"), since1)

    def test_line_detected(self):
        """她台词里说"原谅你了/和好啦"→ 检测到；否定/没气话 → 不算"""
        self.assertTrue(heart.temper_line_detected("好啦好啦，我原谅你了"))
        self.assertTrue(heart.temper_line_detected("不生气了，和好啦"))
        self.assertTrue(heart.temper_line_detected("没事啦，算了"))
        self.assertFalse(heart.temper_line_detected("我才不会原谅你呢"))
        self.assertFalse(heart.temper_line_detected("我还生你的气"))
        self.assertFalse(heart.temper_line_detected(""))

    def test_line_regress_angry_to_jealous(self):
        """台词回写：angry 一次认账 → 降级 jealous（只沿降级链，不一步和好）"""
        h = _mkheart(mood={"primary": "angry", "intensity": 85, "causes": [], "secondary": None})
        self.assertTrue(heart.line_regress(h, "他说我原谅他了"))
        self.assertEqual(h["mood"]["primary"], "jealous")
        self.assertEqual(h["mood"]["intensity"], 50)

    def test_line_regress_jealous_to_content(self):
        """台词回写：jealous 60 → -35 → 25 ≤ 40 → content，cause 记录亲口原谅"""
        h = _mkheart(mood={"primary": "jealous", "intensity": 60, "causes": [], "secondary": None})
        self.assertTrue(heart.line_regress(h, "他说我原谅他了"))
        self.assertEqual(h["mood"]["primary"], "content")
        self.assertIn("亲口说了原谅", h["mood"]["causes"][0])

    def test_line_regress_latch(self):
        """门闩：一段小脾气只认一次账（第二次说原谅不再降级）"""
        heart._temper_written = False
        h = _mkheart(mood={"primary": "angry", "intensity": 85, "causes": [], "secondary": None})
        self.assertTrue(heart.line_regress(h, "x"))
        self.assertEqual(h["mood"]["primary"], "jealous")
        self.assertFalse(heart.line_regress(h, "x"))  # 认过账 → 不认第二笔
        self.assertEqual(h["mood"]["primary"], "jealous")

    def test_line_regress_no_temper_noop(self):
        """没在生气 → 台词回写无副作用"""
        h = _mkheart()
        self.assertFalse(heart.line_regress(h, "x"))
        self.assertEqual(h["mood"]["primary"], "content")

    def test_line_regress_latch_opens_new_temper(self):
        """新一段小脾气 → 门闩重开（吃醋触发重置 _temper_written）"""
        heart._temper_written = True
        heart._temper_rounds = 0
        h = _mkheart(mood={"primary": "angry", "intensity": 85, "causes": [], "secondary": None})
        h, _ = heart.apply_temper(h, "我跟女同事吃饭")  # 新账：primary 不是 jealous → 重置
        self.assertFalse(heart._temper_written)
        self.assertTrue(heart.line_regress(h, "x"))  # 新账里说原谅 → 可再认

    def test_passage_soothe_exponential(self):
        """指数衰减：8 小时 τ≈2h 下 → 45 渐近 + 聊天加速器 → 心软（连续不跳变）"""
        now = datetime(2026, 8, 23, 12, 0)
        since = (now - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
        h = _mkheart(mood={"primary": "angry", "intensity": 90, "causes": [],
                           "secondary": None, "since": since})
        heart.passage_soothe(h, now=now)
        # V = 45 + 45·e^(-8/2) ≈ 45.8 - 6(加速器) ≈ 40 → ≤40 → content（强度重置为 45）
        self.assertEqual(h["mood"]["primary"], "content")
        self.assertEqual(h["mood"]["intensity"], 45)

    def test_passage_soothe_fresh_is_linear_step(self):
        """刚触发（Δt≈0）→ 衰减项≈0，只吃聊天加速器 -6（连续、不跳变）"""
        h = _mkheart(mood={"primary": "angry", "intensity": 75, "causes": [], "secondary": None,
                           "since": datetime.now().strftime("%Y-%m-%d %H:%M")})
        heart.passage_soothe(h)
        self.assertEqual(h["mood"]["intensity"], 69)
        self.assertEqual(h["mood"]["primary"], "angry")

    def test_soothed_block_after_line_regress(self):
        """台词回写和好后 → LLM 不能自报 jealous 卷土重来（和好是程序的裁决）"""
        h = _mkheart(mood={"primary": "content", "intensity": 45,
                           "causes": ["我亲口说了原谅他的话（x）"], "secondary": None})
        heart.merge_llm_suggestion(h, {"mood_change": {"emotion": "jealous", "intensity_delta": 20, "cause": "还要小闹"}})
        self.assertEqual(h["mood"]["primary"], "content")
        self.assertEqual(h["mood"]["intensity"], 45)


class TestGrudge(unittest.TestCase):
    """2026-08-23 批2 B-P1-1 怨气分层（ALMA：情绪分钟级/心情天级）：
    grudge 0-100 半衰期 24h；触发+15、哄-5、台词回写-10；高怨气 → 新账起点更炸"""

    def test_default_heart_has_grudge(self):
        """新心带 grudge 0 + grudge_since"""
        h = heart.default_heart()
        self.assertEqual(h["grudge"], 0)
        self.assertIn("grudge_since", h)

    def test_load_heart_compat_no_grudge(self):
        """老 heart.json 没 grudge 字段 → 补 0（兼容，不崩）"""
        import json
        p = os.path.join(TMP, "old_heart.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"mood": {"primary": "content", "intensity": 50, "causes": []},
                       "affection": 60}, f)
        old = heart.HEART_FILE
        heart.HEART_FILE = p
        try:
            h = heart.load_heart()
            self.assertEqual(h["grudge"], 0)
            self.assertIn("grudge_since", h)
        finally:
            heart.HEART_FILE = old

    def test_trigger_adds_grudge(self):
        """吃醋 → 怨气 +15"""
        h = _mkheart()
        heart.apply_temper(h, "我跟女同事吃饭")
        self.assertEqual(h["grudge"], 15)

    def test_escalation_adds_grudge(self):
        """连续触发升级 → 怨气再 +15（气上加气，账越记越多）"""
        h = _mkheart()
        heart.apply_temper(h, "我跟女同事吃饭")
        heart.apply_temper(h, "她好可爱哦")
        self.assertEqual(h["grudge"], 30)

    def test_soothe_reduces_grudge_slowly(self):
        """哄 → 情绪大降、怨气只 -5（哄消情绪快、消怨气慢）"""
        h = _mkheart()
        heart.apply_temper(h, "我跟女同事吃饭")
        h, _ = heart.apply_temper(h, "对不起宝贝我错了")
        self.assertEqual(h["grudge"], 10)  # 15-5

    def test_line_regress_reduces_more(self):
        """她亲口说原谅 → 怨气 -10（比被哄更可信）"""
        h = _mkheart()
        heart.apply_temper(h, "我跟女同事吃饭")
        heart.line_regress(h, "测试")
        self.assertEqual(h["grudge"], 5)  # 15-10

    def test_grudge_half_life_24h(self):
        """24h 衰减 → 正好半衰（60 → 30）；72h → 12.5%（记仇记不过三天）"""
        now = datetime(2026, 8, 23, 12, 0)
        since = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        h = _mkheart(grudge=60, grudge_since=since)
        heart.grudge_decay(h, now)
        self.assertAlmostEqual(h["grudge"], 30, delta=1)
        since72 = (now - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M")
        h = _mkheart(grudge=60, grudge_since=since72)
        heart.grudge_decay(h, now)
        self.assertAlmostEqual(h["grudge"], 7.5, delta=1)

    def test_grudge_decay_idempotent(self):
        """幂等：同一时刻调 N 次结果一样（不累积扣）"""
        now = datetime(2026, 8, 23, 12, 0)
        since = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
        h = _mkheart(grudge=80, grudge_since=since)
        heart.grudge_decay(h, now)
        first = h["grudge"]
        for _ in range(5):
            heart.grudge_decay(h, now)
        self.assertEqual(h["grudge"], first)

    def test_grudge_high_starts_new_temper_hotter(self):
        """怨气高 → 新一段小脾气起点更高（平时记着仇，一碰就炸得更凶）"""
        h = _mkheart(grudge=80)
        heart.apply_temper(h, "我跟女同事吃饭")
        # 触发先 +15 → 95；起点 = 45 + 95//2 = 92（≥60 底线；无怨气时是 60）
        self.assertEqual(h["mood"]["intensity"], 92)

    def test_grudge_in_describe(self):
        """describe 注入怨气（高 → 翻旧账提示 / 低 → 没积怨）"""
        h = _mkheart(grudge=70)
        self.assertIn("记着上次的账", heart.describe(h))
        h = _mkheart(grudge=5)
        self.assertIn("没有积怨", heart.describe(h))


class TestSootheStrength(unittest.TestCase):
    """2026-08-23 批2 B-P1-2 哄话强弱分级：强哄（道歉/认错/承诺）-35 沿降级链，
    弱哄（甜话/亲亲抱抱）只 -18——防"随口说爱你"秒消气"""

    def test_weak_soothe_half_step(self):
        """angry 75 → 弱哄（爱你）只 -18 → 57 还酸着（甜话消不了真火）"""
        h = _mkheart(mood={"primary": "angry", "intensity": 75, "causes": [], "secondary": None})
        h, event = heart.apply_temper(h, "我爱你啦，别想太多")
        self.assertEqual(event[0], "soothe_weak")
        self.assertEqual(h["mood"]["intensity"], 57)
        self.assertEqual(h["mood"]["primary"], "jealous")  # 甜话让"炸"变"酸"，但还没到和好

    def test_weak_soothe_cannot_fully_soothe_single_round(self):
        """angry 60 → 弱哄 -18 → 42 还酸着（随口甜话一轮哄不好，但"不炸了"变酸）"""
        h = _mkheart(mood={"primary": "angry", "intensity": 60, "causes": [], "secondary": None})
        heart.apply_temper(h, "抱抱你，想你啦")
        self.assertEqual(h["mood"]["primary"], "jealous")
        self.assertEqual(h["mood"]["intensity"], 42)

    def test_strong_wins_over_weak(self):
        """一句话里又有道歉又有甜话 → 按强算（道歉是主菜，甜话是加分）"""
        kind, _ = heart.detect_temper("对不起宝贝，我爱你")
        self.assertEqual(kind, "soothe")

    def test_weak_then_strong_two_rounds(self):
        """angry 90：弱哄一轮 72 变酸（jealous）→ 强哄一轮 37 → 和好（弱+强两轮）"""
        h = _mkheart(mood={"primary": "angry", "intensity": 90, "causes": [], "secondary": None})
        heart.apply_temper(h, "你最好了，爱你")
        self.assertEqual(h["mood"]["primary"], "jealous")
        h, event = heart.apply_temper(h, "对不起我错了")
        self.assertEqual(event[0], "soothe")
        self.assertEqual(h["mood"]["primary"], "content")

    def test_weak_soothe_grudge_also_reduces(self):
        """弱哄也消怨气 -5（甜话虽然消不了火，但也在哄）"""
        h = _mkheart(grudge=30)
        heart.apply_temper(h, "抱抱你")
        self.assertEqual(h["grudge"], 25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
