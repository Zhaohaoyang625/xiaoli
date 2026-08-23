# ============================================
# 网页"最近对话"端点 /recent 单测（2026-08-23）
# 刷新网页不丢聊天记录：后端日记 → 最近 N 条 → 气泡渲染
# ============================================

import unittest
from unittest import mock

from xiaoli import chat as chat_mod


class TestRecentEndpoint(unittest.TestCase):
    def _handler(self, path="/recent?token=secret123"):
        h = chat_mod.WebBridge.__new__(chat_mod.WebBridge)
        h.path = path
        return h

    def setUp(self):
        self._saved = chat_mod.diary
        chat_mod.diary = {"messages": [], "summary": "", "daily": {}}

    def tearDown(self):
        chat_mod.diary = self._saved

    def _call(self, h):
        resp = {}
        with mock.patch.object(chat_mod, "_bridge_token", "secret123"), \
             mock.patch.object(chat_mod.WebBridge, "_json",
                               side_effect=lambda d, c=200: resp.update({"body": d, "code": c})):
            chat_mod.WebBridge.do_GET(h)
        return resp

    def test_no_token_forbidden(self):
        resp = self._call(self._handler("/recent"))  # 无 token
        self.assertEqual(resp["code"], 403)

    def test_empty_diary_ok(self):
        resp = self._call(self._handler())
        self.assertEqual(resp["code"], 200)
        self.assertTrue(resp["body"]["ok"])
        self.assertEqual(resp["body"]["messages"], [])

    def test_returns_last_n(self):
        chat_mod.diary["messages"] = [
            {"role": "user", "content": f"第{i}句", "time": f"08:0{i}"}
            for i in range(1, 6)]
        resp = self._call(self._handler("/recent?n=3&token=secret123"))
        self.assertEqual([m["content"] for m in resp["body"]["messages"]],
                         ["第3句", "第4句", "第5句"])

    def test_filters_non_chat_roles(self):
        """event（早安/主动）等非对话消息不显示"""
        chat_mod.diary["messages"] = [
            {"role": "event", "content": "现在是早安时间", "time": "08:00"},
            {"role": "user", "content": "早", "time": "08:01"},
            {"role": "assistant", "content": "早安呀～", "time": "08:01"},
            {"role": "event", "content": "主动问候", "time": "08:02"},
        ]
        resp = self._call(self._handler())
        self.assertEqual([m["role"] for m in resp["body"]["messages"]],
                         ["user", "assistant"])

    def test_skips_blank_and_non_str_content(self):
        chat_mod.diary["messages"] = [
            {"role": "user", "content": "  ", "time": "08:00"},
            {"role": "assistant", "content": 123, "time": "08:00"},  # 异常数据不崩
            {"role": "user", "content": "真的话", "time": "08:01"},
        ]
        resp = self._call(self._handler())
        self.assertEqual([m["content"] for m in resp["body"]["messages"]], ["真的话"])

    def test_content_truncated_at_300(self):
        chat_mod.diary["messages"] = [
            {"role": "assistant", "content": "长" * 500, "time": "08:00"}]
        resp = self._call(self._handler())
        self.assertEqual(len(resp["body"]["messages"][0]["content"]), 300)

    def test_n_capped_at_50(self):
        chat_mod.diary["messages"] = [
            {"role": "user", "content": f"m{i}", "time": "t"} for i in range(60)]
        resp = self._call(self._handler("/recent?n=999&token=secret123"))
        self.assertEqual(len(resp["body"]["messages"]), 50)

    def test_bad_n_defaults_20(self):
        chat_mod.diary["messages"] = [
            {"role": "user", "content": f"m{i}", "time": "t"} for i in range(30)]
        resp = self._call(self._handler("/recent?n=abc&token=secret123"))
        self.assertEqual(len(resp["body"]["messages"]), 20)


if __name__ == "__main__":
    unittest.main()
