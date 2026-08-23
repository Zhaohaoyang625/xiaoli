# ============================================
# 特殊日子（生日/节日）回归测试
# 测：生日提取 / 生日存储 / 特殊日检查（公历+农历） / 每天只触发一次 / 调度器链路
# ============================================

import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import special
from xiaoli import proactive

TMP = tempfile.mkdtemp()
# 测试数据全写到临时目录，不碰真实 data/
special.SPECIAL_FILE = os.path.join(TMP, "special.json")
special.BIRTHDAY_FILE = os.path.join(TMP, "birthday.json")
proactive.PROACTIVE_FILE = os.path.join(TMP, "proactive.json")
proactive.REMINDERS_FILE = os.path.join(TMP, "reminders.json")


class TestAskBirthday(unittest.TestCase):
    """她主动问生日：不知道 + 每隔3天问一次，知道了就不再问"""

    def setUp(self):
        # 重置状态：没有生日记录 + 很久没问过（2020年1月1日）
        if os.path.exists(special.BIRTHDAY_FILE):
            os.remove(special.BIRTHDAY_FILE)
        special.mark_asked_birthday(datetime(2020, 1, 1))

    def test_never_asked_should_ask(self):
        if os.path.exists(special.SPECIAL_FILE):
            os.remove(special.SPECIAL_FILE)
        self.assertTrue(special.should_ask_birthday(datetime(2026, 8, 20)))

    def test_just_asked_not_again(self):
        special.mark_asked_birthday(datetime(2026, 8, 20))
        self.assertFalse(special.should_ask_birthday(datetime(2026, 8, 21)))
        self.assertFalse(special.should_ask_birthday(datetime(2026, 8, 22)))

    def test_three_days_later_ask_again(self):
        special.mark_asked_birthday(datetime(2026, 8, 20))
        self.assertTrue(special.should_ask_birthday(datetime(2026, 8, 23)))

    def test_known_birthday_no_ask(self):
        special.save_birthday(9, 15)
        self.assertFalse(special.should_ask_birthday(datetime(2026, 8, 20)))


class TestBirthdayExtract(unittest.TestCase):
    def test_normal_forms(self):
        """各种正常说法都能提取"""
        self.assertEqual(special.extract_birthday("我生日是9月15号"), (9, 15))
        self.assertEqual(special.extract_birthday("我的生日在12月3日"), (12, 3))
        self.assertEqual(special.extract_birthday("人家生日：6月28号"), (6, 28))
        self.assertEqual(special.extract_birthday("他生日是2001年2月14日"), (2, 14))

    def test_false_positive_guard(self):
        """没有"生日"关键词 → 不提取（防误报）"""
        self.assertIsNone(special.extract_birthday("12月我要去考试"))
        self.assertIsNone(special.extract_birthday("生日快乐"))
        self.assertIsNone(special.extract_birthday("今天天气不错"))
        self.assertIsNone(special.extract_birthday("我8月20号有空"))

    def test_invalid_date(self):
        """13月32日这类不合法日期 → 不提取"""
        self.assertIsNone(special.extract_birthday("生日是13月32号"))


class TestBirthdayStore(unittest.TestCase):
    def test_save_load_roundtrip(self):
        self.assertIsNone(special.load_birthday())
        special.save_birthday(9, 15)
        self.assertEqual(special.load_birthday(), {"month": 9, "day": 15})


class TestCheckToday(unittest.TestCase):
    def test_birthday_and_holiday_together(self):
        """2月14日出生 → 生日和情人节一起报"""
        special.save_birthday(2, 14)
        desc = special.check_today(datetime(2026, 2, 14, 8, 0))
        self.assertIn("生日", desc)
        self.assertIn("情人节", desc)

    def test_normal_day_none(self):
        """普通日子 → None"""
        self.assertIsNone(special.check_today(datetime(2026, 8, 20, 12, 0)))

    def test_solar_holiday(self):
        """公历节日：12月25日圣诞节"""
        desc = special.check_today(datetime(2026, 12, 25, 9, 0))
        self.assertIn("圣诞节", desc)

    def test_lunar_holiday(self):
        """农历节日：2026-08-19 = 农历七月初七 = 七夕（lunardate 实测日期）"""
        desc = special.check_today(datetime(2026, 8, 19, 9, 0))
        self.assertIn("七夕", desc)


class TestFireOnce(unittest.TestCase):
    def test_once_per_day(self):
        now = datetime(2026, 2, 14, 8, 0)
        self.assertTrue(special.should_fire_today(now))
        special.mark_fired_today(now)
        self.assertFalse(special.should_fire_today(now))
        # 第二天又能触发
        self.assertTrue(special.should_fire_today(datetime(2026, 2, 15, 8, 0)))
        # 程序重启同一天也不重复
        self.assertFalse(special.should_fire_today(datetime(2026, 2, 14, 23, 0)))


class TestSchedulerLink(unittest.TestCase):
    def setUp(self):
        # 前面的测试把 2/14 标成已触发过了 → 拨回前一天，保证本组可触发
        special.mark_fired_today(datetime(2026, 2, 13))
        # 重置互动计时器（别的测试留下的 mark_activity 会误触发"闲聊保险"，污染本组）
        proactive._last_activity = None
        proactive._last_idle_chat = None
        proactive._proactive_enabled = True
        proactive._recording = False

    def test_scheduler_fires_special_once(self):
        """调度器到点调用 on_event("特殊日", …)，同一天不重复"""
        fired = []
        sched = proactive.Scheduler(lambda *a: fired.append(a))
        special.save_birthday(2, 14)
        sched._tick(datetime(2026, 2, 14, 8, 0))
        special_events = [e for e in fired if e[0] == "特殊日"]
        self.assertEqual(len(special_events), 1)
        self.assertIn("生日", special_events[0][1])
        self.assertIn("情人节", special_events[0][1])
        # 同一天再来一次 → 不再触发
        sched._tick(datetime(2026, 2, 14, 9, 0))
        special_events = [e for e in fired if e[0] == "特殊日"]
        self.assertEqual(len(special_events), 1)

    def test_scheduler_asks_birthday(self):
        """生日未知 + 很久没问 → 调度器触发"问他生日"；刚问过 → 不再触发"""
        if os.path.exists(special.BIRTHDAY_FILE):
            os.remove(special.BIRTHDAY_FILE)
        special.mark_asked_birthday(datetime(2020, 1, 1))
        fired = []
        sched = proactive.Scheduler(lambda *a: fired.append(a))
        sched._tick(datetime(2026, 8, 20, 8, 0))
        asks = [e for e in fired if "他的生日" in e[1]]  # 精确匹配：闲聊消息里没有"他的生日"
        self.assertEqual(len(asks), 1)
        # 第二天再来 → 间隔不足3天，不再触发
        sched._tick(datetime(2026, 8, 21, 8, 0))
        asks = [e for e in fired if "他的生日" in e[1]]
        self.assertEqual(len(asks), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
