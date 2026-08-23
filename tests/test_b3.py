# ============================================
# B.3 档案记忆 测试
# 测：本地提取 / 合并去重 / bigram检索 / 记忆老化 / 提示块格式
# ============================================

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import memory

TMP = tempfile.mkdtemp()


def setUpModule():
    memory.MEMORY_FILE = os.path.join(TMP, "facts.json")


def _clear():
    if os.path.exists(memory.MEMORY_FILE):
        os.remove(memory.MEMORY_FILE)


class TestExtractLocal(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_name(self):
        r = memory.extract_facts_local("我叫小明，你呢？")
        self.assertIn(("他叫小明", "身份"), r)

    def test_like(self):
        r = memory.extract_facts_local("我喜欢喝奶茶")
        self.assertIn(("他喜欢喝奶茶", "喜好"), r)

    def test_like_tail(self):
        r = memory.extract_facts_local("我喜欢喝奶茶，也喜欢喝咖啡")
        self.assertEqual(len(r), 1)  # 只抓第一段（到标点为止）
        self.assertEqual(r[0][0], "他喜欢喝奶茶")

    def test_live_and_work(self):
        r = memory.extract_facts_local("我住在杭州，我在阿里工作")
        self.assertIn(("他住在杭州", "生活"), r)
        self.assertIn(("他在阿里工作", "工作学习"), r)

    def test_no_match(self):
        self.assertEqual(memory.extract_facts_local("今天天气真好呀"), [])

    def test_dedup(self):
        r = memory.extract_facts_local("我喜欢猫，我喜欢猫")
        self.assertEqual(len(r), 1)


class TestMerge(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_add_new(self):
        facts = memory.load_facts()
        self.assertTrue(memory.merge_fact(facts, "他喜欢喝奶茶", importance=6))
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["importance"], 6)

    def test_merge_existing_takes_higher(self):
        facts = []
        memory.merge_fact(facts, "他喜欢喝奶茶", importance=5)
        memory.merge_fact(facts, "他喜欢喝奶茶", importance=8)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["importance"], 8)  # 取更高

    def test_importance_clamped(self):
        facts = []
        memory.merge_fact(facts, "x" * 3, importance=99)
        self.assertEqual(facts[0]["importance"], 10)
        memory.merge_fact(facts, "y" * 3, importance=-5)
        self.assertEqual(facts[-1]["importance"], 1)


class TestRecall(unittest.TestCase):
    def setUp(self):
        _clear()

    def _mk(self, content, importance=5, days_ago=0):
        created = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")
        return {"id": "x", "content": content, "category": "喜好",
                "importance": importance, "confidence": 0.8,
                "createdAt": created, "lastRecalled": created}

    def test_recall_related(self):
        facts = [self._mk("他喜欢喝奶茶", importance=7)]
        hits = memory.recall(facts, "我好久没喝奶茶了")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["content"], "他喜欢喝奶茶")

    def test_recall_unrelated(self):
        facts = [self._mk("他喜欢喝奶茶")]
        hits = memory.recall(facts, "今天工作好累啊")
        self.assertEqual(hits, [])

    def test_recall_sorted_by_importance(self):
        facts = [self._mk("他怕黑", importance=3), self._mk("他怕高", importance=9)]
        hits = memory.recall(facts, "他跟我说他怕黑也怕高，怎么办")
        self.assertEqual(hits[0]["importance"], 9)  # 重要的事排前面

    def test_recall_top5(self):
        facts = [self._mk(f"他喜欢水果{i}", importance=5) for i in range(10)]
        hits = memory.recall(facts, "水果 0 1 2 3 4 5 6 7 8 9")
        self.assertLessEqual(len(hits), 5)


class TestDecay(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_fresh_not_decayed(self):
        facts = [{"id": "a", "content": "他喜欢猫", "category": "喜好", "importance": 5,
                  "confidence": 0.8, "createdAt": "2026-08-20 10:00",
                  "lastRecalled": datetime.now().strftime("%Y-%m-%d %H:%M")}]
        kept, removed = memory.decay(facts)
        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, 0)
        self.assertEqual(kept[0]["importance"], 5)

    def test_7_days_no_step(self):
        """v2 遗忘是渐进的：7 天不降 importance（衰减体现在排序分里），且远没到 5 半衰期 → 保留"""
        old = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
        facts = [{"id": "a", "content": "他喜欢猫", "category": "喜好", "importance": 5,
                  "confidence": 0.8, "createdAt": old, "lastRecalled": old}]
        kept, removed = memory.decay(facts)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["importance"], 5)

    def test_forget_after_five_half_lives(self):
        """超过 5 个半衰期没被想起（新鲜度衰减到约 3%）→ 遗忘删除（v2 M2）"""
        # importance 5 → 半衰期 35 天 → 175 天过期；用 200 天测试
        old = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M")
        facts = [{"id": "a", "content": "他喜欢猫", "category": "喜好", "importance": 5,
                  "confidence": 0.8, "createdAt": old, "lastRecalled": old}]
        kept, removed = memory.decay(facts)
        self.assertEqual(kept, [])
        self.assertEqual(removed, 1)

    def test_high_emotion_forgets_slower(self):
        """v2 M5 躯体标记：高情绪记忆半衰期 ×1.5 → 200 天时普通记忆忘了、吃醋的记忆还在"""
        old = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M")
        facts = [
            {"id": "a", "content": "他喜欢猫", "category": "喜好", "importance": 5,
             "confidence": 0.8, "createdAt": old, "lastRecalled": old, "valence": "neutral"},
            {"id": "b", "content": "她吃过他和女同事的醋", "category": "关系", "importance": 5,
             "confidence": 0.8, "createdAt": old, "lastRecalled": old, "valence": "jealous"},
        ]
        kept, removed = memory.decay(facts)
        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, 1)
        self.assertEqual(kept[0]["id"], "b")  # 高情绪记忆留得更久

    def test_high_importance_survives_long(self):
        """重要的事记得久：importance 10 过了60天还在"""
        old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M")
        facts = [{"id": "a", "content": "他生日是3月14日", "category": "个人信息",
                  "importance": 10, "confidence": 0.9, "createdAt": old, "lastRecalled": old}]
        kept, removed = memory.decay(facts)
        self.assertEqual(len(kept), 1)
        self.assertGreater(kept[0]["importance"], 0)


class TestDescribe(unittest.TestCase):
    def test_format(self):
        s = memory.describe_facts([{"content": "他喜欢喝奶茶", "category": "喜好"}])
        self.assertIn("档案记忆", s)
        self.assertIn("他喜欢喝奶茶", s)

    def test_somatic_marker(self):
        """v2 M5：高情绪记忆带出当时的感受"""
        s = memory.describe_facts([{"content": "他提过女同事", "category": "关系", "valence": "jealous"}])
        self.assertIn("酸劲", s)

    def test_empty(self):
        self.assertEqual(memory.describe_facts([]), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
