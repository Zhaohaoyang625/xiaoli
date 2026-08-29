# ============================================
# 调度器→handle_event 调用链回归（2026-08-29）
# 教训：僵尸清理删了 handle_event 的 extra 参数、没同步 Scheduler 调用点 →
# 每秒 TypeError 刷屏，主动功能全废（测试没盖到真实调用链）。
# 这道锁：Scheduler 触发任何事件，handle_event 都不能抛 TypeError。
# ============================================

import unittest
from unittest import mock

from xiaoli import proactive


class TestSchedulerEventChain(unittest.TestCase):
    def test_tick_calls_handler_with_three_args(self):
        """Scheduler 回调契约 = 3 参数 (event_type, content, extra)：
        handle_event 必须收得下（Z25 清理曾删参导致 TypeError 刷屏）"""
        calls = []
        sched = proactive.Scheduler(lambda *a: calls.append(a))
        sched._refresh_world_bg = lambda: None  # 不联网
        with mock.patch("xiaoli.proactive.get_window_now", return_value=("morning", "早安")), \
             mock.patch("xiaoli.proactive.should_fire_window", return_value=True), \
             mock.patch("xiaoli.proactive.try_use_proactive_budget", return_value=True):
            sched._tick(None)
        self.assertTrue(calls, "节奏窗口到点应触发事件")
        self.assertEqual(len(calls[0]), 3, "回调必须 3 参数（TypeError 回归）")
        self.assertEqual(calls[0][0], "节奏")
        self.assertEqual(calls[0][1], "早安")

    def test_handle_event_accepts_three_args(self):
        """handle_event 本身签名兼容 3 参（默认 extra）"""
        import inspect
        import sys
        sys.path.insert(0, "xiaoli")
        from xiaoli import chat as chat_mod
        params = inspect.signature(chat_mod.handle_event).parameters
        self.assertIn("extra", params)


if __name__ == "__main__":
    unittest.main()
