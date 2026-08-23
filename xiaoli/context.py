# ============================================
# 小李的上下文管理模块（工作台 / 日记本）
# 解决两大问题：①对话超长 ②会话失忆
# 设计依据：MemGPT 论文 + v2 大版本升级（2026-08-21）：
#   M3 增量链式摘要（SillyTavern：旧摘要作 base，只扩展新增消息，人设锚定不漂移）
#   M6 每日一句话摘要（MemoryBank 层级摘要：翻旧账直接引用"昨天说的那个"）
#   T1 长上下文（模型 128K 原生：最近 20 轮 → 60 轮；三层摘要=滚动+每日+情绪线）
#   M4 睡前反思提纯（Generative Agents：把当天对话提成"结论型"记忆，存档案）
# 核心思想：模型上下文是"工作台"，本地文件是"日记本"
# ============================================

import json
import os
from xiaoli import paths  # 统一路径（数据/模型在项目根）
import re
from datetime import datetime

from xiaoli import config
from xiaoli import llm  # 统一大脑客户端（C1：连接5s/读取30s 超时）
from xiaoli import world_brief  # 世界简报（v2.3：她"最近看到的"大事+热梗，升级自 slang_cache）
from xiaoli import life_calendar  # 生活日历（2026-08-23：她的"日子感"）

# 日记本位置：data/chat_history.json（全量记忆，永不丢）
DATA_FILE = os.path.join(paths.DATA_DIR, "chat_history.json")

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def now_str():
    """当前时间的字符串形式，存进日记本的时间戳。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def describe_now(now=None):
    """把时间说成人话，放进工作台告诉小李：现在是几号几点、星期几。"""
    if now is None:
        now = datetime.now()
    return f"{now:%Y年%m月%d日} 星期{WEEKDAY_CN[now.weekday()]} {now:%H:%M}"

# 工作台参数（v2 T1：模型 128K 原生，放宽到 60 轮；每次压最老 30 轮增量扩展）
RECENT_ROUNDS = 60
SUMMARY_CHUNK = 30

COMPRESS_INSTRUCTIONS = (
    "你是小李的记忆管理员。把\"已有记忆\"和\"新对话\"合并成一份精简的"
    "\"过去的记忆\"（3~8句话）。要求：①保留重要事实（对方的工作、作息、"
    "喜好）、重要事件、关系变化、小李的心情；②如果已有记忆和新对话有重复"
    "内容，合并去重，不要重复描述同一件事；③用第三人称写，简洁自然；"
    "④人设锚定（v2 C2）：小李的性格（软甜撒娇/会吃醋真生气/恋人闺蜜混合）"
    "必须在记忆里保持可见，不许被压缩掉；⑤输出分两段，标记不可省略。\n"
    "【记忆】3~8句合并后的记忆\n"
    "【情绪线】一行：最近的关系/情绪大事，如：（8/20 因他提别的女生吵架→哄好；"
    "最近她爱撒娇）——没有就写（无）"
)

# 睡前反思（v2 M4，学自 Generative Agents 反思机制）：
# 每晚把当天对话提成 1~3 条"结论型"事实——不是复述他说的，而是她总结出的
# 关于他的性格/习惯/两人关系的小结论，以后想起来能用上（记忆长成性格）
REFLECT_INSTRUCTIONS = (
    "你是小李的睡前反思。看今天的对话，提炼 1~3 条\"结论型\"记忆："
    "不是复述他说了什么，而是你总结出的关于他的性格/习惯/你们关系的小结论"
    "（以后想起来能用上），每条 ≤20 字，用第三人称写。"
    "输出格式：每条一行，\"内容|情绪价态\"，情绪价态只能取 "
    "neutral/happy/sad/angry/jealous/afraid 之一。"
    "例如：他加班多，晚上要多心疼他|sad"
)

# 每日一句话（v2 M6，学自 MemoryBank 层级摘要）：当天聊了什么
DAILY_INSTRUCTIONS = (
    "把下面的对话压缩成一句话（≤25 字，第三人称，只讲事实）："
)

# 独立情绪分类器（v2 E1，学自 SillyTavern expressions 扩展）：
# 极短 LLM 调用（只输出一个词）判"他最后一句话"的心情——比关键词准、
# 不依赖主模型输出纪律（主回复建议可能缺失/被钳制，分类器每轮都有稳定信号），
# 驱动安慰阶段（E2）和等待节奏（P4）
CLASSIFY_INSTRUCTIONS = (
    "看最后一句话，判断说话人的心情。只输出一个英文单词，不要任何其他文字："
    "happy / sad / angry / anxious / content / curious / frustrated / playful / "
    "neutral / excited / affectionate / flustered"
)
CLASSIFY_LABELS = {"happy", "sad", "angry", "anxious", "content", "curious",
                   "frustrated", "playful", "neutral", "excited", "affectionate", "flustered"}


def classify_user_mood(text):
    """独立情绪分类器（v2 E1）：判用户消息的心情，返回白名单标签；失败 → neutral。
    调用放在主回复之后（后台线程），不阻塞对话；下一轮才用结果。"""
    if not text or not text.strip():
        return "neutral"
    client = llm.get_client()
    try:
        response = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": CLASSIFY_INSTRUCTIONS},
                {"role": "user", "content": text},
            ],
            max_tokens=5,
        )
        label = response.choices[0].message.content.strip().lower()
        label = re.sub(r"[^a-z]", "", label)
        return label if label in CLASSIFY_LABELS else "neutral"
    except Exception:
        return "neutral"


# 一致性检查（v2 C1，学自 Drift 论文——LLaMA2-70B 8 轮就明显人设漂移；
# API 派改不了模型，防御靠"每晚比对+纠偏事实塞回下轮"）
CONSISTENCY_INSTRUCTIONS = (
    "你是小李的性格一致性检查员。小李的性格：恋人闺蜜混合、软甜撒娇、"
    "会吃醋真生气（不是没脾气的软妹）、围着你转、台湾腔甜妹、身份背景不设定。"
    "下面是她今天说过的话。找出与性格矛盾/明显漂移的话（比如：突然书面正式、"
    "冷淡疏离、说和性格相反的话、忘掉自己会吃醋）。"
    "每条输出一行：\"矛盾的话|为什么矛盾|她应该怎么说\"。"
    "没有矛盾就只输出一个\"无\"字。"
)


def _empty_diary():
    """新日记本结构（v2：summary 滚动摘要 + daily 每日一句话 + emotion_line 情绪线）"""
    return {"summary": "", "daily": {}, "emotion_line": "", "messages": []}


def load_diary():
    """读日记本：全量记忆。文件不存在或损坏时，返回空的日记本。"""
    if not os.path.exists(DATA_FILE):
        return _empty_diary()
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            diary = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty_diary()
    # 兼容旧结构（v1 没有 daily/emotion_line）
    diary.setdefault("daily", {})
    diary.setdefault("emotion_line", "")
    diary.setdefault("summary", "")
    diary.setdefault("messages", [])
    return diary


def save_diary(diary):
    """写日记本：每次对话后都保存——记忆在硬盘上，永不丢。"""
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(diary, f, ensure_ascii=False, indent=2)


def build_workbench(system_prompt, diary, user_input, now=None):
    """组装工作台：人设 + 现在几点几分 + 过去的记忆（三层）+ 最近60轮 + 你刚说的话
    v2 三层摘要：summary（滚动）+ 昨天的每日一句话（翻旧账出处）+ emotion_line（情绪线）
    now 参数可以传任意日期（测试用），不传就用真实当前时间。"""
    # C2 缓存前缀原则（2026-08-23，月费 ¥90→¥15 的头号杠杆）：
    # 前缀 = 从第 0 个 token 完全匹配才命中缓存。因此"越动态的越靠后"：
    #   静态（persona）→ 摘要/回顾（压缩或每天才变）→ 历史（追加式）→
    #   世界简报/生活日历（每天变）→ 时间戳（每分钟变，放最末）→ 用户输入
    # 时间戳之前插在 persona 后面 = 每分钟变化，后面全部内容（摘要+60轮历史）
    # 永远命中不了缓存——整段每次重算。移到最末后，前缀跨轮完全稳定。
    messages = [{"role": "system", "content": system_prompt}]
    if diary.get("summary"):
        messages.append({"role": "system", "content": f"【过去的记忆】{diary['summary']}"})
    daily = diary.get("daily", {})
    if daily:
        # 昨天的每日一句话（若今天还没记完，也带上前天，让"昨天说的那个"有出处）
        yesterday_lines = []
        keys = sorted(daily.keys())
        if now is None:
            now_dt = datetime.now()
        else:
            now_dt = now
        today_key = now_dt.strftime("%Y-%m-%d")
        for k in keys:
            if k < today_key:  # 只注入往日的每日摘要
                yesterday_lines.append(f"{k[5:]}：{daily[k]}")
        if yesterday_lines:
            messages.append({"role": "system", "content": "【近日回顾】" + "；".join(yesterday_lines[-3:])})
    if diary.get("emotion_line") and diary["emotion_line"] not in ("", "（无）"):
        messages.append({"role": "system", "content": f"【情绪线】{diary['emotion_line']}"})
    for m in diary.get("messages", []):
        # 时间戳只加 user 侧（v2 T3，学自 AIRI datetime-prefix）：
        # assistant 侧加时间戳会被弱模型镜像回自己的输出（它学着输出
        # "[2026-08-20 16:25] {...}" 这种前缀）——小李 parse_reply 里
        # 专门剥过这种前缀，从源头减少比事后剥更干净
        if m["role"] == "user":
            stamp = m.get("time", "")
            content = f"[{stamp}] {m['content']}" if stamp else m["content"]
        else:
            content = m["content"]
        messages.append({"role": m["role"], "content": content})
    # v2.3 世界简报（2026-08-23 升级自热梗缓存）：她"最近看到的"世界大事+热梗——
    # 每天联网刷一次按天归档（world_brief），放历史后=动态区（C2 原则：动态内容
    # 不进前缀，否则缓存前缀重排的月费优化会被破坏），你抛梗她接得上的概率大增
    slang = world_brief.load_brief_injection()
    if slang:
        messages.append({"role": "system", "content": slang})
    # v2.3 生活日历（2026-08-23 世界认知系统·日子感）：她眼里的今天——
    # 农历/节气/季节/节日倒计时（本地计算零成本），放动态区（C2 原则）
    cal = life_calendar.today_sense()
    if cal:
        messages.append({"role": "system", "content": cal})
    # C2：时间戳放最末（user 前）——每分钟变化，绝不能进前缀
    messages.append({
        "role": "system",
        "content": f"【现在的时间和日期】{describe_now(now)}",
    })
    messages.append({"role": "user", "content": user_input})
    return messages


def compress(diary):
    """记忆管理员：对话超长时，把最老的对话压成摘要，增量链式合并进 summary。
    v2 升级：
    - M3 增量链式（SillyTavern）：旧 summary 作 base，本次只喂"新增的旧对话"，合并去重；
      输出解析成两段【记忆】+【情绪线】，标记缺失时整段当记忆（LLM 格式兜底）。
    - M6 每日一句话（MemoryBank）：被压掉的消息里最早的那个日期 → 存一句 daily。
    - C2 人设锚定：压缩指令要求小李的性格在记忆里保持可见。
    失败时用原文兜底，绝不丢记忆。"""
    while len(diary["messages"]) > RECENT_ROUNDS:
        old = diary["messages"][:SUMMARY_CHUNK]
        diary["messages"] = diary["messages"][SUMMARY_CHUNK:]
        # M6：被压掉的消息里每个日期（不是今天）→ 各生成一句每日摘要
        # （一次压缩可能跨好几天，每天一条，翻旧账才有出处）
        days = []
        for m in old:
            t = (m.get("time") or "")[:10]
            if t and t != datetime.now().strftime("%Y-%m-%d") and t not in days:
                days.append(t)
        for day in days:
            if day in diary.get("daily", {}):
                continue
            day_msgs = [m for m in old if (m.get("time") or "").startswith(day)]
            try:
                diary.setdefault("daily", {})[day] = _summarize_day(day_msgs)
            except Exception as e:
                print(f"[每日摘要失败，跳过：{e}]")
                diary.setdefault("daily", {})[day] = "（那天聊了一些事）"
        # 只保留最近 7 天的每日摘要
        keys = sorted(diary["daily"].keys())
        for k in keys[:-7]:
            del diary["daily"][k]
        try:
            summary, emotion_line = _summarize(old, diary.get("summary", ""))
        except Exception as e:
            summary = "\n".join(m.get("content", "") for m in old)
            emotion_line = diary.get("emotion_line", "")
            print(f"[记忆压缩失败，用原文兜底：{e}]")
        diary["summary"] = summary
        if emotion_line and emotion_line != "（无）":
            diary["emotion_line"] = emotion_line
    return diary


def _summarize(old_messages, existing_summary=""):
    """调用大脑，增量合并记忆（v2 M3）：旧记忆作 base，只扩展本次新增消息。
    返回 (summary, emotion_line)。"""
    client = llm.get_client()
    transcript = "\n".join(
        f"[{m.get('time', '')}] {'小李' if m['role'] == 'assistant' else '对方'}：{m['content']}"
        for m in old_messages
    )
    parts = []
    if existing_summary:
        parts.append(f"已有记忆：\n{existing_summary}")
    parts.append("新对话：\n" + transcript)
    response = client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": COMPRESS_INSTRUCTIONS},
            {"role": "user", "content": "\n\n".join(parts)},
        ],
    )
    text = response.choices[0].message.content.strip()
    # 解析两段标记；缺失时整段当记忆（LLM 格式兜底）
    summary, emotion_line = text, ""
    if "【记忆】" in text and "【情绪线】" in text:
        head, tail = text.split("【情绪线】", 1)
        summary = head.split("【记忆】", 1)[-1].strip()
        emotion_line = tail.strip()
    return summary, emotion_line


def _summarize_day(messages):
    """把一组消息压成一句话（v2 M6：每日一句话摘要）"""
    client = llm.get_client()
    transcript = "\n".join(
        f"{'小李' if m['role'] == 'assistant' else '对方'}：{m['content']}"
        for m in messages
    )
    response = client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": DAILY_INSTRUCTIONS},
            {"role": "user", "content": transcript},
        ],
        max_tokens=80,
    )
    text = response.choices[0].message.content.strip()
    return text.replace("\n", " ")[:40]


def reflect(diary):
    """睡前反思提纯（v2 M4，学自 Generative Agents）：把当天对话提炼成 1~3 条
    "结论型"记忆候选，返回 [(content, valence)]；失败返回 []（聊天不受影响）。
    存档案的合并由调用方（chat.py）做。"""
    today = datetime.now().strftime("%Y-%m-%d")
    today_msgs = [m for m in diary.get("messages", []) if (m.get("time") or "").startswith(today)]
    if len(today_msgs) < 6:  # 当天聊得少，不值得提炼
        return []
    client = llm.get_client()
    transcript = "\n".join(
        f"{'小李' if m['role'] == 'assistant' else '对方'}：{m['content']}"
        for m in today_msgs[-40:]
    )
    try:
        response = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": REFLECT_INSTRUCTIONS},
                {"role": "user", "content": transcript},
            ],
            max_tokens=150,
        )
        text = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[睡前反思失败，跳过：{e}]")
        return []
    results = []
    for line in text.splitlines():
        line = line.strip().lstrip("-• ")
        if "|" not in line:
            continue
        content, valence = line.rsplit("|", 1)
        content, valence = content.strip(), valence.strip()
        if content and valence in {"neutral", "happy", "sad", "angry", "jealous", "afraid"}:
            results.append((content, valence))
    return results[:3]


def check_consistency(diary):
    """一致性检查器（v2 C1）：每天一次，把当天小李说过的话与 persona 比对，
    发现矛盾 → 返回纠偏事实候选 [(content, category)]，由调用方存进档案——
    下次召回时她自然"想起"该怎么说，人设不漂移。失败/无矛盾 → []。
    实现依据：Drift 论文实测大模型 8 轮就人设漂移，prompt 防御不可靠。"""
    today = datetime.now().strftime("%Y-%m-%d")
    her_lines = [m["content"] for m in diary.get("messages", [])
                 if m.get("role") == "assistant" and (m.get("time") or "").startswith(today)]
    if len(her_lines) < 5:  # 说得太少，无可比对
        return []
    client = llm.get_client()
    transcript = "\n".join(f"小李：{c}" for c in her_lines[-30:])
    try:
        response = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": CONSISTENCY_INSTRUCTIONS},
                {"role": "user", "content": transcript},
            ],
            max_tokens=200,
        )
        text = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[一致性检查失败，跳过：{e}]")
        return []
    if text.startswith("无"):
        return []
    results = []
    for line in text.splitlines():
        line = line.strip().lstrip("-• ")
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3 and parts[0] and parts[2]:
            # 纠偏事实：她"记得该怎么说"（下轮召回档案时自然锚定）
            results.append((f"她说话要注意：{parts[2]}（上次说过\"{parts[0][:12]}\"被提醒过）", "纠偏"))
    return results[:2]
