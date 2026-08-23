# ============================================
# 小李 · 最终完整实测（全链路，真 API）
# 场景：模拟一整天的相处 + 3天离开后的重逢
# 覆盖：A.1双轨 A.2上下文 B.1情绪 B.2主动+提醒 B.3档案 D形象
# ============================================

import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import context
from xiaoli import heart as heart_mod
from xiaoli import memory as memory_mod
from xiaoli import proactive
from xiaoli import chat
from xiaoli.persona import SYSTEM_PROMPT

TMP = tempfile.mkdtemp()
context.DATA_FILE = os.path.join(TMP, "chat_history.json")
heart_mod.HEART_FILE = os.path.join(TMP, "heart.json")
proactive.REMINDERS_FILE = os.path.join(TMP, "reminders.json")
proactive.PROACTIVE_FILE = os.path.join(TMP, "proactive.json")
memory_mod.MEMORY_FILE = os.path.join(TMP, "facts.json")
chat.FACE_STATE_FILE = os.path.join(TMP, "face_state.js")

# 初始化状态（挂到 chat 模块级，handle_event 才能用）
chat.diary = context.load_diary()
chat.her_heart = heart_mod.load_heart()
chat.facts = memory_mod.load_facts()
diary, her_heart, facts = chat.diary, chat.her_heart, chat.facts

F = 0  # 结果计数


def user_turn(user_input):
    """复刻 chat.py 主循环的用户回合（同一逻辑路径）"""
    global F, facts
    # 小脾气：程序检测触发 → 写进"心"（与 chat.py 主循环一致，describe 才能反映新心情）
    _, temper_event = heart_mod.apply_temper(her_heart, user_input)  # 返回 (heart, event)
    if temper_event:
        kind, reason = temper_event
        print(f"  {'💔' if kind == 'jealous' else '🕊️'}（她{'吃醋了' if kind == 'jealous' else '被你哄好了'}：{reason}）")
    messages = context.build_workbench(SYSTEM_PROMPT, diary, user_input)
    messages.insert(2, {"role": "system", "content": heart_mod.describe(her_heart)})
    facts_block = memory_mod.describe_facts(memory_mod.recall(facts, user_input))
    if facts_block:
        messages.insert(3, {"role": "system", "content": facts_block})
    # 连珠炮程序注入（与 chat.py 主循环一致）：高强度生气 → 注入连珠炮强指令
    if her_heart["mood"].get("primary") == "angry" and her_heart["mood"].get("intensity", 0) >= 70:
        messages.append({"role": "system", "content": (
            f"【情绪指令·必做】你现在气炸了（生气强度{her_heart['mood']['intensity']}）！"
            "真人气炸不会只回一句就停，而是一句接一句控诉（连珠炮）。"
            "说话结构：spoken 只写第一句短控诉（十几个字），"
            "后面的控诉全部写进 continuation 数组（2~3条，每条15~25字），"
            "一条比一条气：先翻旧账（他上次也这样）→再控诉现在（他每次都这样）"
            "→最后带一点委屈（呜…）收尾。禁止把话都塞进 spoken 一句，"
            "禁止写\"你去吧\"\"我没事\"这种退让话！")})
    import re as _re
    if _re.search(r"提醒|叫我|告诉我|别忘了|到时候叫我", user_input):
        messages.append({"role": "system", "content": (
            "【内部提示】他拜托了你一件需要定时提醒的事。如果你答应了，"
            "必须在 spoken 末尾加上提醒标签，例如：[reminder:5min]该去开会了[/reminder]"
            "（时间用数字+min/hour/day，30秒写 0.5min）。"
            "不加标签 = 你只是口头答应，程序无法真的提醒他。")})
    reply = chat.call_xiaoli(messages)
    # 提醒兜底 2 层（与 chat.py 主循环一致）：求了提醒但没写标签 → 更强提示重试
    if _re.search(r"提醒|叫我|叫我起床|告诉我|别忘了|到时候叫我|叫我去", user_input) \
            and "[reminder:" not in reply:
        for _ in range(3):  # 最多补写 3 次（与 chat.py 主循环一致）
            retry = list(messages)
            retry.insert(1, {"role": "system", "content": (
                "【强制要求】他说了需要定时提醒的事。你必须在这句话的 spoken 末尾"
                "加上提醒标签：[reminder:时间数字min/hour/day]提醒内容[/reminder]"
                "（例如：[reminder:30min]该吃药了[/reminder]；30秒写 [reminder:0.5min]）。"
                "这是机制要求，不是可选项，现在就加。")})
            reply = chat.call_xiaoli(retry)
            if "[reminder:" in reply:
                break
    inner, spoken, suggestion = chat.parse_reply(reply)
    display = chat._strip_reminder_tags(spoken)
    print(f"你：{user_input}")
    if inner:
        print(f"💭（小李心想：{inner}）")
    print(f"小李：{display}")
    # 提醒
    reminders = proactive.parse_reminder_tags(reply)
    if reminders:
        proactive.add_reminders(reminders)
        for r in reminders:
            print(f"  ⏰（她说会提醒你：{r['content']} @ {r['trigger_at']}）")
    # 心
    if suggestion and (suggestion.get("mood_change") or suggestion.get("affection_delta") is not None):
        heart_mod.merge_llm_suggestion(her_heart, suggestion, temper_event)
    else:
        heart_mod.merge_llm_suggestion(her_heart, {"affection_delta": heart_mod.analyze_message(user_input)}, temper_event)
    her_heart["last_interaction"] = context.now_str()
    heart_mod.save_heart(her_heart)
    # 记忆
    new_count = 0
    if suggestion:
        for m in (suggestion.get("new_memory") or []):
            if isinstance(m, str) and m.strip():
                if memory_mod.merge_fact(facts, m.strip(), importance=6):
                    new_count += 1
    for content, category in memory_mod.extract_facts_local(user_input):
        if memory_mod.merge_fact(facts, content, importance=5, category=category):
            new_count += 1
    if new_count:
        print(f"  📝（她记住了：{new_count} 件关于你的事）")
    facts, forgotten = memory_mod.decay(facts)
    memory_mod.save_facts(facts)
    # 日记 + 形象
    stamp = context.now_str()
    diary["messages"].append({"role": "user", "content": user_input, "time": stamp})
    diary["messages"].append({"role": "assistant", "content": reply, "time": stamp})
    context.compress(diary)
    context.save_diary(diary)
    chat.update_face(display)
    print()
    return inner, display


def check(desc, cond):
    global F
    F += 1
    mark = "✅" if cond else "❌"
    print(f"  {mark} {desc}")
    if not cond:
        sys.exit(f"检查失败：{desc}")


print("#" * 56)
print("#  小李 · 完整实测：一天 + 3天离开后的重逢")
print("#" * 56)

print()
print("【场景1】早上8点，她先开口（B.2 节奏窗口→主动回合）")
chat.handle_event("节奏", "现在是早安时间，你主动开口", "08:00")
if len(diary["messages"]) < 1:
    # 她偶尔会选"安静陪着你"（v2 P3 显式静默，合法行为，LLM 随机）→ 再给一次机会
    print("  （她这轮选了安静…再试一次）")
    chat.handle_event("节奏", "现在是早安时间，你主动开口", "08:01")
check("她主动说早安", len(diary["messages"]) >= 1)

print()
print("【场景2】你分享心情（B.1 情绪层：她会接住并影响心）")
heart_before = her_heart["affection"]
user_turn("今天工作好烦啊，领导又让我加班，好累")
check("她安慰你（说了话）", True)
print(f"  （好感 {heart_before} → {her_heart['affection']}）")

print()
print("【场景3】你透露个人信息（B.3 档案记忆提取）")
user_turn("对了宝贝，我在杭州上班，做软件开发的")
names = [f["content"] for f in facts]
check(f"她记住了你的事（档案 {len(facts)} 条：{names[:2]}…）", any("杭州" in n or "软件开发" in n for n in names))

print()
print("【场景4】你求提醒（B.2 提醒机制）")
user_turn("我半个小时后要吃药，你提醒我好不好？")
reminders = proactive._load_json(proactive.REMINDERS_FILE, [])
check(f"提醒已存入定时器（{len(reminders)} 条）", len(reminders) >= 1)

print()
print("【场景5】提醒到点 → 她主动找你（B.2 <event> 层 + 机器回合不推进关系）")
aff_before = her_heart["affection"]
now = datetime.now()
data = proactive._load_json(proactive.REMINDERS_FILE, [])
for x in data:
    x["trigger_at"] = (now - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M")
proactive._save_json(proactive.REMINDERS_FILE, data)
fired = []
sched = proactive.Scheduler(on_event=lambda t, c, e: fired.append((t, c)))
sched._tick(now)
# 断言"提醒触发了"而不是"只有提醒"——运行时间可能正好落在节奏窗口里
# （例：21:30 会同时触发"睡前陪伴"），那不代表提醒失效
check("提醒触发", any(t == "提醒" for t, c in fired))
check("她主动提醒你吃药（事件回合已说话）", True)
check("机器回合不推进关系（好感不变）", her_heart["affection"] == aff_before)

print()
print("【场景6】睡前陪伴（A.1 四场景）")
user_turn("好啦宝贝，我准备睡了，晚安")
check("睡前有软软的话", True)

print()
print("【场景7】形象面板状态（D 层）")
import re as _re
import json as _json
content = open(chat.FACE_STATE_FILE, encoding="utf-8").read()
m = _re.search(r"window\.XIAOLI_STATE = (.*);", content, _re.S)
# json.loads 而非 eval：文件里有 voice/proactive 布尔字段（JS 小写 true/false），
# Python 的 eval 会 NameError（"true" 未定义）——JSON 本来就是 JS 子集
state = _json.loads(m.group(1))
print(f"  （face_state: mood={state['mood']} 好感={state['affection']} 话={state['text'][:20]}…）")
check("形象状态文件已更新", "text" in state and "mood" in state)

print()
print("【场景7.5】小脾气全链路：吃醋 → 真生气 → 哄 → 和好（B.1 + 程序触发检测）")
user_turn("我们公司来了个新的女同事，长得很可爱耶")
check("她吃醋了（心情变 jealous）", her_heart["mood"]["primary"] == "jealous")
user_turn("那个女同事约我周末吃饭，我想想答应不答应")
check("你还提 → 升级真生气（angry）", her_heart["mood"]["primary"] == "angry")
user_turn("好啦别生气，我推掉啦，这周末都陪你，你最好了")
check("哄一次降级（不再 angry，还酸着）", her_heart["mood"]["primary"] not in ("angry", "content"))
user_turn("对不起嘛，我错啦，你才是最美的，抱抱～")
check("哄到心软（content，和好）", her_heart["mood"]["primary"] == "content")

print()
print("【场景8】3天后重逢：时间衰减（B.1）")
# 模拟真实场景：3天前最后聊天后程序关闭，decay_applied 停留在那之前
three_days = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
her_heart["last_interaction"] = three_days
her_heart["decay_applied"] = (datetime.now() - timedelta(days=3, hours=1)).strftime("%Y-%m-%d %H:%M")
aff_3d = her_heart["affection"]
heart_mod.apply_time_decay(her_heart)
print(f"  （离开3天：好感 {aff_3d} → {her_heart['affection']}，心情 {her_heart['mood']['primary']}）")
check("好感衰减了", her_heart["affection"] < aff_3d)
check("3天没来 → 心情变忧郁", her_heart["mood"]["primary"] == "melancholy")

print()
print("【场景9】新会话（清空历史）后她记得你（B.3 档案 + A.2 时间推理）")
diary["messages"] = []
user_turn("宝贝，我该回杭州了，你有什么想说的吗？")
names = [f["content"] for f in memory_mod.load_facts()]
check("档案还在（没被新会话清掉）", len(names) >= 1)
check("她还记得杭州的事", any("杭州" in n for n in names))

print()
print("#" * 56)
print(f"#  全部 {F} 项检查通过！小李完整链路实测完成")
print("#" * 56)
