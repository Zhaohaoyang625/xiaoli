# ============================================
# 终端查看命令「记忆」「心情」单测（2026-08-23）
# 小白也能随时检查：她记得什么 / 她现在什么心情
# ============================================

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from xiaoli import chat as chat_mod


class TestShowFacts(unittest.TestCase):
    def _run(self, facts):
        out = io.StringIO()
        with mock.patch.object(chat_mod, "facts", facts), redirect_stdout(out):
            chat_mod._show_facts()
        return out.getvalue()

    def test_empty_facts_friendly_hint(self):
        out = self._run([])
        self.assertIn("档案还是空的", out)

    def test_prints_sorted_by_importance(self):
        facts = [
            {"content": "低重要度", "importance": 3, "category": ""},
            {"content": "高重要度", "importance": 9, "category": "他特意让我记住的"},
        ]
        out = self._run(facts)
        hi = out.index("高重要度")
        lo = out.index("低重要度")
        self.assertLess(hi, lo)  # 重要度高的排前面
        self.assertIn("他特意让我记住的", out)
        self.assertIn("★9", out)

    def test_takes_top_15(self):
        facts = [{"content": f"事{i}", "importance": i, "category": ""}
                 for i in range(30)]
        out = self._run(facts)
        self.assertNotIn("事0", out)  # 重要度最低的被裁掉
        self.assertIn("事29", out)
        # 输出条数 = 15 行 ★（内容行）
        self.assertEqual(out.count("  ★"), 15)


class TestShowHeart(unittest.TestCase):
    def _run(self, heart):
        out = io.StringIO()
        with mock.patch.object(chat_mod, "her_heart", heart), redirect_stdout(out):
            chat_mod._show_heart()
        return out.getvalue()

    def test_shows_mood_and_affection(self):
        out = self._run({"mood": {"primary": "happy", "intensity": 70, "causes": []},
                         "affection": 88})
        self.assertIn("开心", out)
        self.assertIn("70", out)
        self.assertIn("88", out)

    def test_shows_causes(self):
        out = self._run({"mood": {"primary": "jealous", "intensity": 60,
                                  "causes": ["他提了别的女生"]},
                         "affection": 55})
        self.assertIn("吃醋", out)
        self.assertIn("他提了别的女生", out)

    def test_unknown_mood_passthrough(self):
        out = self._run({"mood": {"primary": "bizarre_mood", "intensity": 1, "causes": []},
                         "affection": 60})
        self.assertIn("bizarre_mood", out)

    def test_missing_fields_default(self):
        out = self._run({})
        self.assertIn("平静", out)  # neutral 默认
        self.assertIn("60", out)    # affection 默认


class TestRememberText(unittest.TestCase):
    """终端「记住XXX」提取规则：不误记疑问句/太短的"""

    def test_extracts_after_prefix(self):
        self.assertEqual(chat_mod._remember_text("记住我喜欢喝奶茶"),
                         (True, "我喜欢喝奶茶"))
        self.assertEqual(chat_mod._remember_text("记下我的生日是5月20号"),
                         (True, "我的生日是5月20号"))
        self.assertEqual(chat_mod._remember_text("帮我记着我最怕打针"),
                         (True, "我最怕打针"))

    def test_question_not_remembered(self):
        """"记住了吗/记住没"这类不是要记的内容"""
        self.assertEqual(chat_mod._remember_text("记住了吗"), (False, ""))
        self.assertEqual(chat_mod._remember_text("记住没"), (False, ""))

    def test_too_short_skipped(self):
        self.assertEqual(chat_mod._remember_text("记住"), (False, ""))
        self.assertEqual(chat_mod._remember_text("记住啊"), (False, ""))  # 短+语气词

    def test_plain_chat_not_remembered(self):
        """不带前缀的正常聊天不触发"""
        self.assertEqual(chat_mod._remember_text("你今天记住要买奶茶了吗"), (False, ""))


class TestShowBrief(unittest.TestCase):
    def _run(self, brief_text):
        out = io.StringIO()
        with mock.patch.object(chat_mod.world_brief, "load_brief_injection",
                               return_value=brief_text), redirect_stdout(out):
            chat_mod._show_brief()
        return out.getvalue()

    def test_prints_brief_text(self):
        out = self._run("今天的热搜：台湾各地高温。")
        self.assertIn("台湾各地高温", out)

    def test_empty_brief_friendly_hint(self):
        out = self._run("")
        self.assertIn("还没刷到世界新闻", out)


if __name__ == "__main__":
    unittest.main()
