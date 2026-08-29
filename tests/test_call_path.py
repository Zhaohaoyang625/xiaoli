# ============================================
# 大脑调用路径测试（2026-08-23 用户实测"回复延迟长"）
# call_xiaoli 双路径：
#   问到实时信息（天气/新闻/热搜…）→ Responses + web_search（模型可上网）
#   日常聊天 → Chat Completions 快路径（不挂搜索 → 每轮省 2~5 秒 + 3 分钱）
# 防回归：谁把 web_search 偷偷挂回每轮、或日常对话误走慢路径，这里会红
# ============================================

import time
import unittest
from unittest import mock

import numpy as np

from xiaoli import chat


def _completions(content):
    resp = mock.Mock()
    resp.choices = [mock.Mock()]
    resp.choices[0].message.content = content
    return resp


def _responses(content):
    resp = mock.Mock()
    resp.output_text = content
    return resp


class TestCallPath(unittest.TestCase):
    def _client(self, completions_content=None, responses_content=None):
        client = mock.Mock()
        if completions_content is not None:
            client.chat.completions.create.return_value = _completions(completions_content)
        else:
            client.chat.completions.create.side_effect = AssertionError("日常聊天不该走 chat.completions")
        if responses_content is not None:
            client.responses.create.return_value = _responses(responses_content)
        else:
            client.responses.create.side_effect = AssertionError("这轮不该走 responses/web_search")
        return client

    def test_daily_chat_fast_path(self):
        """日常聊天 → Chat Completions（快路径，不挂搜索）"""
        client = self._client(completions_content='{"spoken": "想你了"}')
        with mock.patch("xiaoli.llm.get_client", return_value=client):
            out = chat.call_xiaoli([{"role": "user", "content": "今天想你了"}])
        self.assertIn("想你了", out)
        client.chat.completions.create.assert_called_once()

    def test_weather_triggers_search(self):
        """问天气 → Responses + web_search（模型可上网）"""
        client = self._client(responses_content='{"spoken": "明天会下雨"}')
        with mock.patch("xiaoli.llm.get_client", return_value=client):
            out = chat.call_xiaoli([{"role": "user", "content": "明天天气怎么样"}])
        self.assertIn("下雨", out)
        kwargs = client.responses.create.call_args.kwargs
        self.assertIn("tools", kwargs)  # 挂了 web_search 工具

    def test_search_gate_uses_last_user_msg(self):
        """只看最后一条用户消息（历史里提过天气 ≠ 这轮要搜）"""
        client = self._client(completions_content='{"spoken": "嗯嗯"}')
        msgs = [{"role": "system", "content": "人设"},
                {"role": "user", "content": "之前问过天气了"},
                {"role": "assistant", "content": "明天会下雨"},
                {"role": "user", "content": "那我们明天去哪？"}]
        with mock.patch("xiaoli.llm.get_client", return_value=client):
            chat.call_xiaoli(msgs)
        client.chat.completions.create.assert_called_once()

    def test_search_api_fail_falls_back(self):
        """搜索路径 API 挂 → 降级 Chat Completions，对话不断"""
        client = mock.Mock()
        client.responses.create.side_effect = Exception("网络炸了")
        client.chat.completions.create.return_value = _completions('{"spoken": "我查不到，但我们可以聊别的"}')
        with mock.patch("xiaoli.llm.get_client", return_value=client):
            out = chat.call_xiaoli([{"role": "user", "content": "今天有什么新闻"}])
        self.assertIn("聊别的", out)

    def test_empty_content_retries(self):
        """空内容 → 自动重试（flash 偶发返回空）"""
        client = mock.Mock()
        client.chat.completions.create.side_effect = [
            _completions(""), _completions('{"spoken": "好了"}')]
        with mock.patch("xiaoli.llm.get_client", return_value=client):
            out = chat.call_xiaoli([{"role": "user", "content": "嗨"}])
        self.assertIn("好了", out)
        self.assertEqual(client.chat.completions.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()


class TestStopStartRace(unittest.TestCase):
    """2026-08-23 修复：stop 后立即 start 不残留"假开"——
    之前 stop 不等旧线程收尾，start() 被 `if self.active` 跳过（旧线程 is_alive
    还是 True）→ 按钮显示开、实际没监听。现在 stop 内 join 等收尾。"""

    def _cm(self):
        from xiaoli import call_mode
        # _loop 里 read 一次就抛 → 线程快进快出；全链路 mock 不碰真设备/模型
        cm = call_mode.CallMode()
        return cm

    def test_stop_joins_old_thread_before_start(self):
        cm = self._cm()
        with mock.patch("sounddevice.InputStream") as m_isdn, \
             mock.patch("xiaoli.vad.get", return_value=None), \
             mock.patch("xiaoli.vad.ready", return_value=False), \
             mock.patch("xiaoli.call_mode.aec.get", return_value=None):
            m_isdn.return_value.read.side_effect = [Exception("设备断开")]
            cm.start()
            t1 = cm._thread
            for _ in range(200):
                if cm.active:
                    break
                time.sleep(0.005)
            self.assertTrue(cm.active, "第一个线程应活着")
            cm.stop()   # 修复点：内部 join 等旧线程退出
            self.assertFalse(cm.active, "stop 后应真正关掉")
            cm.start()  # 修复前：active 还 True → 跳过 → 假开
            for _ in range(200):
                if cm.active:
                    break
                time.sleep(0.005)
            self.assertTrue(cm.active, "重新 start 应真正在监听（新线程）")
            t2 = cm._thread
            self.assertIsNot(t1, t2, "应是新线程")
            cm.stop()
            self.assertFalse(cm.active)

    def test_stop_without_start_safe(self):
        """从没 start 过 → stop 不炸"""
        cm = self._cm()
        with mock.patch("sounddevice.InputStream"):
            cm.stop()
            self.assertFalse(cm.active)


class TestNoInterruptDuringPlayback(unittest.TestCase):
    """2026-08-23 用户实测拍板：不加打断设计。
    她说话期间（is_playing）麦克风只读不掐——数据丢弃，绝不 stop_playing，
    也绝不把读到的内容送去识别（她自己的回声/声音不能变成用户的话）。"""

    def test_playing_loop_discards_without_interrupt(self):
        from xiaoli import call_mode
        cm = call_mode.CallMode()
        play_seq = iter([True, True, False])

        def fake_playing():
            return next(play_seq, False)

        vv_fake = mock.Mock()
        vv_fake.feed = lambda d: False
        vv_fake.last_voice = False
        with mock.patch("sounddevice.InputStream") as m_isdn, \
             mock.patch("xiaoli.vad.get", return_value=vv_fake), \
             mock.patch("xiaoli.call_mode.voice.is_playing", side_effect=fake_playing) as m_play, \
             mock.patch("xiaoli.call_mode.voice.stop_playing") as m_stop:
            # 第一次 read：她正在说 → 数据应被丢弃；之后正常路径 read 连挂 5 次 → 线程退出
            m_isdn.return_value.read.side_effect = [
                np.zeros(3200, np.int16),
                Exception("设备断开"), Exception("设备断开"), Exception("设备断开"),
                Exception("设备断开"), Exception("设备断开")]
            got = []
            cm.on_text = got.append  # 丢弃路径绝不能把读到的内容送去识别
            cm.start()
            for _ in range(300):
                if not cm.active:
                    break
                time.sleep(0.01)
            self.assertFalse(cm.active, "线程应正常退出")
            m_stop.assert_not_called()   # 她说话时绝不掐她
            self.assertEqual(got, [], "她说话期间读到的数据只丢不识别")
            self.assertGreaterEqual(m_play.call_count, 2, "内层 while 至少重查过一次")
            cm.stop()


