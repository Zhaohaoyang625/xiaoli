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
        """真生气 → 第一次哄降级成吃醋（还酸着）→ 再哄才心软回 content（哄几句才和好）"""
        h = _mkheart()
        heart.apply_temper(h, "我跟女同事吃饭")
        heart.apply_temper(h, "她好可爱哦")  # 升级 angry
        h, event = heart.apply_temper(h, "对不起宝贝我错了")
        self.assertEqual(event[0], "soothe")
        self.assertEqual(h["mood"]["primary"], "jealous")  # 还酸着
        h, event = heart.apply_temper(h, "我最喜欢你了，你最好看")
        self.assertEqual(h["mood"]["primary"], "content")  # 完全和好

    def test_soothe_when_happy_no_side_effect(self):
        """心情好时哄她 → 心情不变（无副作用）"""
        h = _mkheart(mood={"primary": "happy", "intensity": 70, "causes": [], "secondary": None})
        _, event = heart.apply_temper(h, "你是最漂亮的！")
        self.assertEqual(event[0], "soothe")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
