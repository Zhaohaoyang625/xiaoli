# ============================================
# 小李的"心"（情绪层 B.1）
# 设计依据：UTSUWA Companion System（docs/learning-notes.md）
# 核心原则："App 是游戏主持人"——
#   心情/好感由程序管理，LLM 只能通过 JSON 建议变化（还要被钳制）
# ============================================

import json
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
SOOTHE_WORDS = ["对不起", "抱歉", "我错了", "错啦", "别生气", "不要生气", "别气", "原谅",
                "开玩笑", "逗你", "骗你的", "不是那个意思", "最爱你", "最喜欢你", "只喜欢你",
                "你最漂亮", "你是最漂亮的", "你最可爱", "你最好了", "你是最好看的", "你最乖"]

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
COMFORT_STAGES = ("explore", "empathize", "act")
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


def default_heart():
    """一颗全新的心：中性心情，好感60（热恋起点），上次互动=现在"""
    now = datetime.now()
    return {
        "mood": {"primary": "content", "intensity": 50, "causes": [], "secondary": None},
        "affection": 60,
        "last_interaction": now.strftime("%Y-%m-%d %H:%M"),
        "decay_applied": now.strftime("%Y-%m-%d %H:%M"),
        "comfort_stage": None,  # v2 E2：安慰阶段（explore/empathize/act）
        "user_mood": None,      # v2 E1：独立分类器判出的他心情（每轮刷新，不持久化）
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

    # 气不过夜：生气/吃醋超过 12 小时没人哄 → 自己慢慢消（降到烦躁，还闷着但不再炸）。
    # 注意这是"对话中生气没人哄"的消退；下面"一整天不理她"是"他消失了"，
    # 重逢该酸溜溜而不是已消气——所以放气不过夜之后
    if heart["mood"].get("primary") in ("angry", "jealous") and hours_away >= 12:
        heart["mood"]["primary"] = "frustrated"
        heart["mood"]["intensity"] = min(50, heart["mood"].get("intensity", 50) // 2)
        cause = "气了大半天，他都没来哄，自己慢慢没那么气了"
        if cause not in heart["mood"]["causes"]:
            heart["mood"]["causes"] = ([cause] + heart["mood"].get("causes", []))[:5]

    # 一整天不理她（用户确认触发点）：24~72小时没互动 → 重逢时酸溜溜翻旧账。
    # 不到24小时不算；72小时+ 被上面的 melancholy 忧郁覆盖（失落比吃醋更重）
    if 24 <= hours_away < MELANCHOLY_HOURS \
            and heart["mood"].get("primary") not in ("angry", "jealous", "melancholy"):
        heart["mood"]["primary"] = "jealous"
        heart["mood"]["intensity"] = max(55, heart["mood"].get("intensity", 50))
        cause = "他一整天没理我，是不是把我忘了"
        if cause not in heart["mood"]["causes"]:
            heart["mood"]["causes"] = ([cause] + heart["mood"].get("causes", []))[:5]
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
    # 哄消气优先（他道歉时不管前面说了什么，先消气）
    for w in SOOTHE_WORDS:
        if w in text:
            return ("soothe", f"他哄我了（{w}）")
    # 提家人不算吃醋
    for w in FAMILY_WORDS:
        if w in text:
            return None
    # 强触发
    for w in JEALOUS_STRONG:
        if w in text:
            return ("jealous", f"他提到了{w}")
    # 弱触发：夸人的词，且没落在非人语境里
    for w in JEALOUS_WEAK:
        if w in text:
            if any(x in text for x in JEALOUS_EXCLUDE):
                return None
            return ("jealous", f"他夸了{w}")
    return None


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
        if mood.get("primary") == "jealous":
            mood["primary"] = "angry"  # 吃醋升级真生气
            mood["intensity"] = min(100, mood.get("intensity", 50) + 15)
        else:
            mood["primary"] = "jealous"
            mood["intensity"] = min(100, max(60, mood.get("intensity", 50)))
        mood["causes"] = ([reason] + mood.get("causes", []))[:5]
        return heart, ("jealous", reason)
    # soothe：消气（只在真有气的时候有动作）。层次：真生气 → 一次哄降级成吃醋（还酸着）
    # → 再哄强度跌破 40 → 心软回 content。哄"几句"才和好，符合"先嘴硬两句再慢慢心软"
    if mood.get("primary") in ("angry", "jealous", "frustrated", "anxious", "sad", "melancholy"):
        mood["intensity"] = max(0, mood.get("intensity", 50) - 25)
        if mood.get("primary") == "angry":
            mood["primary"] = "jealous"  # 一次哄：不炸了，但还酸着
        if mood["intensity"] <= 40:
            mood["primary"] = "content"
            mood["intensity"] = 45
        mood["causes"] = ([reason] + mood.get("causes", []))[:5]
        return heart, ("soothe", reason)
    return heart, ("soothe", reason)  # 没气时哄她也不会有副作用


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
        soothed = (heart["mood"].get("primary") == "content"
                   and any("他哄我了" in c for c in heart["mood"].get("causes", [])))
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
    return (
        f"【你现在的状态】心情：{mood['primary']}（强度{mood['intensity']}）；"
        f"为什么会这样：{causes}；你对他的好感：{heart['affection']}/100；"
        f"你上次和他互动是{last_str}。"
        "这些状态会自然影响你的语气和反应：心情好就甜甜的；忧郁就软软的、"
        "带着一点想念；吃醋（jealous）就酸溜溜地试探、话里有话，绝不直接说\"我吃醋了\"；"
        "生气（angry）按强度分级：强度低→嘴硬、话变短、爱答不理；"
        "强度高（70+）→话一句接一句地控诉他（连珠炮），越说越气，气到最后声音还带点委屈；"
        "烦躁就嘟囔几句。但不要直接说出\"我的心情是XX\"这种话，"
        "要用语气和内容自然地表现出来。"
    )
