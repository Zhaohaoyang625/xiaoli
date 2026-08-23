# ============================================
# 成本组回归测试（2026-08-23 C1/C2，月费 ¥90→¥15 的头号杠杆）
# C2 缓存前缀重排：动态块（时间戳）必须在 user 输入前的最末位——
#   前缀从第 0 token 完全匹配才命中缓存；时间戳每分钟变化，插在中间
#   会让后面的摘要+全部历史永远命中不了缓存
# C1 API 超时：统一 llm.get_client()，连接 5s/读取 30s（SDK 默认 600s，
#   断网时主循环卡死 30 分钟）
# ============================================

import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xiaoli import context
from xiaoli import llm

P = "你是小李，台湾甜妹。"


def _diary():
    return {
        "summary": "他喜欢喝奶茶，最近在减肥",
        "daily": {"2026-08-22": "聊了周末去哪玩"},
        "emotion_line": "（8/20 吃醋吵架→哄好）",
        "messages": [
            {"role": "user", "time": "2026-08-23 10:00", "content": "早啊"},
            {"role": "assistant", "time": "2026-08-23 10:01", "content": "早齁～"},
        ],
    }


def _workbench(now_str, user="今天天气不错"):
    with mock.patch("xiaoli.context.world_brief.load_brief_injection",
                    return_value="【她最近看到的】世界大事：日本地震。") as _, \
         mock.patch("xiaoli.context.life_calendar.today_sense",
                    return_value="【她眼里的今天】农历七月十一"):
        return context.build_workbench(P, _diary(), user,
                                       now=datetime.strptime(now_str, "%Y-%m-%d %H:%M"))


class TestPrefixStability(unittest.TestCase):
    """C2：跨轮前缀必须稳定（这是缓存命中的前提）"""

    def test_prefix_identical_across_minutes(self):
        """同一对话两轮（时间相差 1 分钟）→ 除时间戳和用户输入外完全一致"""
        a = _workbench("2026-08-23 10:00")
        b = _workbench("2026-08-23 10:01")
        self.assertEqual(len(a), len(b))
        # 去掉最后两条（时间戳 + user）后，前缀必须逐条一致
        for i in range(len(a) - 2):
            self.assertEqual(a[i], b[i], f"前缀第 {i} 条变了——缓存必然失效")

    def test_timestamp_is_last_system_message(self):
        """时间戳（每分钟变化）必须紧贴 user 前，不能进前缀"""
        msgs = _workbench("2026-08-23 10:00")
        self.assertIn("现在的时间和日期", msgs[-2]["content"])
        self.assertEqual(msgs[-1], {"role": "user", "content": "今天天气不错"})

    def test_dynamic_blocks_after_history(self):
        """世界简报/生活日历（每天变）必须在历史之后（动态区）"""
        msgs = _workbench("2026-08-23 10:00")
        last_hist = 0
        for i, m in enumerate(msgs):
            if m["role"] == "user" and m["content"].startswith("[2026-08-23"):
                last_hist = i
        world_i = next(i for i, m in enumerate(msgs) if "她最近看到的" in m["content"])
        cal_i = next(i for i, m in enumerate(msgs) if "她眼里的今天" in m["content"])
        self.assertGreater(world_i, last_hist, "世界简报进了前缀区")
        self.assertGreater(cal_i, last_hist, "生活日历进了前缀区")

    def test_static_head_untouched(self):
        """前缀头部（人设+摘要+回顾+情绪线+历史）内容完整保留"""
        msgs = _workbench("2026-08-23 10:00")
        contents = [m["content"] for m in msgs]
        self.assertEqual(contents[0], P)
        self.assertTrue(any("他喜欢喝奶茶" in c for c in contents))
        self.assertTrue(any("08-22" in c for c in contents))
        self.assertTrue(any("吃醋吵架" in c for c in contents))


class TestRetryThin(unittest.TestCase):
    """C3：提醒重试请求瘦身（历史只留最近 10 轮，最坏 4 倍→~1.5 倍）"""

    @staticmethod
    def _long_messages(rounds=40):
        from xiaoli import chat
        msgs = [{"role": "system", "content": "你是小李，台湾甜妹。"},
                {"role": "system", "content": "【过去的记忆】他喜欢喝奶茶"}]
        for i in range(rounds):
            msgs.append({"role": "user", "content": f"[2026-08-23 09:{i % 60:02d}] 第{i}句"})
            msgs.append({"role": "assistant", "content": f"第{i}句回应"})
        msgs.append({"role": "system", "content": "【她最近看到的】日本地震。"})
        msgs.append({"role": "system", "content": "【现在的时间和日期】2026年08月23日"})
        msgs.append({"role": "user", "content": "记得提醒我吃药"})
        return msgs

    def test_thin_keeps_static_and_tail(self):
        """长历史 → 人设/记忆/动态块全保留，历史只留最近 10 轮，当前输入在最后"""
        from xiaoli import chat
        msgs = self._long_messages()
        out = chat._thin_for_retry(msgs)
        # 4 个 system 块（人设/记忆/世界简报/时间戳）+ 最近 20 条历史 + 当前输入
        self.assertEqual(len(out), 4 + 20 + 1)
        self.assertEqual(out[0], msgs[0])  # persona 保留
        self.assertEqual(out[1], msgs[1])  # 记忆保留
        self.assertIn("她最近看到的", out[2]["content"])  # 动态块保留
        self.assertEqual(out[-1]["content"], "记得提醒我吃药")  # 当前输入在最后
        # 历史只留最近：第 30 轮在，第 10 轮不在
        joined = " ".join(m["content"] for m in out)
        self.assertIn("第39句", joined)
        self.assertNotIn("第10句", joined)

    def test_short_messages_unchanged(self):
        """历史很短 → 原样返回（不裁剪）"""
        from xiaoli import chat
        msgs = [{"role": "system", "content": "你是小李。"},
                {"role": "user", "content": "早"},
                {"role": "assistant", "content": "早齁"},
                {"role": "user", "content": "提醒我吃药"}]
        self.assertEqual(chat._thin_for_retry(msgs), msgs)

    def test_current_input_not_duplicated(self):
        """当前输入只出现一次（历史尾部切边不重复）"""
        from xiaoli import chat
        msgs = self._long_messages()
        out = chat._thin_for_retry(msgs)
        cnt = sum(1 for m in out if m.get("content") == "记得提醒我吃药")
        self.assertEqual(cnt, 1)


class TestTimeout(unittest.TestCase):
    """C1：统一 client 的超时配置"""

    @mock.patch("xiaoli.llm.config.DEEPSEEK_API_KEY", "sk-test")
    def test_connect_5s_read_30s(self):
        client = llm.get_client()
        self.assertEqual(client.timeout.connect, 5)
        self.assertEqual(client.timeout.read, 30)

    def test_constant_defined(self):
        self.assertIsNotNone(llm.DEFAULT_TIMEOUT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
