# ============================================
# B.2 主动互动层 测试
# 测：提醒标签解析 / 到期触发只一次 / 错过浮现 / 节奏窗口每天一次 / <event>格式 / 调度器
# ============================================

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import proactive
from xiaoli import special

# 用临时文件隔离数据，不污染真实数据
TMP = tempfile.mkdtemp()


def setUpModule():
    proactive.REMINDERS_FILE = os.path.join(TMP, "reminders.json")
    proactive.PROACTIVE_FILE = os.path.join(TMP, "proactive.json")
    proactive.OUTING_FILE = os.path.join(TMP, "outings.json")
    # 调度器会检查特殊日子/问生日 → 也隔离（2026-08-20 加第4步后补的隔离）
    special.SPECIAL_FILE = os.path.join(TMP, "special.json")
    special.BIRTHDAY_FILE = os.path.join(TMP, "birthday.json")


def _clear():
    """每个测试前清空临时数据，避免测试间互相污染"""
    for p in (proactive.REMINDERS_FILE, proactive.PROACTIVE_FILE,
              proactive.OUTING_FILE,
              special.SPECIAL_FILE, special.BIRTHDAY_FILE):
        if os.path.exists(p):
            os.remove(p)


class TestReminderTags(unittest.TestCase):
    def test_parse_minutes(self):
        before = datetime.now()
        tags = proactive.parse_reminder_tags("记得喝水喔[reminder:5min]该喝水了[/reminder]")
        self.assertEqual(len(tags), 1)
        r = tags[0]
        self.assertEqual(r["content"], "该喝水了")
        trigger = datetime.strptime(r["trigger_at"], "%Y-%m-%d %H:%M:%S")
        delta = trigger - before
        self.assertTrue(timedelta(minutes=4) <= delta <= timedelta(minutes=6))

    def test_parse_hours_days(self):
        tags = proactive.parse_reminder_tags(
            "[reminder:2hour]两小时后[/reminder]和[reminder:1day]明天[/reminder]")
        self.assertEqual(len(tags), 2)
        t1 = datetime.strptime(tags[0]["trigger_at"], "%Y-%m-%d %H:%M:%S")
        t2 = datetime.strptime(tags[1]["trigger_at"], "%Y-%m-%d %H:%M:%S")
        self.assertGreater(t1, datetime.now())
        self.assertGreater(t2, t1)

    def test_parse_decimal_minutes(self):
        """实测教训 2026-08-20：LLM 想表达"30秒"会写 0.5min——
        原正则只认整数，整条标签被静默丢弃（用户求了提醒却没提醒）"""
        before = datetime.now()
        tags = proactive.parse_reminder_tags(
            "马上回来提醒你齁！[reminder:0.5min]宝贝该喝水啦～")
        self.assertEqual(len(tags), 1)
        trigger = datetime.strptime(tags[0]["trigger_at"], "%Y-%m-%d %H:%M:%S")
        delta = trigger - before
        self.assertTrue(timedelta(seconds=20) <= delta <= timedelta(seconds=40))

    def test_parse_seconds(self):
        """LLM 也可能直接写 30sec（秒单位）"""
        before = datetime.now()
        tags = proactive.parse_reminder_tags("[reminder:30sec]该吃药了[/reminder]")
        self.assertEqual(len(tags), 1)
        trigger = datetime.strptime(tags[0]["trigger_at"], "%Y-%m-%d %H:%M:%S")
        delta = trigger - before
        self.assertTrue(timedelta(seconds=25) <= delta <= timedelta(seconds=35))

    def test_no_tags(self):
        self.assertEqual(proactive.parse_reminder_tags("没有标签的话"), [])

    def test_plain_text_not_consumed(self):
        """无标签时原样，不吞内容"""
        tags = proactive.parse_reminder_tags("随便说点什么")
        self.assertEqual(tags, [])

    def test_unclosed_tag(self):
        """实测教训：flash 会省略闭合标签 → 内容到结尾"""
        tags = proactive.parse_reminder_tags("好呀～[reminder:5min]该去开会啦！")
        self.assertEqual(len(tags), 1)
        self.assertEqual(tags[0]["content"], "该去开会啦！")

    def test_unclosed_tag_followed_by_bracket(self):
        """无闭合标签但后面紧跟另一个标签 → 内容停在 [ 前"""
        tags = proactive.parse_reminder_tags(
            "[reminder:2hour]先干这个[reminder:1day]明天那个[/reminder]")
        self.assertEqual(len(tags), 2)
        self.assertEqual(tags[0]["content"], "先干这个")
        self.assertEqual(tags[1]["content"], "明天那个")


class TestDueReminders(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_due_once_then_marked(self):
        proactive.add_reminders([{
            "id": "t1", "content": "到点了",
            "trigger_at": (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"),
            "executed": False, "dismissed": False,
        }])
        # 第一次：到期 → 返回并标记 executed
        due1 = proactive.get_due_reminders()
        self.assertEqual(len(due1), 1)
        proactive.dismiss_reminders([d["id"] for d in due1])  # 真实流程：回调里会 dismiss
        # 第二次：已执行 → 不再返回（只触发一次！）
        due2 = proactive.get_due_reminders()
        self.assertEqual(len(due2), 0)
        # 已 dismiss → 也不会计入 missed
        self.assertEqual(proactive.get_missed_reminders(), [])

    def test_future_not_due(self):
        proactive.add_reminders([{
            "id": "t2", "content": "还没到",
            "trigger_at": (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
            "executed": False, "dismissed": False,
        }])
        self.assertEqual(proactive.get_due_reminders(), [])

    def test_missed_and_dismiss(self):
        """程序关闭期间到点的提醒：启动时浮现一次，然后 dismiss 不再出现"""
        proactive.add_reminders([{
            "id": "t3", "content": "关机期间到的",
            "trigger_at": "2020-01-01 00:00",
            "executed": True, "dismissed": False,
        }])
        missed = proactive.get_missed_reminders()
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]["content"], "关机期间到的")
        proactive.dismiss_reminders([m["id"] for m in missed])
        self.assertEqual(proactive.get_missed_reminders(), [])


class TestWindows(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_window_morning(self):
        key, name = proactive.get_window_now(datetime(2026, 8, 20, 8, 0))
        self.assertEqual((key, name), ("morning", "早安"))

    def test_window_night(self):
        key, name = proactive.get_window_now(datetime(2026, 8, 20, 22, 30))
        self.assertEqual((key, name), ("night", "睡前陪伴"))

    def test_outside_window(self):
        key, name = proactive.get_window_now(datetime(2026, 8, 20, 2, 0))
        self.assertEqual((key, name), (None, None))

    def test_once_per_day(self):
        key = "morning"
        day1 = datetime(2026, 8, 20, 8, 0)
        day2 = datetime(2026, 8, 21, 8, 0)
        self.assertTrue(proactive.should_fire_window(key, day1))
        proactive.mark_window_fired(key, day1)
        self.assertFalse(proactive.should_fire_window(key, day1))  # 今天不再触发
        self.assertTrue(proactive.should_fire_window(key, day2))   # 明天可以再触发

    def test_build_event_format(self):
        msg = proactive.build_event_message("节奏", "现在是早安时间，你主动开口")
        self.assertTrue(msg.startswith("<event>"))
        self.assertTrue(msg.endswith("</event>"))
        self.assertIn("这不是他说的话", msg)


class TestScheduler(unittest.TestCase):
    def setUp(self):
        _clear()
        # 标记"刚问过生日"：本组测试专注提醒/节奏，不测"问生日"（那是 test_special 的职责）
        special.mark_asked_birthday()
        # 重置主动配额（真人感研究落地：每天≤2次，防测试间残留）
        proactive._budget_date = None
        proactive._budget_used = 0

    def test_tick_fires_reminder(self):
        # 用固定时间（凌晨2点，不在任何节奏窗口）→ 只该触发提醒
        # （2026-08-20 加固：原来用真实时钟，晚上跑会额外触发"下班问候"窗口）
        fake_now = datetime(2026, 8, 20, 2, 0)
        proactive.add_reminders([{
            "id": "t4", "content": "到期提醒",
            "trigger_at": (fake_now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"),
            "executed": False, "dismissed": False,
        }])
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        sched._tick(fake_now)
        self.assertEqual(fired, [("提醒", "到期提醒")])

    def test_tick_fires_window_once(self):
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        fake_now = datetime(2026, 8, 20, 8, 5)
        sched._tick(fake_now)
        sched._tick(fake_now)  # 第二次 tick 不该再触发
        self.assertEqual(fired, [("节奏", "早安")])


class TestIdleChat(unittest.TestCase):
    """主动找话题：你一阵子没说话，她得到一次"开口机会"，由她自己判断说或安静"""

    def setUp(self):
        # 重置内存计时器 + 开关（模块级变量），隔离测试
        proactive._last_activity = None
        proactive._last_idle_chat = None
        proactive._proactive_enabled = True
        proactive._recording = False
        # 重置主动配额（真人感研究落地：每天≤2次，防测试间残留）
        proactive._budget_date = None
        proactive._budget_used = 0

    def test_no_activity_never_speaks(self):
        """还没互动过 → 不贸然开口"""
        self.assertFalse(proactive.should_idle_chat(datetime(2026, 8, 20, 20, 0)))

    def test_recent_activity_no_speak(self):
        """刚说过话（3分钟内）→ 不开口，等你先开口（2026-08-20 从90秒放宽到180秒）"""
        t0 = datetime(2026, 8, 20, 20, 0, 0)
        proactive.mark_activity(t0)
        self.assertFalse(proactive.should_idle_chat(t0 + timedelta(seconds=179)))

    def test_idle_long_enough_speaks(self):
        """3分钟没说话 → 给她一次开口机会（她说还是安静由 LLM 判断）"""
        t0 = datetime(2026, 8, 20, 20, 0, 0)
        proactive.mark_activity(t0)
        self.assertTrue(proactive.should_idle_chat(t0 + timedelta(seconds=181)))

    def test_gap_between_idle_chats(self):
        """刚主动找过话题（15分钟内）→ 不再开口（防连珠炮）"""
        t0 = datetime(2026, 8, 20, 20, 0, 0)
        proactive.mark_activity(t0 - timedelta(hours=1))  # 1小时前互动过（早就过了3分钟线）
        proactive.mark_idle_chat(t0)  # 刚给过机会
        self.assertFalse(proactive.should_idle_chat(t0 + timedelta(seconds=120)))
        # 15分钟后她可以再次找话题
        self.assertTrue(proactive.should_idle_chat(t0 + timedelta(seconds=901)))

    def test_scheduler_fires_idle_chat(self):
        """调度器链路：空闲到点 → 触发"闲聊"事件，且只触发一次"""
        t0 = datetime(2026, 8, 20, 20, 0, 0)
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        proactive.mark_activity(t0)  # 20:00 互动过
        sched._tick(t0 + timedelta(seconds=181))  # 20:03:01 空闲到点
        chats = [e for e in fired if e[0] == "闲聊"]
        self.assertEqual(len(chats), 1)
        sched._tick(t0 + timedelta(seconds=200))  # 刚给过机会（距上次19秒<15分钟）→ 不再触发
        chats = [e for e in fired if e[0] == "闲聊"]
        self.assertEqual(len(chats), 1)

    def test_proactive_off_no_idle_chat(self):
        """💬 开关关闭 → 她只回复不主动（节奏/闲聊/特殊日全都不触发）"""
        t0 = datetime(2026, 8, 20, 20, 0, 0)
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        proactive.mark_activity(t0 - timedelta(hours=1))
        proactive.set_proactive_enabled(False)
        sched._tick(t0 + timedelta(seconds=181))
        sched._tick(t0 + timedelta(seconds=300))  # 20:05 在节奏窗口"睡前陪伴"内，也不该触发
        self.assertEqual(fired, [])

    def test_reminder_fires_even_when_proactive_off(self):
        """提醒豁免：开关关了也必须履行答应的事（她答应过 = 承诺）"""
        fake_now = datetime(2026, 8, 20, 2, 0)
        proactive.add_reminders([{
            "id": "t9", "content": "该吃药了",
            "trigger_at": (fake_now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"),
            "executed": False, "dismissed": False,
        }])
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        proactive.set_proactive_enabled(False)
        sched._tick(fake_now)
        self.assertEqual(fired, [("提醒", "该吃药了")])

    def test_recording_blocks_proactive(self):
        """你正在录音（说话）→ 她安静听，不抢话"""
        t0 = datetime(2026, 8, 20, 20, 0, 0)
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        proactive.mark_activity(t0 - timedelta(hours=1))
        proactive.set_recording(True)
        sched._tick(t0 + timedelta(seconds=181))
        self.assertEqual(fired, [])

    def test_idle_fallback_lines(self):
        """3分钟保险的兜底话术：LLM 万一没说出口，她至少说一句软话（保险必须有声）"""
        self.assertTrue(len(proactive.IDLE_FALLBACK_LINES) >= 3)  # 备选够多，不重复
        picked = proactive.pick_idle_fallback()
        self.assertIn(picked, proactive.IDLE_FALLBACK_LINES)


class TestOutingFollow(unittest.TestCase):
    """出门跟进：他说要去吃烧烤/逛街 → 她记住 → 过一阵子惦记着问结果"""

    def setUp(self):
        _clear()
        special.mark_asked_birthday()  # 不测"问生日"，防调度器额外触发

    def test_extract_outings(self):
        """"我要去吃烧烤/我去逛街/出门了" → 抓到活动名"""
        self.assertEqual(proactive.extract_outing("宝贝我要去吃烧烤了"), "吃烧烤")
        self.assertEqual(proactive.extract_outing("我去逛街啦"), "逛街")
        self.assertEqual(proactive.extract_outing("待会我去看电影"), "看电影")
        self.assertEqual(proactive.extract_outing("我去上班了"), "上班")

    def test_extract_ignore_wishes_and_past(self):
        """防误报：愿望（想去）、过去式（去过）、问别人（你去）都不记录"""
        self.assertIsNone(proactive.extract_outing("好想吃烧烤啊"))
        self.assertIsNone(proactive.extract_outing("我去过那家店了"))
        self.assertIsNone(proactive.extract_outing("你要去逛街吗"))
        self.assertIsNone(proactive.extract_outing("今天下雨，不出门了"))

    def test_due_after_range(self):
        """到了跟进时间 → 触发一次并从存档删除"""
        t0 = datetime(2026, 8, 20, 19, 0)
        proactive.add_outing("吃烧烤", follow_at=t0 + timedelta(minutes=45))
        self.assertEqual(proactive.get_due_outings(t0), [])          # 还没到
        due = proactive.get_due_outings(t0 + timedelta(minutes=45))  # 到了
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["activity"], "吃烧烤")
        self.assertEqual(proactive.get_due_outings(t0 + timedelta(minutes=50)), [])  # 已翻篇

    def test_cancel_on_return(self):
        """他说"我回来了" → 跟进取消（人都回来了还问啥）"""
        t0 = datetime(2026, 8, 20, 19, 0)
        proactive.add_outing("吃烧烤", follow_at=t0 + timedelta(minutes=45))
        proactive.cancel_outings_if_back("我到家啦～好饱")
        self.assertEqual(proactive.get_due_outings(t0 + timedelta(hours=1)), [])

    def test_no_cancel_when_still_out(self):
        """没说回来 → 跟进照旧"""
        t0 = datetime(2026, 8, 20, 19, 0)
        proactive.add_outing("逛街", follow_at=t0 + timedelta(minutes=45))
        proactive.cancel_outings_if_back("这家店好多人啊")
        self.assertEqual(len(proactive.get_due_outings(t0 + timedelta(minutes=45))), 1)

    def test_scheduler_fires_outing_follow(self):
        """调度器链路：到跟进时间 → 触发"出门跟进"事件（LLM 自然地问结果）"""
        t0 = datetime(2026, 8, 20, 19, 0)
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        # 19:00 出门，45分钟后跟进
        proactive.add_outing("吃烧烤", follow_at=t0 + timedelta(minutes=45))
        sched._tick(t0 + timedelta(minutes=44))  # 还没到
        self.assertEqual(fired, [])
        sched._tick(t0 + timedelta(minutes=45))  # 到了 → 触发一次
        self.assertEqual([e for e in fired if e[0] == "出门跟进"][0][0], "出门跟进")
        sched._tick(t0 + timedelta(minutes=60))  # 已翻篇 → 不再触发
        self.assertEqual(len([e for e in fired if e[0] == "出门跟进"]), 1)


class TestNightAndBudget(unittest.TestCase):
    """真人感研究落地（docs/research/human-like-dialogue.md）：
    夜间静默（22-8点她"假装睡了"）+ 每天主动配额（少而真 ≤2 次）"""

    def setUp(self):
        _clear()
        proactive._last_activity = None
        proactive._last_idle_chat = None
        proactive._proactive_enabled = True
        proactive._recording = False
        proactive._budget_date = None
        proactive._budget_used = 0
        # 今天问过生日 → 调度器不触发"问生日"（那是 test_special 的职责）
        special.mark_asked_birthday(datetime(2026, 8, 20))

    # --- 夜间静默 ---

    def test_is_night_boundary(self):
        """22:00 她睡了、8:00 醒来（22:00/7:59 夜间，21:59/8:00 不是）"""
        self.assertFalse(proactive.is_night(datetime(2026, 8, 20, 21, 59)))
        self.assertTrue(proactive.is_night(datetime(2026, 8, 20, 22, 0)))
        self.assertTrue(proactive.is_night(datetime(2026, 8, 21, 7, 59)))
        self.assertFalse(proactive.is_night(datetime(2026, 8, 21, 8, 0)))

    def test_night_blocks_all_unreasoned_proactive(self):
        """夜间（23:00，冷场10分钟）→ 节奏窗口/闲聊/出门跟进全不触发"""
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        proactive.mark_activity(datetime(2026, 8, 20, 22, 50))  # 10分钟前互动过
        proactive.add_outing("吃烧烤", follow_at=datetime(2026, 8, 20, 22, 0))  # 早到点的跟进
        sched._tick(datetime(2026, 8, 20, 23, 0))
        self.assertEqual(fired, [])

    def test_night_reminder_still_fires(self):
        """夜间她"睡了"但答应的提醒照常（承诺优先于睡觉）"""
        fake_now = datetime(2026, 8, 20, 23, 1)
        proactive.add_reminders([{
            "id": "t9", "content": "该喝水了",
            "trigger_at": datetime(2026, 8, 20, 23, 0).strftime("%Y-%m-%d %H:%M"),
            "executed": False, "dismissed": False,
        }])
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        sched._tick(fake_now)
        self.assertEqual(fired, [("提醒", "该喝水了")])

    def test_night_outing_deferred_then_morning_asks(self):
        """夜间到点的出门跟进：不触发但数据保留 → 早上她翻旧账地问"""
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        proactive.add_outing("吃烧烤", follow_at=datetime(2026, 8, 20, 23, 0))
        sched._tick(datetime(2026, 8, 20, 23, 30))  # 夜间 → 不触发
        self.assertEqual(fired, [])
        # 数据还在（夜间没删，只跳过触发）
        self.assertEqual(len(proactive._load_json(proactive.OUTING_FILE, [])), 1)
        sched._tick(datetime(2026, 8, 21, 9, 0))  # 早上她惦记着问
        self.assertEqual(len([e for e in fired if e[0] == "出门跟进"]), 1)

    # --- 每日主动配额（少而真 ≤2 次/天） ---

    def test_budget_two_per_day(self):
        """白天三个窗口机会（早8/午12/晚6）→ 配额2次：前两个触发，第三个安静"""
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        sched._tick(datetime(2026, 8, 20, 8, 0))    # morning 早安
        sched._tick(datetime(2026, 8, 20, 12, 0))   # noon 午间关心
        sched._tick(datetime(2026, 8, 20, 18, 0))   # evening 下班问候 → 预算用完
        self.assertEqual(len(fired), 2)

    def test_budget_resets_next_day(self):
        """跨天重置：今天用完，明天又有额度"""
        t = datetime(2026, 8, 20, 8, 0)
        self.assertTrue(proactive.try_use_proactive_budget(t))
        self.assertTrue(proactive.try_use_proactive_budget(t))
        self.assertFalse(proactive.try_use_proactive_budget(t))
        self.assertTrue(proactive.try_use_proactive_budget(datetime(2026, 8, 21, 8, 0)))

    def test_idle_chat_uses_budget(self):
        """闲聊保险也占配额：白天冷场开口一次后，第二次冷场如果预算用完就安静"""
        fired = []
        sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
        t0 = datetime(2026, 8, 20, 9, 0)
        proactive.mark_activity(t0 - timedelta(minutes=10))
        sched._tick(t0)  # 第一次冷场 → 闲聊（用掉1次）
        sched._tick(t0 + timedelta(minutes=16))  # 又冷场 → 闲聊（用掉第2次）
        sched._tick(t0 + timedelta(minutes=32))  # 还冷场 → 预算用完，安静陪着
        self.assertEqual(len([e for e in fired if e[0] == "闲聊"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
