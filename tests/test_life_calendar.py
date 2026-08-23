# ============================================
# 生活日历回归测试（2026-08-23 世界认知系统·第一件：她的"日子感"）
# life_calendar：农历+节气+季节+节日倒计时 → 工作台注入
# 设计纪律：纯本地零成本；历法库不可用降级不炸；注入在历史后用户前
# ============================================

import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xiaoli import life_calendar
from xiaoli import special


class TestTodaySense(unittest.TestCase):
    """today_sense：她眼里的今天"""

    def test_jieqi_and_season(self):
        """2026-08-23：处暑日 → 感知节气+季节+农历"""
        out = life_calendar.today_sense(datetime(2026, 8, 23, 10, 0))
        self.assertIn("农历", out)
        self.assertIn("处暑", out)   # 当天节气
        self.assertIn("夏天", out)   # 季节

    def test_solar_holiday_countdown(self):
        """2026-09-30：明天就是国庆节（公历节日表，稳）"""
        out = life_calendar.today_sense(datetime(2026, 9, 30, 10, 0))
        self.assertIn("明天就是国庆节", out)
        self.assertIn("秋天", out)   # 9 月入秋

    def test_lunar_holiday_countdown(self):
        """中秋（农历八月十五）前 5 天 → 感知到（2026 中秋=9/25）"""
        out = life_calendar.today_sense(datetime(2026, 9, 20, 10, 0))
        self.assertIn("中秋", out)
        self.assertIn("再过 5 天", out)

    def test_normal_day_no_jieqi(self):
        """非节气日不提节气名；入秋后说秋天"""
        out = life_calendar.today_sense(datetime(2026, 9, 1, 10, 0))
        self.assertNotIn("处暑", out)
        self.assertIn("秋天", out)

    def test_birthday_soon(self):
        """他生日 12 天后 → 提到"""
        with mock.patch.object(special, "load_birthday",
                               return_value={"month": 9, "day": 4}):
            out = life_calendar.today_sense(datetime(2026, 8, 23, 10, 0))
            self.assertIn("再过 12 天是他生日", out)

    def test_birthday_far_not_mentioned(self):
        """生日太远（12/25）→ 不提"""
        with mock.patch.object(special, "load_birthday",
                               return_value={"month": 12, "day": 25}):
            out = life_calendar.today_sense(datetime(2026, 8, 23, 10, 0))
            self.assertNotIn("生日", out)

    def test_no_birthday_ok(self):
        """没记住生日 → 不炸，节日照常"""
        with mock.patch.object(special, "load_birthday", return_value=None):
            out = life_calendar.today_sense(datetime(2026, 9, 30, 10, 0))
            self.assertIn("国庆节", out)
            self.assertNotIn("生日", out)

    def test_lunar_unavailable_degrades(self):
        """历法不可用 → 降级为季节+公历节日（不炸）"""
        with mock.patch.object(life_calendar, "_lunar_text", return_value=""), \
             mock.patch.object(life_calendar, "_jieqi_of", return_value=""), \
             mock.patch.object(life_calendar, "_lunar_month_day", return_value=None):
            out = life_calendar.today_sense(datetime(2026, 9, 30, 10, 0))
            self.assertIn("现在是秋天", out)
            self.assertIn("明天就是国庆节", out)  # 公历节日表不受历法影响


class TestWorkbenchInjection(unittest.TestCase):
    """build_workbench：生活日历注入在"历史后、用户前"（动态区）"""

    @mock.patch("xiaoli.context.life_calendar.today_sense",
                return_value="【她眼里的今天】农历七月十一，现在是夏天。")
    @mock.patch("xiaoli.context.world_brief.load_brief_injection", return_value="")
    def test_injects_after_history(self, m_slang, m_sense):
        from xiaoli import context
        diary = {"messages": [
            {"role": "user", "content": "你好", "time": "2026-08-22 10:00"},
            {"role": "assistant", "content": "嗨～"},
        ]}
        msgs = context.build_workbench("人设", diary, "最近怎么样")
        idx = [m.get("content", "") for m in msgs].index(
            "【她眼里的今天】农历七月十一，现在是夏天。")
        self.assertEqual(msgs[idx]["role"], "system")
        self.assertLess(idx, len(msgs) - 1)
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertEqual(msgs[idx - 1]["role"], "assistant")

    @mock.patch("xiaoli.context.life_calendar.today_sense", return_value="")
    def test_empty_no_inject(self, m_sense):
        from xiaoli import context
        msgs = context.build_workbench("人设", {"messages": []}, "你好")
        self.assertFalse(any("她眼里的今天" in m.get("content", "") for m in msgs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
