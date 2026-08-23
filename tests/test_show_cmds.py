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


if __name__ == "__main__":
    unittest.main()
