# ============================================
# 入站梗检测回归测试（2026-08-22 反向接梗）
# 用户抛新梗 → detect_slang 认出可能的新梗 → search_slang 联网查证
# → enrich_with_slang 把情报注入 messages 末尾 → 她接得上
# 原则：任何一环失败都静默降级，绝不打扰对话
# ============================================

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xiaoli import chat


def _mock_completions(content):
    """构造 chat.completions.create 的 mock 返回值"""
    resp = mock.Mock()
    resp.choices = [mock.Mock()]
    resp.choices[0].message.content = content
    return resp


class TestDetectSlang(unittest.TestCase):
    """detect_slang：判断用户话里有没有可能的新梗"""

    @mock.patch("xiaoli.llm.get_client")
    def test_finds_slang(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_completions('{"queries": ["你胆子真是肥嘟嘟的"]}')
        self.assertEqual(chat.detect_slang("你胆子真是肥嘟嘟的"), ["你胆子真是肥嘟嘟的"])

    @mock.patch("xiaoli.llm.get_client")
    def test_no_slang_returns_empty(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_completions('{"queries": []}')
        self.assertEqual(chat.detect_slang("今天天气不错"), [])

    @mock.patch("xiaoli.llm.get_client")
    def test_api_error_returns_empty(self, MockOpenAI):
        """检测 API 挂 → 返回 []，不打扰对话"""
        MockOpenAI.return_value.chat.completions.create.side_effect = Exception("网络炸了")
        self.assertEqual(chat.detect_slang("随便说点啥"), [])

    @mock.patch("xiaoli.llm.get_client")
    def test_bad_json_returns_empty(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_completions("这不是JSON")
        self.assertEqual(chat.detect_slang("试试"), [])


class TestSearchSlang(unittest.TestCase):
    """search_slang：联网查证短语"""

    @mock.patch("xiaoli.llm.get_client")
    def test_returns_summary(self, MockOpenAI):
        r = mock.Mock()
        r.output_text = "「你胆子真是肥嘟嘟的」是2026年8月的梗，来自外送小哥嗆店家抢单的视频，指人胆子大。"
        MockOpenAI.return_value.responses.create.return_value = r
        out = chat.search_slang("你胆子真是肥嘟嘟的")
        self.assertIn("肥嘟嘟", out)
        self.assertLessEqual(len(out), 300)

    @mock.patch("xiaoli.llm.get_client")
    def test_failure_returns_empty(self, MockOpenAI):
        MockOpenAI.return_value.responses.create.side_effect = Exception("API挂了")
        self.assertEqual(chat.search_slang("随便查查"), "")


class TestEnrichWithSlang(unittest.TestCase):
    """enrich_with_slang：情报注入 messages 末尾"""

    def _msgs(self, user_text="你胆子真是肥嘟嘟的"):
        return [{"role": "system", "content": "人设"},
                {"role": "user", "content": user_text}]

    @mock.patch("xiaoli.chat.search_slang", return_value="「你胆子真是肥嘟嘟的」是2026年8月新梗，指人胆子大")
    @mock.patch("xiaoli.chat.detect_slang", return_value=["你胆子真是肥嘟嘟的"])
    def test_injects_info_at_end(self, m_detect, m_search):
        msgs = self._msgs()
        out = chat.enrich_with_slang(msgs)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[-1]["role"], "system")
        self.assertIn("2026年8月新梗", out[-1]["content"])
        # 原消息不被篡改（缓存前缀位置不变——情报只在末尾追加）
        self.assertEqual(out[0], msgs[0])
        self.assertEqual(out[1], msgs[1])

    @mock.patch("xiaoli.chat.search_slang")
    @mock.patch("xiaoli.chat.detect_slang", return_value=[])
    def test_no_slang_no_inject(self, m_detect, m_search):
        msgs = self._msgs("今天好累")
        out = chat.enrich_with_slang(msgs)
        self.assertEqual(out, msgs)
        m_search.assert_not_called()

    @mock.patch("xiaoli.chat.search_slang")
    @mock.patch("xiaoli.chat.detect_slang", return_value=["某某梗"])
    def test_search_fail_no_inject(self, m_detect, m_search):
        m_search.return_value = ""
        msgs = self._msgs()
        self.assertEqual(chat.enrich_with_slang(msgs), msgs)

    @mock.patch("xiaoli.chat.search_slang")
    @mock.patch("xiaoli.chat.detect_slang", return_value=["某某梗"])
    def test_last_msg_not_user_skipped(self, m_detect, m_search):
        """最后一条不是 user（如 keep_talking 注入）→ 不预检"""
        msgs = [{"role": "system", "content": "人设"},
                {"role": "user", "content": "嗯"},
                {"role": "assistant", "content": "宝贝"}]
        out = chat.enrich_with_slang(msgs)
        self.assertEqual(out, msgs)
        m_detect.assert_not_called()

    @mock.patch("xiaoli.chat.search_slang")
    @mock.patch("xiaoli.chat.detect_slang", side_effect=Exception("预检挂了"))
    def test_detect_exception_no_inject(self, m_detect, m_search):
        """预检本身异常 → 原样返回（对话照常）"""
        msgs = self._msgs()
        self.assertEqual(chat.enrich_with_slang(msgs), msgs)
        m_search.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
