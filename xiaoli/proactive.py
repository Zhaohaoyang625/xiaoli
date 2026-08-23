# ============================================
# 小李的主动互动层（B.2）
# 设计依据：
#   1. 互动节奏表（docs/interaction-rhythm.md）—— 一天4次主动窗口
#   2. UTSUWA 提醒机制 —— [reminder:5min] 标签 + 定时器 + 错过浮现
#   3. UTSUWA 事件层 —— 机器回合用 <event> 块进入 prompt，不伪装用户消息
# 原则：主动但不轰炸（一天4次窗口+你找她秒回）；机器回合不推进关系
# ============================================

import json
import os
from xiaoli import paths  # 统一路径（数据/模型在项目根）
import random
import re
import threading
import uuid
from datetime import datetime, timedelta

from xiaoli import special  # 特殊日子（生日/节日）检查
from xiaoli import world_brief  # 世界简报（v2.3：她每天"刷手机"储备世界大事+热梗）

# 提醒存档
REMINDERS_FILE = os.path.join(paths.DATA_DIR, "reminders.json")
# 节奏触发记录（每天每窗口一次）
PROACTIVE_FILE = os.path.join(paths.DATA_DIR, "proactive.json")
# 出门跟进存档（他说要去吃烧烤/逛街 → 她惦记着过会儿问结果）
OUTING_FILE = os.path.join(paths.DATA_DIR, "outings.json")

# 主动找话题（2026-08-20 用户需求："开启聊天后她也能自己向我说话"）：
# 不是死板计时器——而是低频给她"开口机会"，由她自己判断有没有想说的：
# 距上次互动 >3 分钟 且 距上次机会 >15 分钟 → 调度器给她一次机会，LLM 决定说或安静
IDLE_MIN_ACTIVITY_SECONDS = 180
MIN_IDLE_CHAT_GAP_SECONDS = 900

# 真人感研究落地（2026-08-20，docs/research/human-like-dialogue.md）：
# - 每日"主动发起"配额：少而真（Replika 实测每天 1-2 条 + 产品数据：主动过多
#   用户从"被重视"变"被粘住"的反感）。只算"无理由主动"（节奏窗口/闲聊保险）；
#   提醒/特殊日/问生日/出门跟进这些有理由的主动不占配额
DAILY_PROACTIVE_BUDGET = 2
# - 夜间静默：22:00-8:00 她"假装睡了"不主动（revive-companion 静默时段 + 用户拍板），
#   但你在她随时秒回；早上她自然醒来（问候/早安照常）。提醒除外（答应的事必须履行）
NIGHT_SILENT_START_HOUR = 22
NIGHT_SILENT_END_HOUR = 8

# 节奏窗口（小时）：起床后/午间/傍晚/睡前
WINDOWS = [
    ("morning", 7, 9, "早安"),
    ("noon", 11, 13, "午间关心"),
    ("evening", 17, 19, "下班问候"),
    ("night", 21, 23, "睡前陪伴"),
]


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- 提醒（UTSUWA 机制） ----------

# 零宽断言：不消耗 [ 或 [/reminder] 的字符，避免吞掉下一个标签的开头。
# 实测教训 2026-08-20：LLM 想表达"30秒"时会写 [reminder:0.5min]（小数）或 [reminder:30sec]，
# 原正则只认整数 → 0.5min 整条标签被静默丢弃（用户求了提醒却没提醒，也不知道哪出错了）。
# 现在支持：整数/小数（0.5min=30秒）+ 秒单位（sec/s）
_TAG_RE = r"\[reminder:(\d+(?:\.\d+)?)\s*(sec|s|min|hour|day)\](.*?)(?=\[/reminder\]|\[|\Z)"
_UNIT_SECONDS = {"sec": 1, "s": 1, "min": 60, "hour": 3600, "day": 86400}


def parse_reminder_tags(reply):
    """从小李的回复里提取提醒标签：[reminder:5min]内容[/reminder]
    实测教训：flash 模型会省略闭合标签（[/reminder]），所以兼容两种情况：
    有闭合 → 内容到 [/reminder]；无闭合 → 内容到下一个 [ 或回复结尾。
    支持的时间单位：sec/s（秒）、min（分钟）、hour（小时）、day（天）；
    支持小数（0.5min=30秒）。时间精度到秒（30 秒的提醒也准）。"""
    found = []
    for m in re.finditer(_TAG_RE, reply, re.S):
        amount = float(m.group(1))
        delta = timedelta(seconds=amount * _UNIT_SECONDS[m.group(2)])
        found.append({
            "id": str(uuid.uuid4())[:8],
            "content": m.group(3).strip(),
            "trigger_at": (datetime.now() + delta).strftime("%Y-%m-%d %H:%M:%S"),
            "executed": False,
            "dismissed": False,
        })
    return found


def add_reminders(reminders):
    data = _load_json(REMINDERS_FILE, [])
    data.extend(reminders)
    _save_json(REMINDERS_FILE, data)


def get_due_reminders(now=None):
    """返回到期的、未执行的提醒，并标记为已执行。"""
    if now is None:
        now = datetime.now()
    data = _load_json(REMINDERS_FILE, [])
    due = []
    remaining = []
    for r in data:
        if r.get("executed") or r.get("dismissed"):
            remaining.append(r)
            continue
        try:
            t = datetime.strptime(r["trigger_at"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                t = datetime.strptime(r["trigger_at"], "%Y-%m-%d %H:%M")  # 兼容旧数据
            except ValueError:
                remaining.append(r)
                continue
        except (KeyError, TypeError):
            remaining.append(r)
            continue
        if t <= now:
            r["executed"] = True
            due.append(r)
            remaining.append(r)  # 保留记录，可查看
        else:
            remaining.append(r)
    _save_json(REMINDERS_FILE, remaining)
    return due


def get_missed_reminders(now=None):
    """程序关闭期间到点的提醒（executed=True 但从未被展示过）→ 用 dismissed 标记。
    这里简化为：启动时检查有无 executed 且未 dismissed 的提醒，返回它们。"""
    if now is None:
        now = datetime.now()
    data = _load_json(REMINDERS_FILE, [])
    missed = [r for r in data if r.get("executed") and not r.get("dismissed")]
    return missed


def dismiss_reminders(ids):
    data = _load_json(REMINDERS_FILE, [])
    for r in data:
        if r["id"] in ids:
            r["dismissed"] = True
    _save_json(REMINDERS_FILE, data)


# ---------- 节奏窗口（互动节奏表） ----------

def get_window_now(now=None):
    """当前落在哪个节奏窗口。不在窗口内返回 None。"""
    if now is None:
        now = datetime.now()
    for key, start, end, name in WINDOWS:
        if start <= now.hour < end:
            return key, name
    return None, None


def should_fire_window(key, now=None):
    """今天这个窗口触发过没有。"""
    if now is None:
        now = datetime.now()
    data = _load_json(PROACTIVE_FILE, {})
    last = data.get("last_trigger", {}).get(key)
    return last != now.strftime("%Y-%m-%d")


def mark_window_fired(key, now=None):
    if now is None:
        now = datetime.now()
    data = _load_json(PROACTIVE_FILE, {})
    data.setdefault("last_trigger", {})[key] = now.strftime("%Y-%m-%d")
    _save_json(PROACTIVE_FILE, data)


def build_event_message(event_type, content):
    """把触发的事包装成 <event> 块（学自 UTSUWA：不伪装成用户消息）。"""
    return f"<event>{event_type}：{content}。这不是他说的话，是你自己决定想对他说的。</event>"


# ---------- 主动找话题（对话有来有往，不靠你单方面起话头） ----------

_last_activity = None    # 最后一次互动时间（用户说话或她说话都算）
_last_idle_chat = None   # 上次给"开口机会"的时间
_proactive_enabled = True  # 主动讲话总开关（网页💬按钮控制；提醒除外）
_recording = False       # 正在录音（用户在说话 → 她不打扰）

# v2 P2 话题转移检测（Proactive Dialogue Survey）：他连续几句都是极短回应
# （嗯/哈哈/好）→ 话题可能聊腻了 → 下次主动机会她该换个话题（真人不会硬聊）
_short_reply_streak = 0
SHORT_REPLY_LIMIT = 3   # 连续 3 句短回应 → 判定话题耗尽


def mark_reply_len(text):
    """每轮用户输入后调用：记录回应长度，连续极短 → 话题可能耗尽"""
    global _short_reply_streak
    if text and len(text.strip()) <= 6:
        _short_reply_streak += 1
    else:
        _short_reply_streak = 0


def topic_exhausted():
    """话题是否可能耗尽（连续短回应）"""
    return _short_reply_streak >= SHORT_REPLY_LIMIT


def mark_activity(now=None):
    """每次互动后调用：她/你说过话，计时器清零"""
    global _last_activity
    _last_activity = now or datetime.now()


def set_proactive_enabled(on):
    """网页💬开关：on=True 她全自动（早安/晚安/生日/追话）；on=False 只回复"""
    global _proactive_enabled
    _proactive_enabled = bool(on)


def is_proactive_enabled():
    return _proactive_enabled


def set_recording(recording):
    """录音期间调用：你在说话 → 她不抢话（打断机制的核心）"""
    global _recording
    _recording = bool(recording)


def is_recording():
    return _recording


def should_idle_chat(now=None):
    """给她一次"开口机会"：你有一阵子没说话（>3分钟）且上次机会够久（>15分钟）。
    注意：这只是机会，她说还是不说由她自己判断（LLM 输出空 = 安静陪着）"""
    global _last_activity, _last_idle_chat
    if now is None:
        now = datetime.now()
    if _last_activity is None:
        return False  # 还没互动过，不贸然开口
    if (now - _last_activity).total_seconds() < IDLE_MIN_ACTIVITY_SECONDS:
        return False  # 你刚说过话，等她先开口
    if _last_idle_chat and \
            (now - _last_idle_chat).total_seconds() < MIN_IDLE_CHAT_GAP_SECONDS:
        return False  # 刚给过机会，别打扰
    return True


def mark_idle_chat(now=None):
    global _last_idle_chat
    _last_idle_chat = now or datetime.now()


# ---------- 真人感节奏（2026-08-20 研究落地） ----------

# 每天"主动发起"预算（内存即可：程序常开；跨天自动重置）
_budget_date = None
_budget_used = 0


def is_night(now=None):
    """夜间（22:00-8:00）：她"假装睡了"——不主动发起，但你在她随时秒回"""
    if now is None:
        now = datetime.now()
    return now.hour >= NIGHT_SILENT_START_HOUR or now.hour < NIGHT_SILENT_END_HOUR


def try_use_proactive_budget(now=None):
    """主动发起配额：每天最多 DAILY_PROACTIVE_BUDGET 次（少而真，宁可少不可烦）。
    返回是否还有额度；有额度则消耗一次。跨天自动重置。"""
    global _budget_date, _budget_used
    if now is None:
        now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if _budget_date != today:
        _budget_date, _budget_used = today, 0
    if _budget_used >= DAILY_PROACTIVE_BUDGET:
        return False
    _budget_used += 1
    return True


# 3分钟保险丝的兜底话术：LLM 万一走神没说出口，她至少要说一句软话（保险必须有声）
# 研究教训（产品反例）：绝不出现"你为什么不理我"这类追问（用户3天没回被AI问
# "是不是我做错了什么"是公认反面教材）——改为分享自己/温柔惦记
IDLE_FALLBACK_LINES = [
    "寶貝～你在忙嗎？人家有點想你耶……",
    "嘿嘿，趁你沒說話，人家偷偷想你了一下啦～",
    "寶貝～肚子餓不餓呀？要不要休息一下下？",
    "寶貝，今天過得怎麼樣呀？人家想聽你說說～",
    "人家今天看到一個超可愛的貓咪影片耶，你忙完人家放給你看～",
    "寶貝～你慢慢忙，人家在這裡陪著你喔。",
]


def pick_idle_fallback():
    """闲聊保险的兜底：随机挑一句软话（她至少会说点什么，不冷场）"""
    return random.choice(IDLE_FALLBACK_LINES)


# ---------- 出门跟进（2026-08-20 用户："请她吃烧烤逛街实现不了，不知道怎么对话"） ----------
# 她不能真的出门，但"住在手机里的女朋友"可以惦记你：
# 你说要去吃烧烤/逛街 → 她记住 → 30~90分钟后（随机，真人惦记没准点）
# 她自然地问你结果（学自 Replika"跟进式主动"——比干巴巴的"早安"更像真人）
OUTING_FOLLOW_RANGE = (30, 90)  # 出门后多久跟进（分钟）

# 出门意图：必须是"我去/我要去/出门/出发"这类确定要去的话（防误报：
# "好想去吃烧烤"是愿望不记录；"我去过那家店"是过去式不记录；"你去逛街吗"是问别人不记录）
_OUTING_RE = re.compile(
    r"(?:我(?:要|现在|待会|一会儿|马上)?去|出门|出发)"
    r"(?P<act>(?:吃)?(?:烧烤|火锅|串串|夜宵|小龙虾|麻辣烫|小吃|饭|奶茶|"
    r"早餐|午餐|晚餐)|逛街|看电影|超市|买菜|上班|开会|爬山|打球|跑步|"
    r"健身|出差|旅游|旅行|公园|散步|遛狗|游泳|理发|上课|考试|医院|体检)"
)
# 回来了 → 跟进取消（人都回来了还问啥，直接聊今天玩得怎么样就行）
# "了/啦"都要兼容：真人说"我到家了""我到家啦"都有
_OUTING_DONE_RE = re.compile(r"(?:回来|到家|回家|吃完|逛完|结束)(?:了|啦)")


def extract_outing(text):
    """从他说的话里抓"要去做什么"（活动名），不是出门/没具体活动 → None"""
    m = _OUTING_RE.search(text or "")
    if not m:
        return None
    return m.group("act").strip()


def add_outing(activity, follow_at=None):
    """记下"他出门了"。follow_at 不传 → 30~90分钟后随机跟进（真人惦记没准点）"""
    if follow_at is None:
        follow_at = datetime.now() + timedelta(minutes=random.randint(*OUTING_FOLLOW_RANGE))
    data = _load_json(OUTING_FILE, [])
    data.append({"id": str(uuid.uuid4())[:8], "activity": activity,
                 "follow_at": follow_at.strftime("%Y-%m-%d %H:%M")})
    _save_json(OUTING_FILE, data)


def get_due_outings(now=None):
    """到跟进时间的出门记录 → 返回并从存档删除（一次性的，问完就翻篇）"""
    if now is None:
        now = datetime.now()
    data = _load_json(OUTING_FILE, [])
    due, remaining = [], []
    for o in data:
        try:
            t = datetime.strptime(o["follow_at"], "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):
            continue
        if t <= now:
            due.append(o)
        else:
            remaining.append(o)
    _save_json(OUTING_FILE, remaining)
    return due


def cancel_outings_if_back(text):
    """他说"我回来了/到家了" → 出门跟进取消（她不用再问结果了）"""
    if _OUTING_DONE_RE.search(text or ""):
        _save_json(OUTING_FILE, [])


# ---------- 调度器（后台线程） ----------

class Scheduler:
    """后台调度器：每秒检查一次节奏窗口和到期提醒，到点调用回调。"""

    def __init__(self, on_event):
        self.on_event = on_event  # 回调(event_type, content, is_user_simulated)
        self._stop = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[调度器出错：{e}]")
            self._stop.wait(1)

    def _tick(self, now=None):
        if now is None:
            now = datetime.now()
        # 0. 热梗缓存刷新（v2.2 反向接梗治本）：她"刷抖音"的习惯——每天最多刷一次
        #    最新热梗存缓存（工作台注入用）。联网搜索 3~8 秒 → 后台线程，不阻塞调度
        if not getattr(self, "_slang_refreshing", False):
            self._slang_refreshing = True
            threading.Thread(target=self._refresh_world_bg, daemon=True).start()
        # 1. 正在录音（你在说话）→ 她不抢话；主动开关关闭 → 她只回复；
        #    夜间（22-8点）→ 她"假装睡了"不主动（提醒除外——那是她答应你的事，必须履行）
        passive = _recording or not _proactive_enabled or is_night(now)
        # 1. 节奏窗口（每天主动配额内，少而真：先到先得，最多2次）
        if not passive:
            key, name = get_window_now(now)
            if key and should_fire_window(key, now) and try_use_proactive_budget(now):
                self.on_event("节奏", name, now.strftime("%H:%M"))
                mark_window_fired(key, now)
        # 2. 到期提醒（不受开关和录音限制：答应的事必须做）
        due = get_due_reminders(now)
        for r in due:
            self.on_event("提醒", r["content"], r["trigger_at"])
            dismiss_reminders([r["id"]])
        # 3. 特殊日子（生日/节日）：每天最多主动说一次，跨天运行零点也能触发
        if not passive:
            special_desc = special.check_today(now)
            if special_desc and special.should_fire_today(now):
                self.on_event("特殊日", special_desc, now.strftime("%H:%M"))
                special.mark_fired_today(now)
            # 4. 不知道他生日时，每隔几天找机会主动问他（像真女朋友一样想了解你）
            if special.should_ask_birthday(now):
                self.on_event(
                    "特殊日",
                    "你还不知道他的生日。这次主动找他时，找个自然的方式撒娇问他"
                    "（他告诉你了就好好记住，以后他生日要给他惊喜）",
                    now.strftime("%H:%M"),
                )
                special.mark_asked_birthday(now)
            # 5. 闲聊保险（>3分钟没互动 + >15分钟没机会）：3分钟是保险丝——
            #    她日常靠"每轮判断"延续话题，万一冷场了，到点**必须**主动找他
            #    （LLM 没说出口 → 程序用兜底话术保证她一定开口）。
            #    占主动配额：今天配额用完（少而真 2 次/天）→ 她安静陪着
            if should_idle_chat(now) and try_use_proactive_budget(now):
                # v2 P2：他最近都是短回应（话题可能耗尽）→ 明确让她换话题
                topic_hint = ""
                if topic_exhausted():
                    topic_hint = "他最近回应都很简短，可能聊腻了当前话题——"
                self.on_event(
                    "闲聊",
                    "他有一阵子没说话了（超过3分钟）。作为女朋友，你现在应该"
                    "主动找他聊——想他、今天遇到的事、问他吃沒吃、撒個嬌，"
                    "隨便找個自然的話題開口（別問'你怎麼不說話'這種）。"
                    + topic_hint,
                    now.strftime("%H:%M"),
                )
                mark_idle_chat(now)
            # 6. 出门跟进：他说过要去吃烧烤/逛街（出门了），过了一阵子她惦记着
            #    自然地问结果（学自 Replika 跟进式主动；他回来说"到家了"会取消）。
            #    夜间不触发（她"睡了"）但**不删数据**——他回来会取消；没取消的话
            #    早上她醒来自然地问"昨天去得怎么样"（翻旧账式跟进反而更真实）
            if not is_night(now):
                for o in get_due_outings(now):
                    self.on_event(
                        "出门跟进",
                        f"他之前说要{o['activity']}，现在过了一段时间。"
                        "你惦记着他，自然地问问他怎么样了（像热恋女友随口关心："
                        "'寶貝～吃得好吗？人多不多？'），语气要软、要自然，别太正式",
                        now.strftime("%H:%M"),
                    )

    def _refresh_world_bg(self):
        """后台刷世界简报（v2.3）：联网 3~8 秒，异常静默——刷不到就保持旧简报，
        不影响任何对话/调度。"""
        try:
            world_brief.refresh_world_brief()
        except Exception:
            pass
        finally:
            self._slang_refreshing = False


# ---------- O6 工作记忆：他说"待会给你看照片"→ 她记得，下次对话带出 ----------
def _is_photo_input(text):
    """他这轮是不是发了图片路径（= 兑现"给你看照片"的承诺）。
    延迟 import 防循环依赖（vision 只在需要时引入）。"""
    try:
        from xiaoli import vision
        return vision.is_photo_path(text)
    except Exception:
        return False



# 学自《Cognitive Architectures for Language Agents》的工作记忆槽：
# "他答应过、还没兑现的事"不该进长期档案（记了也是噪音），而是短期工作槽——
# 下轮对话带出提醒，兑现/两次提醒后清空。真人也是这样：记得，但不唠叨。
# 2026-08-22 实现：单一承诺槽（同时只记一件——真人也记不住三件待办），
# 提醒最多 2 次，第 2 次后删除（她"记得但不想变成唠叨"）
PROMISE_FILE = os.path.join(paths.DATA_DIR, "promises.json")

# "待会/等一下/回头/晚点/明天/改天 + 给你 + 看/发/告诉/说/传 + 内容" → 承诺
# 组4 = 承诺的核心对象（如"照片"），兑现检测用它（他说"照片"=兑现）
_PROMISE_RE = re.compile(
    r"(待会|等一下|等会儿|回头|晚点|明天|改天|之后|过会儿|稍后)"
    r".{0,12}(给你|你).{0,12}(看|发|告诉|说|传|拿)([^，。！？…\s]{1,12})?"
)


def promise_scan(text):
    """从他的话里找"待会给你看X"式承诺 → 存进工作槽。返回 True 表示记住了。
    新的覆盖旧的（真人一样：新待办盖过旧待办）。"""
    m = _PROMISE_RE.search(text)
    if not m:
        return False
    keyword = (m.group(4) or "").strip()
    keyword = re.sub(r"^(这个|那个|一个|一下)", "", keyword) or (m.group(4) or "").strip()
    promise = {
        "content": m.group(0).strip(),
        "keyword": keyword,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "reminded": 0,
    }
    _save_json(PROMISE_FILE, promise)
    return True


def promise_hint(user_input):
    """他这轮说话时，她心里带着的工作槽提示。返回提示块或 None：
    - 他的话里带出承诺关键词（兑现了）→ 清槽，不提示
    - 没兑现 → 提示她记得，提醒次数+1；提醒满 2 次 → 清槽（不唠叨）"""
    promise = _load_json(PROMISE_FILE, None)
    if not promise:
        return None
    kw = promise.get("keyword", "")
    if (kw and kw in user_input) or _is_photo_input(user_input):
        # 兑现了 → 清槽。2026-08-23：他说"待会给你看照片"→ 真发的是图片路径
        # （如 C:\Pics\cat.jpg），路径里没有"照片"两字 → 按关键词判不到，
        # 她下轮还惦记"没给我看"（明明看了！）→ 发图片路径也算兑现
        _save_json(PROMISE_FILE, None)
        return None
    promise["reminded"] = promise.get("reminded", 0) + 1
    if promise["reminded"] >= 2:
        _save_json(PROMISE_FILE, None)
    else:
        _save_json(PROMISE_FILE, promise)
    return (
        f"【你记得他说过】他说过：\"{promise['content']}\"，还没兑现。"
        "他若提到相关话题，可以自然地问一句（像真女朋友惦记着，别催、别正式）；"
        "他没提就别主动翻出来（记得 ≠ 唠叨）。"
    )


def clear_promise():
    """清理工作槽（用不到的时候）"""
    _save_json(PROMISE_FILE, None)
