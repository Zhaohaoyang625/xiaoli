# ============================================
# 生活日历（2026-08-23 世界认知系统·第一件：她的"日子感"）
#
# 为什么需要它：真人知道的不只是"8月23日 星期日"——她还知道农历、
# 节气、季节、快过什么节，所以会自然冒出"齁，快中秋了啦""入秋了要喝热水"。
# 生活日历把这层"日子感"喂给她：纯本地计算，零成本、零延迟、零联网。
#
# 设计（学自 lunar-python 6tail 3.7k star 纯 Python 中文历法标准库）：
#   - 农历日期：台湾人生活里农历很重要（七夕/中秋都看农历）
#   - 节气：只有当天是节气日才提（真人不会天天念叨节气）
#   - 季节：按月划分（气象学 3-5 春/6-8 夏/9-11 秋/12-2 冬）
#   - 节日倒计时：未来 14 天内的节日（复用 special.py 的节日表，
#     与主动事件同一张表不重复不漏；他生日 ≤14 天才提）
#   - 注入位置：工作台动态区（历史后、用户前——C2 缓存前缀原则）
#   - 失败/无数据 → 返回空串，主流程不受影响
# ============================================

from datetime import datetime, timedelta

from xiaoli import special

# 注入块标题（与"【她最近刷到的】"同风格：事实块，她看完自己组织语言）
BLOCK_TITLE = "【她眼里的今天】"

# 季节（按月，气象学划分）
_SEASON_CN = {
    1: "冬天", 2: "冬天", 3: "春天", 4: "春天", 5: "春天",
    6: "夏天", 7: "夏天", 8: "夏天", 9: "秋天", 10: "秋天",
    11: "秋天", 12: "冬天",
}

# 节日预告窗口（天）：未来多少天内的节日她"知道快到了"
COUNTDOWN_WINDOW = 14

_WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def _lunar_text(dt):
    """农历月日的人话（如"七月十一"）；历法库不可用 → ""（静默降级）"""
    try:
        from lunar_python import Lunar
        lunar = Lunar.fromDate(dt)
        return f"农历{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}"
    except Exception:
        return ""


def _jieqi_of(dt):
    """那天是否节气日；是 → 节气名（"处暑"），不是/失败 → "" """
    try:
        from lunar_python import Lunar
        return Lunar.fromDate(dt).getJieQi() or ""
    except Exception:
        return ""


def _lunar_month_day(dt):
    """那天的农历 (月, 日) 数字形式（查节日表用）；失败 → None"""
    try:
        from lunar_python import Lunar
        lunar = Lunar.fromDate(dt)
        return (lunar.getMonth(), lunar.getDay())
    except Exception:
        return None


def _countdown_events(dt):
    """未来 COUNTDOWN_WINDOW 天内的节日/节气 → [(天数, 名称, 那天)]。
    公历节日直接查表；农历节日需要历法库换算；未来 1 天以上的节气也算预告。"""
    events = []
    for offset in range(0, COUNTDOWN_WINDOW + 1):
        day = dt + timedelta(days=offset)
        name = special.SOLAR_HOLIDAYS.get((day.month, day.day))
        if name is None:
            md = _lunar_month_day(day)
            if md is not None:
                name = special.LUNAR_HOLIDAYS.get(md)
        if name is None and offset > 0:
            name = _jieqi_of(day)  # 未来的节气（今天的不算，今天由 _jieqi_of 单独提）
        if name:
            events.append((offset, name, day))
    return events


def _birthday_countdown(dt):
    """他生日距今天数（未来 14 天内 → 天数；没记住/太远 → None）"""
    b = special.load_birthday()
    if not b:
        return None
    try:
        bday = datetime(dt.year, b["month"], b["day"]).date()
        if bday < dt.date():
            bday = datetime(dt.year + 1, b["month"], b["day"]).date()
    except ValueError:
        return None
    # 数日历格（去时间分量）：今天到生日隔几个自然日，真人这么算
    days = (bday - dt.date()).days
    return days if 0 <= days <= COUNTDOWN_WINDOW else None


def _day_suffix(offset, name, day):
    """节日倒计时的人话（今天/明天/再过 N 天 + 日期星期）"""
    when = {0: "今天就是", 1: "明天就是"}.get(offset, f"再过 {offset} 天是")
    return f"{when}{name}（{day:%m月%d日} 星期{_WEEKDAY_CN[day.weekday()]}）。"


def today_sense(now=None):
    """她眼里的今天：农历+季节+节气+节日倒计时，拼成注入片段。
    历法不可用 → 只出季节+节日（降级）；全失败 → ""（不注入）。"""
    dt = now or datetime.now()
    parts = []
    # 日子基调：农历 + 季节 + （节气日时）节气
    base = []
    lunar = _lunar_text(dt)
    if lunar:
        base.append(lunar)
    season = _SEASON_CN.get(dt.month, "")
    if season:
        base.append(f"现在是{season}")
    jq = _jieqi_of(dt)
    if jq and jq not in special.LUNAR_HOLIDAYS.values():
        base.append(f"今天正好是{jq}")
    if base:
        parts.append("，".join(base) + "。")
    # 节日/节气倒计时
    for offset, name, day in _countdown_events(dt):
        parts.append(_day_suffix(offset, name, day))
    # 他生日
    bd = _birthday_countdown(dt)
    if bd == 0:
        parts.append("今天是他生日！")
    elif bd == 1:
        parts.append("明天是他生日！")
    elif bd is not None:
        parts.append(f"再过 {bd} 天是他生日。")
    if not parts:
        return ""
    return BLOCK_TITLE + " ".join(parts)
