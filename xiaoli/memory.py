# ============================================
# 小李的档案记忆层（B.3）
# 设计依据：
#   1. MISS MemoryScorer —— 记忆有 importance/confidence，本地评分不花API钱
#   2. MISS 记忆老化 —— 重要度随时间衰减，衰减到0删除
#   3. UTSUWA 三层记忆 —— 贴身记忆(人设) / 档案记忆(这里) / 聊天记忆(diary)
# 原则：记忆在硬盘；提取靠 LLM+本地正则双通道；检索用本地关键词（向量留待以后）
# ============================================

import json
import math
import os
from xiaoli import paths  # 统一路径（数据/模型在项目根）
import re
import uuid
from datetime import datetime, timedelta

MEMORY_FILE = os.path.join(paths.DATA_DIR, "facts.json")

# 本地提取模式（兜底：LLM 没提取时，程序自己抓）
# 注意：匹配到标点为止，避免吞掉整句话；存的是完整片段（"我"开头 → 存成"他"）
_LOCAL_PATTERNS = [
    (r"我(?:叫|是)(.{1,20}?)(?:，|,|。|！|？|$)", "身份"),
    (r"我(?:住在|家住)(.{1,20}?)(?:，|,|。|！|？|$)", "生活"),
    (r"我(?:最喜欢|最爱|超爱|很爱|喜欢)(.{1,20}?)(?:，|,|。|！|？|$)", "喜好"),
    (r"我(?:讨厌|不喜欢|最怕)(.{1,20}?)(?:，|,|。|！|？|$)", "喜恶"),
    (r"我(?:生日|过生日)(?:是|在)?(.{1,20}?)(?:，|,|。|！|？|$)", "个人信息"),
    (r"我在(.{1,20}?)(?:工作|上班|上学)", "工作学习"),
    (r"我(?:每天|一般|平时)(.{1,20}?)(?:，|,|。|！|？|$)", "习惯"),
]

# 去重时忽略的"噪音"事实（太泛泛的不记）
_NOISE = {"", "嗯", "好", "好的", "恩", "嗯嗯", "啊", "哦"}


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


def load_facts():
    return _load_json(MEMORY_FILE, [])


def save_facts(facts):
    _save_json(MEMORY_FILE, facts)


# ---------- 提取 ----------

def extract_facts_local(text):
    """本地正则兜底提取（学自 MISS：本地评分不花API钱）。
    返回 [(content, category)]，LLM 没提取时程序自己抓。
    例："我喜欢喝奶茶" → ("他喜欢喝奶茶", "喜好")"""
    found = []
    for pattern, category in _LOCAL_PATTERNS:
        for m in re.finditer(pattern, text):
            # group(0) 可能带结尾标点（匹配里的分隔符），去掉
            content = re.sub(r"^我", "他", m.group(0)).strip().rstrip("，,。！？")
            if content not in _NOISE:
                found.append((content, category))
    # 去重
    seen = set()
    result = []
    for content, category in found:
        if content not in seen:
            seen.add(content)
            result.append((content, category))
    return result


# 高情绪价态（v2 M5 躯体标记，学自 MATE）：
# 记下时带强烈情绪的回忆，忘得更慢（半衰期 ×1.5），召回时带出"当时的感受"
HIGH_EMOTION_VALENCES = {"angry", "jealous", "sad", "afraid", "happy", "excited"}
_VALENCE_TEXT = {
    "angry": "当时的生气", "jealous": "当时的酸劲", "sad": "当时的心疼",
    "happy": "当时的开心", "excited": "当时的激动", "afraid": "当时的担心",
}


def merge_fact(facts, content, importance=5, category="其他", confidence=0.8, valence="neutral"):
    """存一条事实；已存在同内容 → 更新重要度（取更高）和时间戳（复习=加固）。
    v2 M5：valence 是"记下这条记忆时她的情绪"（躯体标记）——高情绪记忆衰减更慢"""
    for f in facts:
        if f["content"] == content:
            f["importance"] = max(f["importance"], importance)
            f["confidence"] = max(f["confidence"], confidence)
            f["lastRecalled"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return False  # 已存在，不是新记忆
    facts.append({
        "id": str(uuid.uuid4())[:8],
        "content": content,
        "category": category,
        "importance": min(10, max(1, importance)),
        "confidence": min(1.0, max(0.1, confidence)),
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "lastRecalled": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "valence": valence,
        "recallCount": 0,  # v2 记忆频率：被想起的累积次数（召回 +1）
    })
    return True


# ---------- 检索 ----------

def _bigrams(text):
    """中文字符二元组集合（本地检索用，不装分词库）"""
    chars = re.findall(r"[一-鿿]|\w+", text)
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


# 高频虚字：单字命中的加分不算它们
_COMMON_CHARS = set("的了是在我你他她它们就都有什么吗吧呢啊哦呀喔诶嗯好没去回")


def _char_features(text):
    """字符 unigram+bigram 特征（TF-IDF 向量用，保留重复计数）"""
    chars = re.findall(r"[一-鿿]", text)
    feats = list(chars)
    feats += [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    return feats


def _tfidf_cosine(a_text, b_text, idf):
    """零依赖字符 TF-IDF 余弦（稀疏 dict 点积）。无共同特征 → 0"""
    va = {}
    for f in _char_features(a_text):
        va[f] = va.get(f, 0) + 1
    vb = {}
    for f in _char_features(b_text):
        vb[f] = vb.get(f, 0) + 1
    common = set(va) & set(vb)
    if not common:
        return 0.0
    dot = sum(va[f] * idf.get(f, 1.0) * vb[f] * idf.get(f, 1.0) for f in common)
    na = math.sqrt(sum((v * idf.get(f, 1.0)) ** 2 for f, v in va.items()))
    nb = math.sqrt(sum((v * idf.get(f, 1.0)) ** 2 for f, v in vb.items()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _build_idf(facts):
    """从全部事实统计字符特征 idf（逆文档频率）：常见字权重低、专属词权重高"""
    dfs = {}
    n = 0
    for f in facts:
        content = f.get("content", "") if isinstance(f, dict) else str(f)
        n += 1
        for feat in set(_char_features(content)):
            dfs[feat] = dfs.get(feat, 0) + 1
    return {feat: math.log(n / (1 + df)) + 1 for feat, df in dfs.items()}


# bge 真向量缓存（facts 内容做 key，模型加载一次，推理结果复用）
_EMBED_CACHE = {}


def _fact_vec(fact_content):
    """事实内容的 bge 向量（缓存复用）。模型不可用 → None"""
    if fact_content in _EMBED_CACHE:
        return _EMBED_CACHE[fact_content]
    try:
        import embed
        vec = embed.embed(fact_content)
    except Exception:
        vec = None
    if len(_EMBED_CACHE) > 500:
        _EMBED_CACHE.clear()  # 防无限增长
    _EMBED_CACHE[fact_content] = vec
    return vec


def _score(fact_content, user_input, idf=None, input_vec=None):
    """事实与输入的相关度打分（v2 O3 升级：混合"字面 + 语义"）：
    共同 bigram ×2 + 共同有效单字 ×1（字面）；
    + 语义加分：bge 真向量余弦 ×2（模型可用时）> 字符 TF-IDF 余弦 ×2（兜底）> 0。
    语义加分的意义：他说"要去爬山"，她记得"他怕高"——无共同字符，
    但语义相关，能进候选。分数 ≥ 1 视为相关（候选线）。"""
    gram_hits = len(_bigrams(fact_content) & _bigrams(user_input))
    f_chars = set(re.findall(r"[一-鿿]", fact_content)) - _COMMON_CHARS
    u_chars = set(re.findall(r"[一-鿿]", user_input))
    char_hits = len(f_chars & u_chars)
    base = gram_hits * 2 + char_hits
    if input_vec is not None:
        v = _fact_vec(fact_content)
        if v is not None:
            try:
                import embed
                return base + embed.cosine(input_vec, v) * 2.0
            except Exception:
                pass
    if idf:
        return base + _tfidf_cosine(fact_content, user_input, idf) * 2.0
    return base


def _hours_since(dt_str, now):
    """从 dt_str 到现在的小时数（解析失败视为很久以前）"""
    try:
        last = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    except (ValueError, KeyError, TypeError):
        return 1e9
    return max(0.0, (now - last).total_seconds() / 3600.0)


def half_life_hours(fact):
    """记忆的半衰期（小时，v2 M2/M5/M7，学自 AIRI 无状态遗忘曲线 + MATE 躯体标记）：
    重要度决定忘得多快——importance 5 → 35 天，importance 10 → 70 天；
    高情绪价态的记忆 ×1.5（生气/吃醋/开心的事记得更久）；
    频率系数（v2 M7 间隔重复）：被想起过的次数越多 → 忘得越慢——
    每被召回 1 次半衰期 ×1.15，封顶 ×2（≈5 次后到顶）：
    "他常提的事"（爱吃烧烤、工作的事）自然记得更久，一次没提过的按原曲线。"""
    h = fact.get("importance", 5) * 7 * 24
    if fact.get("valence") in HIGH_EMOTION_VALENCES:
        h *= 1.5
    h *= min(2.0, 1.15 ** fact.get("recallCount", 0))
    return h


def recall(facts, user_input, top=5, now=None):
    """三信号加权召回（v2 M1，学自 Generative Agents 检索）：
    候选 = 相关度 ≥ 1（有任何共同点）；排序 = 0.4×重要度 + 0.3×新近度 + 0.3×相关度。
    新近度 = 半衰期指数衰减（v2 M2）：刚想起过 → 1.0，很久没提 → 趋近 0。
    召回回血（v2 M2）：排序用刷新前的时间戳（否则信号全变 1.0），
    回血只对真正返回的 top 条做。以后上向量时把相关度换成 cosine 即可，接口不变。"""
    if now is None:
        now = datetime.now()
    # O3 语义信号（每轮一次，几十条事实的向量化成本毫秒级）：
    # 模型可用 → bge 真向量（查询端加指令）；否则字符 TF-IDF；都没有 → 原逻辑
    idf = _build_idf(facts)
    input_vec = None
    try:
        import embed as _embed
        input_vec = _embed.embed(user_input, is_query=True)
    except Exception:
        input_vec = None
    hits = []
    for f in facts:
        rel = _score(f["content"], user_input, idf=idf, input_vec=input_vec)
        if rel >= 1:
            hits.append((f, rel))
    if not hits:
        return []

    def weighted(item):
        f, rel = item
        recency = math.exp(-_hours_since(f.get("lastRecalled", f.get("createdAt", "")), now)
                           / half_life_hours(f) * math.log(2))
        rel_n = min(rel, 10) / 10
        return 0.4 * (f.get("importance", 5) / 10) + 0.3 * recency + 0.3 * rel_n

    hits.sort(key=weighted, reverse=True)
    top_hits = [f for f, _rel in hits[:top]]
    stamp = now.strftime("%Y-%m-%d %H:%M")
    for f in top_hits:
        f["lastRecalled"] = stamp  # 召回回血
        # v2 M7 记忆频率：被想起 +1（累积"重复接触"，半衰期延长——间隔重复原理）
        f["recallCount"] = f.get("recallCount", 0) + 1
    return top_hits


# ---------- 遗忘（v2 M2 无状态半衰期，学自 AIRI） ----------

def decay(facts, now=None):
    """物理清理：超过 5 个半衰期没被想起（新鲜度衰减到约 3%）→ 遗忘删除。
    不再是"7 天一刀切 -1"——遗忘是渐进的（排序分随半衰期现算），
    物理删除只清"彻底淡出"的，高情绪记忆半衰期长、留得更久。"""
    if now is None:
        now = datetime.now()
    kept = []
    removed = 0
    for f in facts:
        last = f.get("lastRecalled") or f.get("createdAt") or ""
        if _hours_since(last, now) > half_life_hours(f) * 5:
            removed += 1
            continue  # 遗忘
        kept.append(f)
    return kept, removed


# ---------- 生成提示词块 ----------

def describe_facts(facts_subset):
    """把召回的档案记忆变成"你记得的事"提示块（v2 M5：高情绪记忆带出当时的感受）"""
    if not facts_subset:
        return ""
    lines = []
    for f in facts_subset:
        line = f"- {f['content']}（{f['category']}）"
        v = f.get("valence")
        if v in _VALENCE_TEXT:
            line += f"——想起这个，她还带着{_VALENCE_TEXT[v]}"
        lines.append(line)
    return "## 你记得的关于他的事（档案记忆，直接引用，别犹豫）\n" + "\n".join(lines)


# ---------- O5 纪念日/里程碑预告（v2 P2，2026-08-22） ----------
# 学自 Replika/Character.AI 的"纪念日提醒"：重要的日子她不该等你说才想起，
# 而是提前几天"惦记着"（7天→3天→1天→当天，递减临近）。真人：越近越念叨。
# 数据来源：档案里"在一起/纪念日/交往 + X月X日"式记忆（LLM 存的或本地正则提的）
# 分隔段用 [^0-9] 而不是 . ：贪婪的 . 会吞掉月份数字（实测 "10月1日" 被解析成
# "0月1日" → 非法日期被跳过 → 预告静默失效。这是 2026-08-22 修过的真 bug）
_ANNIVERSARY_RE = re.compile(r"(纪念日|在一起|交往|恋爱|周年|确立关系)[^0-9]{0,6}(\d{1,2})月(\d{1,2})日")


def find_anniversaries(facts, now=None):
    """扫档案里的日期型纪念，返回 [(内容, 天数差)]：
    - 差 0：今天就是纪念日
    - 差 1/3/7：临近预告窗口（真人提前一周开始惦记）
    不匹配日期/没有档案 → []（安静，不影响正常聊天）"""
    from datetime import datetime as _dt, date as _date
    today = (now or _dt.now()).date()
    hits = []
    for f in facts:
        content = f.get("content", "") if isinstance(f, dict) else ""
        m = _ANNIVERSARY_RE.search(content)
        if not m:
            continue
        month, day = int(m.group(2)), int(m.group(3))
        try:
            this_year = _date(today.year, month, day)
        except ValueError:
            continue  # 2月30日之类的不合法日期 → 跳过
        if this_year < today:
            this_year = _date(today.year + 1, month, day)  # 今年已过 → 明年的
        diff = (this_year - today).days
        if diff in (0, 1, 3, 7):
            hits.append((content, diff))
    return hits
