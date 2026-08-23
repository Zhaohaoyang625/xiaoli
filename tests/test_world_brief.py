# ============================================
# 世界简报回归测试（2026-08-23 世界认知系统·完整版）
# world_brief：按天归档的大事+热梗简报，升级自 slang_cache（热梗缓存）
# 设计纪律：24h 限频省钱；失败静默；>7 天过气不注入；跨天去重；注入在历史后用户前
# ============================================

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xiaoli import world_brief


def _brief(data, date_str="2026-08-23", updated="2026-08-23 09:00"):
    return {"date": date_str, "updated": updated,
            "events": data.get("events", []), "slang": data.get("slang", [])}


class WorldBriefTestBase(unittest.TestCase):
    """公共环境：临时简报目录 + 还原"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_dir = world_brief.BRIEF_DIR
        world_brief.BRIEF_DIR = os.path.join(self._tmp, "world_briefs")

    def tearDown(self):
        world_brief.BRIEF_DIR = self._orig_dir

    def _write(self, date_str, data, updated=None):
        os.makedirs(world_brief.BRIEF_DIR, exist_ok=True)
        with open(os.path.join(world_brief.BRIEF_DIR, f"{date_str}.json"),
                  "w", encoding="utf-8") as f:
            json.dump(_brief(data, date_str, updated or f"{date_str} 09:00"),
                      f, ensure_ascii=False, indent=2)


class TestRefresh(WorldBriefTestBase):
    """refresh_world_brief：24h 限频、过期重刷、失败静默"""

    @mock.patch("xiaoli.llm.get_client")
    def test_fresh_skips_refresh(self, MockOpenAI):
        """24 小时内刷过 → 不再调 API（省钱）"""
        self._write("2026-08-23", {"events": [], "slang": []},
                    updated=datetime.now().strftime("%Y-%m-%d %H:%M"))
        world_brief.refresh_world_brief()
        MockOpenAI.assert_not_called()

    @mock.patch("xiaoli.llm.get_client")
    def test_stale_refreshes_and_writes_new_day(self, MockOpenAI):
        """过期（>24h）→ 联网重刷，落盘"今天"的文件"""
        stale = (datetime.now() - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M")
        self._write("2026-08-22", {"events": [{"title": "旧事"}], "slang": []}, stale)
        r = mock.Mock()
        r.output_text = json.dumps({
            "events": [{"title": "台风", "desc": "逼近台湾"}],
            "slang": [{"phrase": "胆子肥嘟嘟", "meaning": "8月新梗"}],
        }, ensure_ascii=False)
        MockOpenAI.return_value.responses.create.return_value = r
        world_brief.refresh_world_brief()
        today = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(world_brief.BRIEF_DIR, f"{today}.json")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("胆子肥嘟嘟", json.dumps(data, ensure_ascii=False))

    @mock.patch("xiaoli.llm.get_client")
    def test_api_failure_keeps_old(self, MockOpenAI):
        """联网失败 → 静默返回现有注入文本（不炸、不清空）"""
        self._write("2026-08-22", {"events": [{"title": "旧事", "desc": "还是旧的"}],
                                   "slang": []})
        MockOpenAI.return_value.responses.create.side_effect = Exception("网络炸了")
        out = world_brief.refresh_world_brief()
        self.assertIn("旧事", out)

    @mock.patch("xiaoli.llm.get_client")
    def test_no_brief_failure_returns_empty(self, MockOpenAI):
        """没简报 + 联网失败 → 空串（主流程不受影响）"""
        MockOpenAI.return_value.responses.create.side_effect = Exception("网络炸了")
        self.assertEqual(world_brief.refresh_world_brief(), "")

    @mock.patch("xiaoli.llm.get_client")
    def test_bad_json_returns_none_and_keeps_old(self, MockOpenAI):
        """LLM 输出坏 JSON → 不落盘，旧简报保留"""
        self._write("2026-08-22", {"events": [{"title": "旧事"}], "slang": []})
        r = mock.Mock()
        r.output_text = "不是JSON"
        MockOpenAI.return_value.responses.create.return_value = r
        out = world_brief.refresh_world_brief()
        self.assertIn("旧事", out)


class TestInjection(WorldBriefTestBase):
    """load_brief_injection：组装策略（最新完整 + 前几天弱提及 + 过气过滤 + 去重）"""

    def test_latest_full_older_weak(self):
        """最新一天完整注入；前几天只列名字弱提及"""
        self._write("2026-08-22", {"events": [{"title": "昨天的大事", "desc": "详情"}],
                                   "slang": [{"phrase": "牛来", "meaning": "行情梗"}]})
        self._write("2026-08-23", {"events": [{"title": "今天的大事", "desc": "刚发生"}],
                                   "slang": [{"phrase": "胆子肥嘟嘟", "meaning": "新梗"}]})
        out = world_brief.load_brief_injection(datetime(2026, 8, 23, 12, 0))
        self.assertIn("今天的大事（刚发生）", out)   # 最新天完整
        self.assertIn("胆子肥嘟嘟=新梗", out)
        self.assertIn("前几天她还看到过", out)      # 弱提及
        self.assertIn("昨天的大事", out)
        self.assertNotIn("详情", out)               # 旧天不注入 desc（只列名字）

    def test_overage_not_injected(self):
        """>7 天的简报不注入（过气）"""
        self._write("2026-08-10", {"events": [{"title": "十天前的事"}], "slang": []})
        self._write("2026-08-20", {"events": [{"title": "三天前的事"}], "slang": []})
        out = world_brief.load_brief_injection(datetime(2026, 8, 23, 12, 0))
        self.assertNotIn("十天前的事", out)
        self.assertIn("三天前的事", out)

    def test_duplicate_across_days_skipped(self):
        """同梗跨天重复 → 只出现在最新天（去重，学 mem0 supersede）"""
        self._write("2026-08-22", {"events": [], "slang": [{"phrase": "牛来", "meaning": "旧解释"}]})
        self._write("2026-08-23", {"events": [], "slang": [{"phrase": "牛来", "meaning": "新解释"}]})
        out = world_brief.load_brief_injection(datetime(2026, 8, 23, 12, 0))
        self.assertIn("牛来=新解释", out)
        self.assertNotIn("牛来", out.replace("牛来=新解释", ""))

    def test_empty_dir(self):
        """没有简报 → 空串（不注入）"""
        self.assertEqual(world_brief.load_brief_injection(), "")

    def test_corrupt_brief_skipped(self):
        """损坏的简报文件 → 跳过，不炸"""
        self._write("2026-08-23", {"events": [{"title": "好的"}], "slang": []})
        with open(os.path.join(world_brief.BRIEF_DIR, "2026-08-22.json"),
                  "w", encoding="utf-8") as f:
            f.write("坏文件")
        out = world_brief.load_brief_injection(datetime(2026, 8, 23, 12, 0))
        self.assertIn("好的", out)

    def test_max_chars_cap(self):
        """注入总长度受 MAX_CHARS 限制"""
        self._write("2026-08-23", {"events": [{"title": "事" * 300, "desc": "长"}],
                                   "slang": [{"phrase": "梗" * 300, "meaning": "长"}]})
        out = world_brief.load_brief_injection(datetime(2026, 8, 23, 12, 0))
        self.assertLessEqual(len(out), world_brief.MAX_CHARS)


class TestEnsureFresh(WorldBriefTestBase):
    """ensure_fresh：对话前停机补刷三保险之二"""

    @mock.patch("xiaoli.llm.get_client")
    def test_fresh_no_refresh(self, MockOpenAI):
        """简报新鲜 → 不联网"""
        self._write("2026-08-23", {"events": [{"title": "新事", "desc": "x"}], "slang": []},
                    updated=datetime.now().strftime("%Y-%m-%d %H:%M"))
        out = world_brief.ensure_fresh()
        self.assertIn("新事", out)
        MockOpenAI.assert_not_called()

    @mock.patch("xiaoli.llm.get_client")
    def test_stale_refreshes_sync(self, MockOpenAI):
        """简报过期 → 同步补刷（对话前她一定已刷到）"""
        stale = (datetime.now() - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M")
        self._write("2026-08-22", {"events": [{"title": "旧事"}], "slang": []}, stale)
        r = mock.Mock()
        r.output_text = json.dumps({"events": [{"title": "补刷到的新事", "desc": "刚发生"}],
                                    "slang": []}, ensure_ascii=False)
        MockOpenAI.return_value.responses.create.return_value = r
        out = world_brief.ensure_fresh()
        self.assertIn("补刷到的新事", out)

    @mock.patch("xiaoli.llm.get_client")
    def test_no_brief_refreshes(self, MockOpenAI):
        """完全没有简报 → 补刷"""
        r = mock.Mock()
        r.output_text = json.dumps({"events": [{"title": "第一份", "desc": "简报"}],
                                    "slang": []}, ensure_ascii=False)
        MockOpenAI.return_value.responses.create.return_value = r
        out = world_brief.ensure_fresh()
        self.assertIn("第一份", out)


class TestWorkbenchInjection(unittest.TestCase):
    """build_workbench：世界简报注入在"历史后、用户前"（动态区）"""

    @mock.patch("xiaoli.context.world_brief.load_brief_injection",
                return_value="【她最近看到的】世界大事：台风（逼近台湾）。")
    def test_injects_after_history(self, m_load):
        from xiaoli import context
        diary = {"messages": [
            {"role": "user", "content": "你好", "time": "2026-08-22 10:00"},
            {"role": "assistant", "content": "嗨～"},
        ]}
        msgs = context.build_workbench("人设", diary, "最近怎么样")
        idx = [m.get("content", "") for m in msgs].index(
            "【她最近看到的】世界大事：台风（逼近台湾）。")
        self.assertEqual(msgs[idx]["role"], "system")
        self.assertLess(idx, len(msgs) - 1)
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertEqual(msgs[idx - 1]["role"], "assistant")

    @mock.patch("xiaoli.context.world_brief.load_brief_injection", return_value="")
    def test_empty_no_inject(self, m_load):
        from xiaoli import context
        msgs = context.build_workbench("人设", {"messages": []}, "你好")
        self.assertFalse(any("她最近看到的" in m.get("content", "") for m in msgs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
