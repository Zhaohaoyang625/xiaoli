# ============================================
# 小李的特殊日子（生日/节日）—— 升级版
# 1. 节日表：公历固定 + 农历（lunardate 库，装不上就只算公历）
# 2. 他的生日：聊天中提取 → data/birthday.json（单独存，不怕记忆老化忘记）
# 3. 每天最多主动触发一次（记在 data/special.json）
# 触发方式：挂进 proactive.Scheduler，跨天运行也会零点自动触发
# ============================================

import json
import os
from xiaoli import paths  # 统一路径（数据/模型在项目根）
import re
from datetime import datetime

SPECIAL_FILE = os.path.join(paths.DATA_DIR, "special.json")
BIRTHDAY_FILE = os.path.join(paths.DATA_DIR, "birthday.json")

# 不知道他生日时，每隔几天找机会主动问一次（像真女朋友想了解你）
ASK_INTERVAL_DAYS = 3

# 公历固定节日：month, day -> 名称
SOLAR_HOLIDAYS = {
    (1, 1): "元旦",
    (2, 14): "情人节",
    (5, 20): "520表白日",
    (6, 1): "儿童节",
    (10, 1): "国庆节",
    (12, 24): "平安夜",
    (12, 25): "圣诞节",
    (12, 31): "跨年夜",
}

# 农历节日：农历 month, day -> 名称（lunardate 库计算，见 _lunar_today）
LUNAR_HOLIDAYS = {
    (1, 1): "春节",
    (1, 15): "元宵节",
    (7, 7): "七夕",
    (8, 15): "中秋节",
    (9, 9): "重阳节",
}


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


# ---------- 他的生日 ----------

def load_birthday():
    """返回 {'month': 9, 'day': 15} 或 None（没记住）"""
    b = _load_json(BIRTHDAY_FILE, None)
    if b and isinstance(b.get("month"), int) and isinstance(b.get("day"), int):
        return b
    return None


def save_birthday(month, day):
    _save_json(BIRTHDAY_FILE, {"month": month, "day": day})


# 匹配"生日是9月15号 / 生日在12月3日 / 我生日：6月28号"
_BIRTHDAY_RE = re.compile(
    r"生日(?:是|在|为|：|:)?\s*(?:(\d{4})年)?(\d{1,2})[月\-/](\d{1,2})[日号]?"
)


def extract_birthday(text):
    """从一句话里找生日：'我生日是9月15号' → (9, 15)。
    必须是"生日"关键词打头（防"12月要去考试"这类误报）。返回 None"""
    for m in _BIRTHDAY_RE.finditer(text):
        try:
            month, day = int(m.group(2)), int(m.group(3))
        except ValueError:
            continue
        if 1 <= month <= 12 and 1 <= day <= 31:
            return month, day
    return None


# ---------- 今天是什么日子 ----------

def _lunar_today(now):
    """今天的农历 (month, day)。lunardate 没装 → 返回 None（跳过农历节日）"""
    try:
        from lunardate import LunarDate
        lunar = LunarDate.from_solar_date(now.year, now.month, now.day)
        return (lunar.month, lunar.day)
    except ImportError:
        return None


def check_today(now=None):
    """今天是什么特殊日子 → 返回给 LLM 的描述字符串；没有则 None。
    生日优先（他是你的一切），节日照常报，两个撞一起就都报"""
    if now is None:
        now = datetime.now()
    parts = []
    # ① 他的生日（最优先，且报出日期让她说出"生日快乐"）
    b = load_birthday()
    if b and (b["month"], b["day"]) == (now.month, now.day):
        parts.append(f"今天是他生日（{b['month']}月{b['day']}日）")
    # ② 公历节日
    name = SOLAR_HOLIDAYS.get((now.month, now.day))
    if name:
        parts.append(f"今天是{name}")
    # ③ 农历节日
    lunar = _lunar_today(now)
    if lunar:
        name = LUNAR_HOLIDAYS.get(lunar)
        if name:
            parts.append(f"今天是{name}（农历）")
    return "，也是".join(parts) if parts else None


# ---------- 她主动问你的生日（生日未知时的自然获取方式） ----------

def should_ask_birthday(now=None):
    """还不知道你生日 + 距离上次问 ≥3天 → 该找机会问你了。
    返回 True 后由调度器触发，她会在主动找你时自然地问出来"""
    if now is None:
        now = datetime.now()
    if load_birthday():
        return False  # 已经知道了，不用问
    data = _load_json(SPECIAL_FILE, {})
    last = data.get("last_ask")
    if not last:
        return True  # 从来没问过
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d")
    except ValueError:
        return True
    return (now.date() - last_dt.date()).days >= ASK_INTERVAL_DAYS


def mark_asked_birthday(now=None):
    if now is None:
        now = datetime.now()
    data = _load_json(SPECIAL_FILE, {})
    data["last_ask"] = now.strftime("%Y-%m-%d")
    _save_json(SPECIAL_FILE, data)


# ---------- 每天只触发一次 ----------

def should_fire_today(now=None):
    if now is None:
        now = datetime.now()
    data = _load_json(SPECIAL_FILE, {})
    return data.get("fired") != now.strftime("%Y-%m-%d")


def mark_fired_today(now=None):
    if now is None:
        now = datetime.now()
    data = _load_json(SPECIAL_FILE, {})
    data["fired"] = now.strftime("%Y-%m-%d")
    _save_json(SPECIAL_FILE, data)


if __name__ == "__main__":
    # 自测
    from datetime import date
    print("今天：", check_today())
    print("生日提取测试：")
    for s in ["我生日是9月15号", "我的生日在12月3日", "人家生日：6月28号",
              "12月我要去考试", "他生日是2001年2月14日", "生日快乐"]:
        print(f"  「{s}」→", extract_birthday(s))
