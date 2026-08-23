# ============================================
# 世界简报（2026-08-23 世界认知系统·完整版：她的"网络生活"）
# 升级自 slang_cache（热梗缓存 v2.2），回应用户"不只是热梗，还要热门事件"：
#   - 单文件覆盖 → 按天归档 data/world_briefs/2026-08-23.json：
#     跨天重复自然去重（同梗/同事件只取最新日期，学 mem0 supersede 覆盖思想）
#   - 内容双块：世界大事 events + 流行热梗 slang（一次联网，~3 分钱/天）
#   - 生命周期（学 RAG 时效论文 emergence→growth→decay）：>7 天不注入（过气）
#   - 注入策略：最新一天完整 + 前 2 天只列名字弱提及（每天 ≤800 字）
#   - 停机补刷三保险（她"几天没看手机"回来会补刷）：
#     ①启动即刷（proactive 后台线程）②对话前 ensure_fresh 同步补刷
#     （对话时她一定已刷到或正在刷）③调度器每日
#   - 24h 限频省钱；失败静默（不影响主流程）；线程安全（防并发双刷）
# ============================================

import json
import os
import threading
from datetime import datetime, timedelta

from xiaoli import config
from xiaoli import llm  # 统一大脑客户端（C1：连接5s/读取30s 超时）
from xiaoli import paths

BRIEF_DIR = os.path.join(paths.DATA_DIR, "world_briefs")
REFRESH_INTERVAL_HOURS = 24  # 24h 内刷过不再刷（省钱）
MAX_AGE_DAYS = 7             # 简报最长寿命：过气（论文四阶段 decay）
OLDER_WEAK_DAYS = 2          # 前 N 天只弱提及（名字级）
MAX_EVENTS = 5               # 大事个数
MAX_SLANG = 6                # 热梗个数
MAX_CHARS = 800              # 注入总长度上限

_lock = threading.Lock()     # 防 proactive 后台 + 对话前同步补刷并发双刷


def _now_str(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M")


def _brief_path(date_str):
    return os.path.join(BRIEF_DIR, f"{date_str}.json")


def _latest_brief(now=None):
    """最新一份简报 (date_str, data) 或 None。按文件名（日期）找最新，坏的跳过。"""
    if not os.path.isdir(BRIEF_DIR):
        return None
    try:
        dates = sorted(f[:-5] for f in os.listdir(BRIEF_DIR) if f.endswith(".json"))
    except OSError:
        return None
    while dates:
        d = dates[-1]
        try:
            with open(_brief_path(d), encoding="utf-8") as f:
                return d, json.load(f)
        except Exception:
            dates.pop()
    return None


def _is_fresh(now=None):
    """最新简报是否 24h 内刷过（不需要重新联网）"""
    now = now or datetime.now()
    lb = _latest_brief(now)
    if not lb:
        return False
    try:
        updated = datetime.strptime(lb[1].get("updated", "2000-01-01 00:00"),
                                    "%Y-%m-%d %H:%M")
        return now - updated < timedelta(hours=REFRESH_INTERVAL_HOURS)
    except Exception:
        return False


def _fetch_brief(now=None):
    """联网搜"最近的大事+热梗"，解析成 {date, updated, events, slang}。失败 → None。"""
    now = now or datetime.now()
    prompt = (
        f"今天是{now:%Y年%m月%d日}。请联网搜索后整理\"最近几天\"的真实信息，"
        "严格按 JSON 输出（不要多余文字）：\n"
        '{"events": [{"title": "事件名", "desc": "一句话说清楚（≤30字）"}], '
        '"slang": [{"phrase": "梗的典型说法/原句", "meaning": "一句话意思（≤20字）"}]}\n'
        f"要求：①events 是最近1~3天真实发生的大事（天气灾害/科技/社会/娱乐皆可，{MAX_EVENTS}个左右）"
        f"②slang 是最近正在流行或刚出现的网络热梗（{MAX_SLANG}个左右，保留典型原句——"
        "用户会拿原句来逗她）③只写真实搜到的，编造为零分"
    )
    try:
        client = llm.get_client()
        r = client.responses.create(
            model=config.DEEPSEEK_MODEL,
            input=[{"role": "user", "content": prompt}],
            tools=[{"type": "web_search"}],
            tool_choice={"type": "web_search"},
            text={"format": {"type": "json_object"}},
        )
        text = r.output_text or ""
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        obj = json.loads(text[start:end + 1])
        events = [{"title": str(e.get("title", "")).strip(),
                   "desc": str(e.get("desc", "")).strip()}
                  for e in obj.get("events", []) if e.get("title")]
        slang = [{"phrase": str(s.get("phrase", "")).strip(),
                  "meaning": str(s.get("meaning", "")).strip()}
                 for s in obj.get("slang", []) if s.get("phrase")]
        if not events and not slang:
            return None
        return {"date": now.strftime("%Y-%m-%d"),
                "updated": _now_str(now),
                "events": events[:MAX_EVENTS],
                "slang": slang[:MAX_SLANG]}
    except Exception:
        return None


def refresh_world_brief(now=None):
    """过期才联网刷新（24h 限频）。线程安全（正在刷时其他人等待复用结果）。
    成功 → 落盘当天文件；返回当前注入文本（可能空串）。"""
    with _lock:
        if _is_fresh(now):
            return load_brief_injection(now)
        data = _fetch_brief(now)
        if data:
            try:
                os.makedirs(BRIEF_DIR, exist_ok=True)
                with open(_brief_path(data["date"]), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except OSError:
                pass
    return load_brief_injection(now)


def _load_recent(now=None):
    """按日期倒序读 ≤MAX_AGE_DAYS 天的简报（含今天）。坏的跳过。"""
    now = now or datetime.now()
    if not os.path.isdir(BRIEF_DIR):
        return []
    try:
        dates = sorted(f[:-5] for f in os.listdir(BRIEF_DIR) if f.endswith(".json"))
    except OSError:
        return []
    result = []
    for d in dates:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            if (now - dt).days > MAX_AGE_DAYS:
                continue  # 过气：不注入（真人也不翻旧梗）
            with open(_brief_path(d), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                result.append((d, data))
        except Exception:
            continue
    result.sort(key=lambda x: x[0], reverse=True)
    return result


def load_brief_injection(now=None):
    """组装注入文本：最新一天完整 + 前 OLDER_WEAK_DAYS 天弱提及（名字级、去重）。
    无简报 → ""（不注入）。"""
    briefs = _load_recent(now)
    if not briefs:
        return ""
    parts = []
    # 最新一天：完整注入（字段可能缺失——LLM/手工数据不保证全，防御用 .get）
    _, latest = briefs[0]
    ev = [f"{e.get('title', '')}（{e.get('desc', '')}）" for e in latest.get("events", [])]
    sl = [f"{s.get('phrase', '')}={s.get('meaning', '')}" for s in latest.get("slang", [])]
    if ev or sl:
        block = "【她最近看到的】"
        if ev:
            block += "世界大事：" + "；".join(ev) + "。"
        if sl:
            block += "大家热聊：" + "；".join(sl) + "。"
        parts.append(block)
    # 前 OLDER_WEAK_DAYS 天：只列名字（弱提及），最新天出现过的跳过（去重）
    seen = {e.get("title", "") for e in latest.get("events", [])} \
        | {s.get("phrase", "") for s in latest.get("slang", [])}
    older_lines = []
    for d, b in briefs[1:1 + OLDER_WEAK_DAYS]:
        names = []
        for e in b.get("events", []):
            t = e.get("title", "")
            if t and t not in seen:
                names.append(t)
                seen.add(t)
        for s in b.get("slang", []):
            p = s.get("phrase", "")
            if p and p not in seen:
                names.append(p)
                seen.add(p)
        if names:
            older_lines.append(f"{d[5:]}：" + "、".join(names[:6]))
    if older_lines:
        parts.append("前几天她还看到过：" + "；".join(older_lines))
    out = " ".join(parts)
    return out[:MAX_CHARS]


def ensure_fresh(now=None):
    """对话前保证"她已刷到"：简报过期 → 同步补刷（首次对话慢几秒，之后 24h 秒过）。
    后台正在刷 → 等它刷完复用；失败 → 静默（旧简报兜底，对话不断）。"""
    if _is_fresh(now):
        return load_brief_injection(now)
    return refresh_world_brief(now)
