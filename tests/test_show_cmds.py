# ============================================
# 终端查看命令「记忆」「心情」单测（2026-08-23）
# 小白也能随时检查：她记得什么 / 她现在什么心情
# ============================================

import io
import os
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


class TestHandleRemember(unittest.TestCase):
    """「记住XXX」命令接线：处理了就吃掉输入（不发大脑），不处理的走正常聊天"""

    def _call(self, text):
        """返回 (handled, 输出, facts_列表, merge_mock, save_mock)。
        注意：facts_列表是真实引用、mocks 是对象——with 退出后仍可断言（patch 恢复的是模块属性，不是这些对象）"""
        out = io.StringIO()
        facts_holder = []
        with mock.patch.object(chat_mod, "facts", facts_holder), \
             mock.patch.object(chat_mod.memory_mod, "merge_fact") as merge_mock, \
             mock.patch.object(chat_mod.memory_mod, "save_facts") as save_mock, \
             redirect_stdout(out):
            merge_mock.side_effect = lambda facts, content, **kw: facts.append(
                {"content": content, **kw})
            handled = chat_mod.handle_remember(text)
        return handled, out.getvalue(), facts_holder, merge_mock, save_mock

    def test_plain_chat_not_handled(self):
        handled, _, _, _, _ = self._call("今天天气不错")
        self.assertFalse(handled)

    def test_valid_remember_handled_and_saved(self):
        handled, out, facts_holder, _, save_mock = self._call("记住我喜欢喝奶茶")
        self.assertTrue(handled)
        self.assertIn("我喜欢喝奶茶", out)
        self.assertEqual(facts_holder[0]["importance"], 9)
        self.assertEqual(facts_holder[0]["category"], "他特意让我记住的")
        save_mock.assert_called_once()

    def test_question_handled_but_not_saved(self):
        """「记住了吗」被吃掉（不发给大脑），但也不误记"""
        handled, out, _, merge_mock, _ = self._call("记住了吗")
        self.assertTrue(handled)
        self.assertIn("没听清要记住啥", out)
        merge_mock.assert_not_called()


class TestShowToday(unittest.TestCase):
    def _run(self, special_text=None):
        out = io.StringIO()
        with mock.patch.object(chat_mod.special, "check_today",
                               return_value=special_text), redirect_stdout(out):
            chat_mod._show_today()
        return out.getvalue()

    def test_plain_day_friendly(self):
        out = self._run(None)
        self.assertIn("星期", out)
        self.assertIn("平平常常的一天", out)

    def test_special_day_shown(self):
        out = self._run("今天是七夕（农历）")
        self.assertIn("七夕", out)

    def test_birthday_priority(self):
        out = self._run("今天是他生日（5月20日）")
        self.assertIn("他生日", out)


class TestShowBrief(unittest.TestCase):
    def _run(self, brief_text, refresh_ok=True):
        out = io.StringIO()
        refresh = mock.Mock()
        if not refresh_ok:
            refresh.side_effect = RuntimeError("网络炸了")
        with mock.patch.object(chat_mod.world_brief, "ensure_fresh", refresh), \
             mock.patch.object(chat_mod.world_brief, "load_brief_injection",
                               return_value=brief_text), redirect_stdout(out):
            chat_mod._show_brief()
        return out.getvalue(), refresh

    def test_prints_brief_text(self):
        out, refresh = self._run("今天的热搜：台湾各地高温。")
        self.assertIn("台湾各地高温", out)
        refresh.assert_called_once()  # 过期自动刷新

    def test_empty_brief_friendly_hint(self):
        out, _ = self._run("")
        self.assertIn("还没刷到世界新闻", out)

    def test_refresh_failure_reads_old(self):
        """联网失败不崩，读旧的（哪怕旧的也空 → 提示）"""
        out, _ = self._run("", refresh_ok=False)
        self.assertIn("还没刷到世界新闻", out)


if __name__ == "__main__":
    unittest.main()


class TestFaceStateTokenSync(unittest.TestCase):
    """2026-08-23 修复：启动即写 face_state.js（会话 token 同步）。
    之前启动不写，网页加载到上次进程的旧 token → 403"连不上"（聊几句后才刷新）"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_file = chat_mod.FACE_STATE_FILE
        chat_mod.FACE_STATE_FILE = os.path.join(self._tmp.name, "face_state.js")
        # 2026-08-29 实测教训：update_face 写两份（根 data/ + web/data/），
        # 只重定向第一份 → 测试把真实 web/data/face_state.js 写成 abc123 →
        # 网页 token 失配全 403（"点了没反应"）。两份都要进临时目录！
        self._saved_web_file = chat_mod.WEB_FACE_STATE_FILE
        chat_mod.WEB_FACE_STATE_FILE = os.path.join(self._tmp.name, "web_face_state.js")
        self._saved_heart = chat_mod.her_heart
        chat_mod.her_heart = {"mood": {"primary": "happy", "intensity": 60},
                              "affection": 80}

    def tearDown(self):
        chat_mod.FACE_STATE_FILE = self._saved_file
        chat_mod.WEB_FACE_STATE_FILE = self._saved_web_file
        chat_mod.her_heart = self._saved_heart
        self._tmp.cleanup()

    def test_write_state_includes_new_token(self):
        chat_mod._bridge_token = "abc123"
        chat_mod.update_face("")
        with open(chat_mod.FACE_STATE_FILE, encoding="utf-8") as f:
            raw = f.read()
        self.assertIn("bridge_token", raw)
        self.assertIn("abc123", raw)

    def test_heart_none_skips_write(self):
        chat_mod.her_heart = None
        chat_mod.update_face("")
        self.assertFalse(os.path.exists(chat_mod.FACE_STATE_FILE))
