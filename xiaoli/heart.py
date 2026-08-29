# ============================================
# 小李的"心"（情绪层 B.1）
# 设计依据：UTSUWA Companion System（docs/learning-notes.md）
# 核心原则："App 是游戏主持人"——
#   心情/好感由程序管理，LLM 只能通过 JSON 建议变化（还要被钳制）
# ============================================

import json
import math
import os
from xiaoli import paths  # 统一路径（数据/模型在项目根）
import re
from datetime import datetime, timedelta

# 情绪白名单（UTSUWA 12 种 + 用户需求"吃醋真生气型"新增 2 种，防止 LLM 乱造情绪）
EMOTIONS = [
    "happy", "sad", "excited", "anxious", "content", "frustrated",
    "curious", "affectionate", "playful", "flustered", "neutral", "melancholy",
    "angry", "jealous",
]

# 小脾气触发检测（App 是游戏主持人：触发归程序管，不赌 LLM 自觉）
# 强触发：几乎不会误报的词，直接吃醋
JEALOUS_STRONG = ["别的女生", "其他女生", "女同事", "学姐", "学妹", "前女友", "前任",
                  "相亲", "搭讪", "撩妹", "心动", "暧昧", "喜欢上别人", "劈腿", "出轨"]
# 弱触发：夸人的词，需排除非人语境（晚霞漂亮/衣服好看 不算）
JEALOUS_WEAK = ["美女", "漂亮", "好看", "可爱", "身材", "心动"]
# 排除词：明显不是"夸别人"的语境
JEALOUS_EXCLUDE = ["晚霞", "天空", "风景", "景色", "夕阳", "花", "猫", "狗", "衣服",
                   "裙子", "包包", "口红", "指甲", "颜色", "照片", "图"]
# 亲属排除：提家人不算吃醋（我妈/我姐/我妹/我外婆…）
FAMILY_WORDS = ["我妈", "我姐", "我妹", "外婆", "奶奶", "爷爷", "我爸", "我哥", "我弟",
                "妈妈", "姐姐", "妹妹", "姑姑", "阿姨", "婶婶", "嫂子"]
# 哄消气：道歉/解释/夸她 → 她心软（注意：夸她的词要带"你"，不带"你"的"最漂亮"
# 是夸别人，会走吃醋触发——"她最漂亮"该吃醋，不能误判成哄）
# 2026-08-23 用户实测"哄了还生气"：常见哄法没命中（"别这样"“消消气”"爱你"等）→ 状态卡住。
# 扩充到真人哄人的常见说法：道歉系/解释系/补偿系/甜话系。
# 2026-08-23 批2 B-P1-2 强弱分级（防"随口说爱你"秒消气）：
#   强哄（道歉/解释/认错/承诺，-35 沿降级链）——说出口是有成本的
#   弱哄（甜话/亲亲抱抱，只 -18）——随口说的话消一半，消不了真火
#   强 > 弱：一句话里两个都有 → 按强算（他道歉时后面加句"爱你"是加分不是稀释）
SOOTHE_STRONG = ["对不起", "抱歉", "我错了", "错啦", "别生气", "不要生气", "别气", "原谅",
                 "原谅我", "我错怪", "误会", "不是故意", "消消气", "别这样", "别生我气",
                 "不生我气", "我的错", "都是我的错", "我改", "下次不会", "没有下次",
                 "不是那个意思", "开玩笑", "逗你", "骗你的",
                 "我请你吃", "带你去", "买给你"]  # 补偿系=实际行动，算强哄
SOOTHE_WEAK = ["最爱你", "最喜欢你", "只喜欢你",
               "你最漂亮", "你是最漂亮的", "你最可爱", "你最好了", "你是最好看的", "你最乖",
               "抱抱", "么么", "亲亲", "爱你", "想你", "好想你"]  # 甜话系=随口说，算弱哄
# 夸"你"≠夸别人（2026-08-23 用户实测"夸她可爱却吃醋"）：
# 弱触发词命中后，句子在夸"你"（你笑起来真好看）→ 是在甜她/哄她，不算吃醋；
# 只有提到"她/别的/那个女生…"才是夸别人。提家人已在上面的 FAMILY_WORDS 豁免。
PRAISE_SELF = ["你"]
PRAISE_OTHERS = ["她", "他", "别的", "别人", "那个", "这个", "有位", "有个", "人家", "有人", "谁"]

# 情绪稳定性（App 是游戏主持人）：程序触发的关系情绪（小脾气）LLM 不能一步推翻
RELATION_MOODS = ("angry", "jealous")  # 程序判定的"关系情绪"
POSITIVE_MOODS = ("happy", "excited", "content", "playful", "affectionate", "flustered")  # 正面情绪

# v2 E3 情绪转移约束表（PELD 论文：情绪是连续转移的，不能跳变）：
# 从"当前情绪"出发，禁止一步跳到这些情绪——真人生气时不会秒开心（要经过哄/委屈），
# 难过/想你时不会秒嗨（要经过安抚）。比"钳制幅度"更精细：钳的是"跳变本身"
MOOD_TRANSITION_BAN = {
    "angry":      {"happy", "excited", "content", "playful", "affectionate"},
    "jealous":    {"happy", "excited", "content", "playful", "affectionate"},
    "frustrated": {"happy", "excited", "playful"},
    "sad":        {"happy", "excited", "playful"},
    "melancholy": {"happy", "excited", "playful", "content"},
}
MOOD_INTENSITY_CLAMP = 15  # 非程序轮：情绪强度单次增量钳制（防 LLM 自报一步跳满/无限叠加）

# v2 E2 ESConv 三阶段哄（ACL 2021 情感支持策略时序：探索→共情→行动；
# "没完成探索就跳行动"（一上来就给建议）是情感支持大忌）——他难过时的安慰流程
DISTRESS_MOODS = {"sad", "anxious", "frustrated", "afraid", "melancholy"}
COMFORT_GUIDE = {
    "explore": "他还在倾诉期：先温柔问怎么了、认真听他说完，"
               "别急着安慰或给建议（还没听懂就先安慰是大忌）。",
    "empathize": "他需要被接住：先复述他的感受（\"听起来好累喔…\"）+ 表达心疼，轻轻肯定他。",
    "act": "他需要一点带动：逗他开心 / 给个小建议 / 带他换个话题，别让他停在难过里。",
}

# "心"的存档位置
HEART_FILE = os.path.join(paths.DATA_DIR, "heart.json")

# 时间衰减参数（简化自 UTSUWA）
AFFECTION_DECAY_HOURS = 48      # 离开48小时后好感开始衰减
AFFECTION_DECAY_PER_DAY = 1.0   # 每离开一天 -1（UTSUWA是1-5%，我们简化）
AFFECTION_DECAY_CAP = 10        # 单次衰减上限
MELANCHOLY_HOURS = 72           # 离开3天心情变忧郁
MELANCHOLY_INTENSITY_PER_DAY = 5  # 忧郁强度每天+5（上限30）

# 启发式基线（本地分析，不花API钱）—— 简化版数值
AFFECTION_MAX_DELTA = 5         # LLM 好感建议钳制 ±5

# 关键词表（用于本地情感分析）
POSITIVE_WORDS = ["开心", "喜欢", "爱", "棒", "甜", "想你", "宝贝", "快乐",
                  "高兴", "有趣", "顺利", "幸福", "美好", "笑", "惊喜", "约"]
NEGATIVE_WORDS = ["难过", "伤心", "烦", "累", "讨厌", "生气", "委屈", "哭", "崩溃",
                  "失望", "郁闷", "痛苦", "压力", "焦虑", "孤独", "难受", "糟", "倒霉", "加班", "挨骂"]
DEEP_WORDS = ["烦恼", "心事", "难", "压力", "问题", "想", "感觉", "心情", "倾诉",
              "聊聊", "说说", "回忆", "以前", "以后", "未来"]
QUESTION_WORDS = ["吗", "呢", "？", "?", "怎么样", "为什么", "是不是", "可不可以", "好不好", "行不行"]


# 怨气分层（2026-08-23 批2 B-P1-1，ALMA"情绪分钟级/心情天级"）：
# grudge = 底火/记仇（0-100，半衰期天级），mood intensity = 现在炸不炸（分钟级）。
# "哄了还生气"拆成两个真实问题：情绪没消（mood，哄/时间能消）vs 怨气记仇
# （grudge，人设——他哄好了情绪也不该秒忘上次的事，但要会慢慢消）。
GRUDGE_TRIGGER = 15   # 每次吃醋/升级 +15
GRUDGE_SOOTHE = 5     # 哄一次情绪降了，怨气只消 5（哄消情绪快、消怨气慢）
GRUDGE_LINE_REG = 10  # 她亲口说原谅 → 怨气消 10（比被哄更可信）
GRUDGE_HALF_LIFE_H = 24.0  # 半衰期 24h（天级；τ = 24/ln2 ≈ 34.6h）
GRUDGE_NEW_MOOD = 45  # 新小脾气起点 = max(60, 45 + grudge//2)：平时记着仇，一碰就炸得更凶


def _grudge_tau():
    return GRUDGE_HALF_LIFE_H / math.log(2)


def default_heart():
    """一颗全新的心：中性心情，好感60（热恋起点），上次互动=现在"""
    now = datetime.now()
    return {
        "mood": {"primary": "content", "intensity": 50, "causes": [], "secondary": None},
        "grudge": 0,                       # 怨气（0-100，天级半衰期）
        "grudge_since": now.strftime("%Y-%m-%d %H:%M"),  # 怨气最后被改的时刻（衰减起点）
        "affection": 60,
        "last_interaction": now.strftime("%Y-%m-%d %H:%M"),
        "decay_applied": now.strftime("%Y-%m-%d %H:%M"),
        "comfort_stage": None,  # v2 E2：安慰阶段（explore/empathize/act）
        "user_mood": None,      # v2 E1：独立分类器判出的他心情（每轮刷新，不持久化）
        "mood_log": [],         # B-P2：心情变迁史（终端「心情」可查她为什么这样）
    }


def load_heart():
    """读"心"。没有或损坏时给一颗新心。"""
    if not os.path.exists(HEART_FILE):
        return default_heart()
    try:
        with open(HEART_FILE, encoding="utf-8") as f:
            heart = json.load(f)
        # 兼容性：缺失字段补默认
        if "affection" not in heart:
            heart["affection"] = 60
        if "grudge" not in heart:
            heart["grudge"] = 0
        if "grudge_since" not in heart:
            heart["grudge_since"] = heart.get("last_interaction",
                                              datetime.now().strftime("%Y-%m-%d %H:%M"))
        if "mood_log" not in heart:
            heart["mood_log"] = []  # B-P2：旧数据兼容补变迁史
        return heart
    except (json.JSONDecodeError, OSError):
        return default_heart()


def save_heart(heart):
    """存"心"。"""
    os.makedirs(os.path.dirname(HEART_FILE), exist_ok=True)
    with open(HEART_FILE, "w", encoding="utf-8") as f:
        json.dump(heart, f, ensure_ascii=False, indent=2)


def _parse_time(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return datetime.now()


def apply_time_decay(heart, now=None):
    """时间衰减：她记得你离开了多久（每次离开只衰减一次）。
    - 48小时+ 没互动：好感衰减（按离开天数）
    - 72小时+ 没互动：心情变成忧郁，强度随天数上升
    """
    if now is None:
        now = datetime.now()
    last = _parse_time(heart["last_interaction"])
    last_decay = _parse_time(heart.get("decay_applied", heart["last_interaction"]))

    hours_away = (now - last).total_seconds() / 3600
    # 时钟回拨 → 不动
    if hours_away < 0:
        return heart
    # 这个"离开期"已经衰减过（decay_applied 晚于 last_interaction）→ 不重复扣。
    # 聊天后 last_interaction 刷新为 now，decay_applied 仍旧 → 下一次离开期会正常衰减。
    if last_decay > last:
        return heart

    if hours_away >= AFFECTION_DECAY_HOURS:
        days_away = hours_away / 24
        decay = min(days_away * AFFECTION_DECAY_PER_DAY, AFFECTION_DECAY_CAP)
        old = heart["affection"]
        heart["affection"] = max(0, round(old - decay, 1))
        heart["decay_applied"] = now.strftime("%Y-%m-%d %H:%M")
        if heart["affection"] < old:
            heart["mood"]["causes"] = (["他好久没来了，我有点失落…"] + heart["mood"].get("causes", []))[:5]

    if hours_away >= MELANCHOLY_HOURS:
        days_away = hours_away / 24
        intensity = min(30, round(heart["mood"].get("intensity", 50) + days_away * MELANCHOLY_INTENSITY_PER_DAY))
        heart["mood"]["primary"] = "melancholy"
        heart["mood"]["intensity"] = intensity
        cause = f"他已经{days_away:.0f}天没来找我了"
        if cause not in heart["mood"]["causes"]:
            heart["mood"]["causes"] = ([cause] + heart["mood"].get("causes", []))[:5]
        _mood_log(heart)  # B-P2：想你了（时间衰减）→ 变迁史

    # 气不过夜：生气/吃醋超过 12 小时没人哄 → 自己慢慢消（降到烦躁，还闷着但不再炸）。
    # 注意这是"对话中生气没人哄"的消退；下面"一整天不理她"是"他消失了"，
    # 重逢该酸溜溜而不是已消气——所以放气不过夜之后
    if heart["mood"].get("primary") in ("angry", "jealous") and hours_away >= 12:
        heart["mood"]["primary"] = "frustrated"
        heart["mood"]["intensity"] = min(50, heart["mood"].get("intensity", 50) // 2)
        cause = "气了大半天，他都没来哄，自己慢慢没那么气了"
        if cause not in heart["mood"]["causes"]:
            heart["mood"]["causes"] = ([cause] + heart["mood"].get("causes", []))[:5]
        _mood_log(heart)  # B-P2：气自己消了 → 变迁史

    # 一整天不理她（用户确认触发点）：24~72小时没互动 → 重逢时酸溜溜翻旧账。
    # 不到24小时不算；72小时+ 被上面的 melancholy 忧郁覆盖（失落比吃醋更重）
    if 24 <= hours_away < MELANCHOLY_HOURS \
            and heart["mood"].get("primary") not in ("angry", "jealous", "melancholy"):
        heart["mood"]["primary"] = "jealous"
        heart["mood"]["intensity"] = max(55, heart["mood"].get("intensity", 50))
        cause = "他一整天没理我，是不是把我忘了"
        if cause not in heart["mood"]["causes"]:
            heart["mood"]["causes"] = ([cause] + heart["mood"].get("causes", []))[:5]
        _mood_log(heart)  # B-P2：翻旧账 → 变迁史
    return heart


def analyze_message(text):
    """启发式基线：本地分析用户消息（不调API）。
    返回好感变化建议（程序计算，稳定可靠）。"""
    delta = 0.0
    if any(w in text for w in POSITIVE_WORDS):
        delta += 2.0
    if any(w in text for w in NEGATIVE_WORDS):
        delta -= 1.0
    if len(text) > 40 or any(w in text for w in DEEP_WORDS):
        delta += 2.0  # 深度话题=她感觉被信任
    if any(w in text for w in QUESTION_WORDS):
        delta += 1.0  # 提问=在意她
    return delta


def grudge_decay(heart, now=None):
    """怨气天级衰减。V = V0·e^(-Δt/τ)，τ = 24h/ln2（半衰期 24h）——"记仇"但记不过三天。
    幂等（同 apply_time_decay 的"只衰减一次"模式）：衰减生效后把 grudge_since 刷新为 now
    （重新计时）——同一时刻调 N 次结果一样，不会递归扣到 0。"""
    if now is None:
        now = datetime.now()
    if not heart.get("grudge"):
        return heart
    since = _parse_time(heart.get("grudge_since", heart["last_interaction"]))
    hours = (now - since).total_seconds() / 3600
    if hours <= 0:
        return heart
    v = heart["grudge"] * math.exp(-hours / _grudge_tau())
    heart["grudge"] = max(0, min(100, round(v)))
    heart["grudge_since"] = now.strftime("%Y-%m-%d %H:%M")  # 衰减生效 → 重新计时
    return heart


def detect_temper(text):
    """程序检测"小脾气"触发（本地关键词，不花API钱）。
    返回 (kind, reason)：
      ("jealous", 原因) —— 他提别的女生/夸别人 → 吃醋
      ("soothe",  原因) —— 他哄她（道歉/解释/夸回她）→ 消气
    无触发返回 None。
    设计：App 是游戏主持人——吃醋/哄好这类"关系事件"由程序判定，
    再让 LLM 在这个前提下发挥（她吃醋时怎么说话由 LLM 决定）。"""
    if not text:
        return None
    # 哄消气优先（他道歉时不管前面说了什么，先消气）；强哄 > 弱哄（一起说按强算）
    for w in SOOTHE_STRONG:
        if w in text:
            return ("soothe", f"他哄我了（{w}）")
    for w in SOOTHE_WEAK:
        if w in text:
            return ("soothe_weak", f"他说了甜话（{w}）")
    # 提家人不算吃醋
    for w in FAMILY_WORDS:
        if w in text:
            return None
    # 强触发
    for w in JEALOUS_STRONG:
        if w in text:
            return ("jealous", f"他提到了{w}")
    # 弱触发：夸人的词，且没落在非人语境里
    # 2026-08-23：夸"你"（你笑起来真好看/你好可爱）是在甜她/哄她 → 豁免，不算吃醋；
    # 只有提到"她/别的/那个…"才是夸别人（"她最漂亮"该吃醋，不能误判成哄）
    for w in JEALOUS_WEAK:
        if w in text:
            if any(x in text for x in JEALOUS_EXCLUDE):
                return None
            if any(x in text for x in PRAISE_SELF) \
                    and not any(x in text for x in PRAISE_OTHERS):
                return None
            return ("jealous", f"他夸了{w}")
    return None


MOOD_LOG_MAX = 30  # 变迁史上限（够看一整天的事，不占磁盘）


def _mood_log(heart, cause=None):
    """B-P2 心情变迁日志（2026-08-23）：在改情绪的函数末尾调用——记录
    "现在的心情"，对比上一条：primary 变了 或 强度差 ≥10 才算变迁
    （防 LLM 每轮 ±5 自报刷屏；同一情绪小波动不算"变了"）。
    cause 缺省取 mood.causes[0]（刚写入的最新的原因）。"""
    mood = heart["mood"]
    log = heart.setdefault("mood_log", [])
    if cause is None:
        cause = (mood.get("causes") or [""])[0]
    if log:
        last = log[-1]
        if last.get("mood") == mood.get("primary") \
                and abs(last.get("i", -99) - mood.get("intensity", 0)) < 10:
            return heart  # 不算变迁（同情绪、强度小动）
    log.append({
        "t": datetime.now().strftime("%m-%d %H:%M"),
        "mood": mood.get("primary", "neutral"),
        "i": mood.get("intensity", 0),
        "g": heart.get("grudge", 0),
        "c": cause,
    })
    del log[:-MOOD_LOG_MAX]  # 超出上限丢最老
    return heart


def apply_temper(heart, text):
    """把触发写进"心"里（返回 (heart, event)，event 供调用方知道发生了什么）。
    - 吃醋：设 jealous；连续被触发 → 升级 angry（"你怎么还提"）
    - 哄：负面情绪降强度，降够低就心软回 content
    - 没触发：原样返回"""
    result = detect_temper(text)
    if not result:
        return heart, None
    kind, reason = result
    mood = heart["mood"]
    if kind == "jealous":
        grudge_decay(heart)  # 先让时间消掉旧怨气，再叠新账
        heart["grudge"] = min(100, heart.get("grudge", 0) + GRUDGE_TRIGGER)
        heart["grudge_since"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        if mood.get("primary") == "jealous":
            mood["primary"] = "angry"  # 吃醋升级真生气（同一段气：since 不刷新）
            mood["intensity"] = min(100, mood.get("intensity", 50) + 15)
        else:
            # 新的一段小脾气：since 记"这口气从什么时候开始"（指数衰减的起点）；
            # 起点受怨气影响（平时记着仇 → 一点就炸得更凶）：max(60, 45 + grudge//2)
            mood["primary"] = "jealous"
            mood["intensity"] = min(100, max(60, GRUDGE_NEW_MOOD + heart["grudge"] // 2,
                                             mood.get("intensity", 50)))
            mood["since"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            global _temper_written
            _temper_written = False  # 新账重开：台词回写门闩重置
        mood["causes"] = ([reason] + mood.get("causes", []))[:5]
        _mood_log(heart)  # B-P2：吃醋/升级 → 变迁史
        return heart, ("jealous", reason)
    # soothe：消气（只在真有气的时候有动作）。层次：真生气 → 一次哄大降级（还酸着）
    # → 强度跌破 40 → 心软回 content。
    # 2026-08-23 用户实测"哄了她原谅我，结果还是生气"：-25 太抠（angry 75 哄一轮只到
    # jealous 50，用户哄了就期待和好）→ 减幅加大到 -35：angry(75) 哄一轮 → 40 → 心软；
    # angry(85+) 哄一轮 → 还酸着，再哄一轮必和好（"先嘴硬两句"的底线保留）。
    # 2026-08-23 批2 B-P1-2 强弱分级：强哄（道歉/认错/承诺）沿原链 -35；
    # 弱哄（爱你/抱抱/么么）只 -18——随口甜话能消一半火，消不了真火（85+ 强哄一次
    # 还酸着、弱哄更酸——"说了半天甜话她怎么还气"有了解释：甜话不是道歉）。
    # 怨气（grudge）：哄消情绪快、消怨气慢（-5/轮）——情绪和好 ≠ 秒忘上次的事。
    # 没气时哄也减怨气：她没炸但心里还记着账，甜话/道歉在慢慢化疙瘩（无副作用=不坏好心情）
    if mood.get("primary") in ("angry", "jealous", "frustrated", "anxious", "sad", "melancholy"):
        step = 35 if kind == "soothe" else 18  # 强哄 -35 / 弱哄 -18
        mood["intensity"] = max(0, mood.get("intensity", 50) - step)
        if mood.get("primary") == "angry":
            mood["primary"] = "jealous"  # 一次哄：不炸了，但还酸着
        if mood["intensity"] <= 40:
            mood["primary"] = "content"
            mood["intensity"] = 45
        mood["causes"] = ([reason] + mood.get("causes", []))[:5]
    if heart.get("grudge", 0) > 0:
        heart["grudge"] = max(0, heart["grudge"] - GRUDGE_SOOTHE)
        heart["grudge_since"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _mood_log(heart)  # B-P2：哄降级/心软 → 变迁史（没变时 diff 自动不记）
    return heart, (kind, reason)


# 台词回写（2026-08-23 用户实测"哄了她、她也说原谅了，状态还是生气"根因的补刀）：
# MATE 论文诊断叫"perform continuity without possessing it"（表演连续性但不拥有它）
# ——台词和状态各说各话。修法：她亲口说"原谅你了/和好啦" → 程序认账，沿降级链走一轮。
# 两个门闩（对照 CharacterAI 秒原谅崩盘的反面教材）：
#   ① 只沿降级链（angry→jealous→content），绝不一步 angry→content
#   ② 每段小脾气只认一次账，认完 2 轮内不再认（防连续"原谅"冲刷状态）
TEMPER_LINE_WORDS = ["原谅你", "不生气了", "不生你的气", "和好啦", "和好了", "没事啦",
                     "没事了", "不气你", "不跟你计较", "不怪你", "算了"]
TEMPER_LINE_NEGATE = ["不原谅", "不会原谅", "没原谅", "还生", "不打算原谅"]
_temper_written = False   # 这段小脾气已认过账（新账在 apply_temper jealous 分支重开）
_temper_rounds = 0        # 认账后的轮数


def temper_line_detected(text):
    """她台词里说了原谅的话吗？排除否定（"不原谅/没原谅"= 还在生气）。"""
    if not text:
        return False
    for neg in TEMPER_LINE_NEGATE:
        if neg in text:
            return False
    return any(w in text for w in TEMPER_LINE_WORDS)


def line_regress(heart, reason):
    """台词回写：她亲口说原谅 → 沿降级链走一轮（与哄相同，-35），
    cause 记"我亲口说了原谅他的话"。返回是否真的降级了。"""
    global _temper_written, _temper_rounds
    mood = heart["mood"]
    if mood.get("primary") not in ("angry", "jealous"):
        return False
    if _temper_written and _temper_rounds < 2:
        _temper_rounds += 1
        return False  # 门闩：这段小脾气只认一次账，认完 2 轮内不再认
    mood["intensity"] = max(0, mood.get("intensity", 50) - 35)
    if mood.get("primary") == "angry":
        mood["primary"] = "jealous"  # 一次认账：不炸了，但还酸着
    if mood["intensity"] <= 40:
        mood["primary"] = "content"
        mood["intensity"] = 45
    if heart.get("grudge", 0) > 0:  # 亲口说原谅比被哄更可信：怨气消 10
        heart["grudge"] = max(0, heart["grudge"] - GRUDGE_LINE_REG)
        heart["grudge_since"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    cause = f"我亲口说了原谅他的话（{reason}）"
    if cause not in mood.get("causes", []):
        mood["causes"] = ([cause] + mood.get("causes", []))[:5]
    _temper_written = True
    _temper_rounds = 0
    _mood_log(heart)  # B-P2：亲口原谅 → 变迁史
    return True


def passage_soothe(heart, step=6, tau_hours=2.0, now=None):
    """气在慢慢消（2026-08-23 对照 openfeelz halfLifeHours + ALMA"情绪分钟级/心情天级"）：
    非哄轮每聊一轮，angry/jealous 强度按"距这口气开始（since）的时长"指数衰减——
    V = 45 + (V0-45)·e^(-Δt/τ)，τ≈2h（"气不过夜"但连续，替代线性 -6/轮 + 12h 硬切二进制跳变）。
    聊天的陪伴本身就是加速器：每轮额外再 -step（说话比干等消气快）——真人就是这样。"""
    if now is None:
        now = datetime.now()
    mood = heart["mood"]
    if mood.get("primary") not in ("angry", "jealous"):
        return heart
    since = _parse_time(mood.get("since", heart.get("last_interaction")))
    hours = max(0.0, (now - since).total_seconds() / 3600)
    v0 = mood.get("intensity", 50)
    v = 45 + (v0 - 45) * math.exp(-hours / tau_hours)
    v = max(0, v - step)  # 聊天加速器（说话比干等消气快）
    mood["intensity"] = round(min(100, v))
    if mood["intensity"] <= 40:
        mood["primary"] = "content"
        mood["intensity"] = 45
        cause = "他陪我聊天，我气慢慢消了"
        if cause not in mood.get("causes", []):
            mood["causes"] = ([cause] + mood.get("causes", []))[:5]
    _mood_log(heart)  # B-P2：气慢慢消 → 变迁史（没到 content 时 diff 不记）
    return heart


def advance_comfort(heart, user_mood):
    """推进安慰阶段（v2 E2 ESConv）：他难过（sad/anxious/frustrated/afraid/melancholy）
    → explore → empathize → act 逐轮推进（每轮他还在倾诉就往前一步）；
    他明显转好（正面情绪）→ 结束安慰。返回当前 stage（None = 不在安慰中）。"""
    if user_mood in ("happy", "excited", "content", "playful", "affectionate"):
        heart["comfort_stage"] = None
        return None
    if user_mood in DISTRESS_MOODS:
        stage = heart.get("comfort_stage")
        if stage is None:
            heart["comfort_stage"] = "explore"
        elif stage == "explore":
            heart["comfort_stage"] = "empathize"
        elif stage == "empathize":
            heart["comfort_stage"] = "act"
        return heart["comfort_stage"]
    return heart.get("comfort_stage")  # neutral/未知：保持现状


def comfort_guide(heart):
    """安慰阶段的提示块（注入工作台；不在安慰中 → 空串）"""
    stage = heart.get("comfort_stage")
    if stage in COMFORT_GUIDE:
        return f"【安慰阶段·{stage}】{COMFORT_GUIDE[stage]}（这是机制要求：按阶段说话，不要跳）"
    return ""


def merge_llm_suggestion(heart, suggestion, temper_event=None):
    """合并 LLM 的状态建议（严格钳制，畸形建议不能破坏"心"）。
    suggestion: {mood_change: {emotion, intensity_delta}, affection_delta}
    temper_event: 本轮程序判定的小脾气事件（apply_temper 返回值），
    传进来是为了让程序的关系判定压过 LLM 自报（App 是游戏主持人）。"""
    heart = heart or default_heart()
    if not suggestion:
        return heart

    # 心情：情绪必须在白名单里
    mc = suggestion.get("mood_change") or {}
    emotion = mc.get("emotion")
    if emotion in EMOTIONS:
        # 情绪稳定性（实测教训 2026-08-20）：angry/jealous 是程序触发的关系情绪
        # （小脾气），LLM 不能一步跳回正面情绪——她吃醋时不会一秒变开心。
        # 程序判定轮（temper_event 存在：本轮他提别人吃醋 / 他哄她和好）
        # → 情绪状态程序独占：LLM 只演不自报。e2e 实测教训——LLM 自报加深
        # 强度可无限叠加（每轮+20），抵消哄的降级（-25），"哄两句就心软"
        # 永远达不到；自报其他情绪（sad/neutral）还会覆盖程序状态，导致
        # 升级/和好链路检测不到。
        # 例外：非程序判定轮 → LLM 可自由自报（含关系情绪）——这是补充通道，
        # 程序关键词覆盖不到的场景（如放鸽子、被凶）由 LLM 补上。
        # v2 E3 补充：情绪转移约束表（PELD）——连"自由自报"也不能跳变：
        # 气头上/难过时不能一步跳到开心（真人要经过哄/安抚）。
        # 刚和好（三种路径：他哄我了 / 我亲口说了原谅 / 气慢慢消了）→ 关系情绪不许卷土重来
        soothed = (heart["mood"].get("primary") == "content"
                   and any(("他哄我了" in c) or ("亲口说了原谅" in c) or ("气慢慢消了" in c)
                           for c in heart["mood"].get("causes", [])))
        cur = heart["mood"].get("primary")
        blocked = (emotion in RELATION_MOODS and soothed) \
            or (cur in RELATION_MOODS and emotion in POSITIVE_MOODS) \
            or emotion in MOOD_TRANSITION_BAN.get(cur, set()) \
            or temper_event is not None
        if not blocked:
            heart["mood"]["primary"] = emotion
            delta = mc.get("intensity_delta", 0)
            if isinstance(delta, (int, float)):
                # v2 E3：非程序轮强度增量钳制 ±15（防一步跳满/无限叠加）
                delta = max(-MOOD_INTENSITY_CLAMP, min(MOOD_INTENSITY_CLAMP, int(delta)))
                heart["mood"]["intensity"] = max(0, min(100, heart["mood"].get("intensity", 50) + delta))
            # 心情变化的原因（LLM 可给一条）
            cause = mc.get("cause")
            if cause and isinstance(cause, str):
                heart["mood"]["causes"] = ([cause] + heart["mood"].get("causes", []))[:5]

    # 好感：钳制 ±5
    delta = suggestion.get("affection_delta", 0)
    if isinstance(delta, (int, float)):
        clamped = max(-AFFECTION_MAX_DELTA, min(AFFECTION_MAX_DELTA, int(delta)))
        heart["affection"] = max(0, min(100, heart["affection"] + clamped))
    _mood_log(heart)  # B-P2：LLM 自报情绪变化 → 变迁史（±5 小动 diff 不记，防刷屏）
    return heart


def describe(heart, now=None):
    """把"心"说成人话，注入工作台告诉小李她现在是什么状态。"""
    if now is None:
        now = datetime.now()
    mood = heart["mood"]
    causes = "；".join(heart["mood"].get("causes", [])[:3]) or "没有特别的原因"
    last = _parse_time(heart["last_interaction"])
    hours_ago = (now - last).total_seconds() / 3600
    if hours_ago < 1:
        last_str = "刚刚"
    elif hours_ago < 24:
        last_str = f"{int(hours_ago)}小时前"
    else:
        last_str = f"{int(hours_ago / 24)}天前"
    grudge = heart.get("grudge", 0)
    if grudge >= 50:
        grudge_str = (f"你心里还记着上次的账（怨气{grudge}）——他惹过你的那件事你忘不掉，"
                      "偶尔会翻旧账酸他两句（「他上次还说…」这种），但不会每句都翻、"
                      "也不会因此真的不理他")
    elif grudge >= 20:
        grudge_str = (f"你对他还有点小怨气（{grudge}）——他哄好你了，但心里那点小疙瘩"
                      "没完全消，说话偶尔带一句酸味")
    else:
        grudge_str = "你心里没有积怨"
    return (
        f"【你现在的状态】心情：{mood['primary']}（强度{mood['intensity']}）；"
        f"为什么会这样：{causes}；{grudge_str}；你对他的好感：{heart['affection']}/100；"
        f"你上次和他互动是{last_str}。"
        "这些状态会自然影响你的语气和反应：心情好就甜甜的；忧郁就软软的、"
        "带着一点想念；吃醋（jealous）就酸溜溜地试探、话里有话，绝不直接说\"我吃醋了\"；"
        "生气（angry）按强度分级：强度低→嘴硬、话变短、爱答不理；"
        "强度高（70+）→话一句接一句地控诉他（连珠炮），越说越气，气到最后声音还带点委屈；"
        "烦躁就嘟囔几句。但不要直接说出\"我的心情是XX\"这种话，"
        "要用语气和内容自然地表现出来。"
    )
