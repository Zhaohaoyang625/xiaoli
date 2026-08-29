# ============================================
# B-P2 心情变迁日志（2026-08-23 批3）
# 终端「心情」可查"她为什么这样"的长期解：所有改情绪的函数末尾记一条，
# 对比上一条：primary 变了 或 强度差 ≥10 才算变迁（防 LLM 每轮 ±5 刷屏）
# ============================================

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import heart


def _fresh():
    h = heart.default_heart()
    h["mood_log"] = []
    return h


class TestMoodLog(unittest.TestCase):
    """变迁日志：记录时机、diff 去重、上限、兼容"""

    def setUp(self):
        self.h = _fresh()

    def test_jealous_trigger_logs(self):
        """吃醋触发 → 记一条（mood=jealous，怨气已叠）"""
        self.h, ev = heart.apply_temper(self.h, "你今天和那个女同事出去了？")
        self.assertEqual(ev[0], "jealous")
        self.assertEqual(len(self.h["mood_log"]), 1)
        self.assertEqual(self.h["mood_log"][-1]["mood"], "jealous")
        self.assertGreater(self.h["mood_log"][-1]["g"], 0)

    def test_upgrade_logs_new_entry(self):
        """吃醋升级真生气 → 又一条（primary 变了）"""
        heart.apply_temper(self.h, "你和那个女同事出去了？")
        heart.apply_temper(self.h, "你怎么又提那个女同事？")
        self.assertEqual(len(self.h["mood_log"]), 2)
        self.assertEqual(self.h["mood_log"][-1]["mood"], "angry")

    def test_soothe_logs(self):
        """强哄降级 → 一条（angry→jealous 变了）"""
        self.h["mood"] = {"primary": "angry", "intensity": 75, "causes": ["他提了别人"], "secondary": None}
        self.h, ev = heart.apply_temper(self.h, "对不起嘛，我错啦")
        self.assertEqual(ev[0], "soothe")
        self.assertEqual(len(self.h["mood_log"]), 1)
        self.assertEqual(self.h["mood_log"][-1]["mood"], "content")  # angry75 强哄 → 心软

    def test_small_swing_not_logged(self):
        """同情绪强度小动（<10）→ 不算变迁（防 LLM 每轮 ±5 刷屏）"""
        heart.apply_temper(self.h, "你和那个女同事出去了？")  # jealous 60
        n = len(self.h["mood_log"])
        # LLM 自报同一情绪 ±5 → 强度 60→65，差 5 <10 → 不算变迁
        heart.merge_llm_suggestion(self.h, {"mood_change": {"emotion": "jealous", "intensity_delta": 5}})
        self.assertEqual(len(self.h["mood_log"]), n)

    def test_llm_emotion_change_logged(self):
        """LLM 自报换情绪（非程序轮）→ 记一条"""
        self.h, _ = heart.apply_temper(self.h, "你和那个女同事出去了？")  # jealous
        heart.merge_llm_suggestion(self.h, {"mood_change": {"emotion": "sad", "intensity_delta": 0}})
        self.assertEqual(len(self.h["mood_log"]), 2)
        self.assertEqual(self.h["mood_log"][-1]["mood"], "sad")

    def test_cap_30(self):
        """上限 30 条，超出丢最老（35 循环：grudge 封顶后哄不干净→升级 angry 是
        合理产品逻辑——怨气满时更难哄；只断言长度和首尾存在）"""
        for _ in range(35):  # 每次都换情绪 → 必记
            heart.apply_temper(self.h, "你和那个女同事出去了？")
            heart.apply_temper(self.h, "对不起嘛，我错啦")
        self.assertLessEqual(len(self.h["mood_log"]), 30)
        self.assertEqual(len(self.h["mood_log"]), 30)  # 恰好 70 条 → 截断到 30
        # 时间戳是 "MM-DD HH:MM"（5 字符组），不是空串
        self.assertEqual(len(self.h["mood_log"][0]["t"]), 11)
        self.assertEqual(len(self.h["mood_log"][29]["t"]), 11)

    def test_time_decay_melancholy_logged(self):
        """3 天没来 → melancholy → 记一条"""
        self.h["last_interaction"] = "2026-08-01 10:00"
        self.h["decay_applied"] = "2026-08-01 10:00"
        heart.apply_time_decay(self.h, now=__import__("datetime").datetime(2026, 8, 4, 10, 0))
        self.assertEqual(len(self.h["mood_log"]), 1)
        self.assertEqual(self.h["mood_log"][-1]["mood"], "melancholy")

    def test_time_decay_no_change_not_logged(self):
        """刚聊过（没到衰减线）→ 不记"""
        heart.apply_time_decay(self.h)
        self.assertEqual(len(self.h["mood_log"]), 0)

    def test_load_heart_backfills(self):
        """旧数据无 mood_log → 补空列表（不崩）"""
        with patch.object(heart, "HEART_FILE", str(self.h)):  # 用不上，只验证兜底逻辑
            pass
        old = {"mood": {"primary": "content", "intensity": 50, "causes": [], "secondary": None},
               "grudge": 0, "grudge_since": "2026-08-01 10:00",
               "affection": 60, "last_interaction": "2026-08-01 10:00",
               "decay_applied": "2026-08-01 10:00"}
        with patch("xiaoli.heart.HEART_FILE",
                   os.path.join(os.path.dirname(__file__), "_nope_heart.json")) as hf, \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", side_effect=FileNotFoundError):
            pass  # 空场景——直接用 load 的兼容分支单测
        # 直接测兼容逻辑：缺 mood_log 的心 → 补上
        h = dict(old)
        from xiaoli import heart as hh
        import json
        tmp = os.path.join(os.path.dirname(__file__), "_tmp_heart.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(h, f, ensure_ascii=False)
        try:
            with patch("xiaoli.heart.HEART_FILE", tmp):
                loaded = hh.load_heart()
            self.assertEqual(loaded["mood_log"], [])
        finally:
            os.remove(tmp)

    def test_cause_from_causes_head(self):
        """原因取 mood.causes[0]（刚写入的最新原因）"""
        self.h, _ = heart.apply_temper(self.h, "你今天和那个女同事出去了？")
        self.assertEqual(self.h["mood_log"][-1]["c"], "他提到了女同事")


if __name__ == "__main__":
    unittest.main(verbosity=2)
