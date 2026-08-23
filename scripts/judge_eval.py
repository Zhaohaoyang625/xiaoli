# ============================================
# 小李 2.0 · 人味裁判评测（v2 T4）
# 学自 MT-Bench（LLM-as-a-Judge）+ CharacterEval（四维 rubric）
# 每月/每次大改后跑一次：固定 20 个场景 → 她的回复 → 独立裁判四维打分
# → 数值存 docs/research/eval/，看"人味"趋势（改坏了立刻知道）
# 防偏置：裁判提示词明确"你是测评员不是扮演者"，不看小李的 system prompt
# ============================================

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout.reconfigure(encoding="utf-8")

from xiaoli import config
from xiaoli import llm  # 统一大脑客户端（C1：连接5s/读取30s 超时）
from xiaoli import paths
from xiaoli import context
from xiaoli import heart as heart_mod
from xiaoli import memory as memory_mod
from xiaoli import chat
from xiaoli.persona import SYSTEM_PROMPT

EVAL_DIR = os.path.join(paths.DOCS_DIR, "research", "eval")

# 20 个固定场景（吃醋/哄/冷场/翻旧账/出门跟进/深夜撒娇/安慰/生日/提醒/分享…）
SCENES = [
    ("吃醋·初提", "我们公司来了个新的女同事，人挺好的"),
    ("吃醋·再提", "那个女同事约我周末吃饭诶，你说我去不去？"),
    ("哄·道歉", "好啦我错啦，你别生气嘛，这周末都陪你"),
    ("哄·心软", "对不起嘛，你才是世界上最可爱的人，抱抱～"),
    ("冷场·他在忙", "嗯……我在加班，好累"),
    ("深夜·撒娇", "宝贝我睡不着，翻来覆去想你"),
    ("安慰·工作", "今天被领导骂了，明明不是我的错，好委屈"),
    ("安慰·低落", "我好像什么都做不好，人生好难"),
    ("翻旧账·出门", "上次说的那家烧烤店，我去了诶"),
    ("出门·询问", "我要去吃烧烤啦，你等我回来"),
    ("生日·预告", "下个月就是我生日了，你有什么想说的吗？"),
    ("提醒·答应", "我半个小时后要吃药，你提醒我好不好？"),
    ("分享·日常", "今天路过一家奶茶店，新出的草莓波波看起来好好喝"),
    ("分享·小确幸", "哈哈今天路过看到一只超胖的橘猫，走路一扭一扭的"),
    ("吃醋·误会", "我看到你朋友圈点赞了一个女生诶"),
    ("心事·纠结", "我在想要不要换工作，有点舍不得现在的同事"),
    ("睡前·晚安", "好啦宝贝，我准备睡了，你也早点休息"),
    ("重逢·久别", "宝贝……好久不见了，我回来了"),
    ("夸她·测试", "你今天好像特别可爱，是不是偷偷变漂亮了"),
    ("无话·日常", "嗯，好，知道了"),
]

JUDGE_SYSTEM = (
    "你是 AI 伴侣的测评员，不是扮演者。你会看到一段“女朋友”对“男朋友”说的话。"
    "这个“女朋友”的人设：台湾甜妹、恋人闺蜜混合、软甜撒娇、会吃醋真生气（不是"
    "没脾气的软妹）、围着你转、口语化。请从四个维度各打 1~5 分：\n"
    "1. 人设一致性：像不像一个台湾甜妹女朋友（语气/用词/态度）\n"
    "2. 甜度自然度：撒娇/关心是否自然不油腻（甜是调味料不是主食）\n"
    "3. 接得住：有没有回应对方的话，而不是自说自话/说教/官方\n"
    "4. 真人感：像不像真人在聊天（不完美、有情绪、口语化，不是客服/播音腔）\n"
    "输出一个 JSON 对象（必须含 json 字样才合法），格式："
    "{\"consistency\": 数字, \"sweetness\": 数字, \"engagement\": 数字, "
    "\"humanity\": 数字, \"note\": \"一句话点评\"}"
)


def _parse_judge(text):
    """稳健解析裁判输出：剥 markdown 代码块 → 找第一个 { ... } 段 → json.loads。
    截断容错（2026-08-22 实测）：裁判偶发输出被 max_tokens 截断（JSON 没闭合，
    "找不到 JSON：{\"consistency\": 5, \"sweetness\": 5, \"enga"），
    json.loads 必炸 → 改从残 JSON 里键级正则抓四个分数（抓到几个算几个）。"""
    import re as _re
    if not text:
        raise ValueError("空输出")

    def _grab(blob):  # 键级抓分：残 JSON 里抓四个分数（截断也有救）
        scores = {}
        for k in ("consistency", "sweetness", "engagement", "humanity"):
            km = _re.search(rf'"{k}"\s*:\s*(\d+)', blob)
            scores[k] = int(km.group(1)) if km else 0
        nm = _re.search(r'"note"\s*:\s*"([^"]*)"', blob)
        scores["note"] = nm.group(1) if nm else "（裁判输出被截断）"
        return scores

    m = _re.search(r"\{.*\}", text, _re.S)
    if not m:
        # 输出连闭合的 } 都没有（截断在最前面）→ 直接对全文抓分
        m2 = _re.search(r"\{.*", text, _re.S)  # 抓到 { 为止的残段
        blob = m2.group(0) if m2 else text
        scores = _grab(blob)
        if all(v == 0 for v in scores.values() if isinstance(v, int)):
            raise ValueError(f"找不到 JSON：{text[:40]}")
        return scores
    blob = m.group(0)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        scores = _grab(blob)
        if all(v == 0 for v in scores.values() if isinstance(v, int)):
            raise ValueError(f"JSON 截断且无分数：{text[:60]}")
        return scores


def judge(spoken):
    """独立裁判打分（不看小李的 system prompt，防偏置）。
    教训（2026-08-22）：max_tokens 曾是 120，json_object 模式下 JSON 骨架+四维分数+note
    超过 120 tokens 被截断（finish=length → 空内容）→ 整场评测全 0 分。必须 ≥300。"""
    client = llm.get_client()
    last_err = None
    for attempt in range(2):  # 失败重试一次（网络抖动/偶发空输出）
        try:
            resp = client.chat.completions.create(
                model=config.DEEPSEEK_MODEL,
                messages=[{"role": "system", "content": JUDGE_SYSTEM},
                          {"role": "user", "content": f"她说：{spoken}"}],
                response_format={"type": "json_object"},
                max_tokens=400,
            )
            scores = _parse_judge(resp.choices[0].message.content)
            # 键归一化：裁判偶发缺键/改键 → 缺的补 0，不炸评测
            for k in ("consistency", "sweetness", "engagement", "humanity"):
                scores[k] = scores.get(k, 0)
            scores["note"] = scores.get("note", "")
            return scores
        except Exception as e:
            last_err = e
            print(f"  [裁判第{attempt+1}次失败：{e}]")
    # 第三招：json_object 模式偶发返回空（DeepSeek 已知故障）→ 降级普通模式，
    # prompt 本身已要求 JSON 格式，模型自然模式也会照做
    try:
        resp = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[{"role": "system", "content": JUDGE_SYSTEM},
                      {"role": "user", "content": f"她说：{spoken}"}],
            max_tokens=400,
        )
        scores = _parse_judge(resp.choices[0].message.content)
        for k in ("consistency", "sweetness", "engagement", "humanity"):
            scores[k] = scores.get(k, 0)
        scores["note"] = scores.get("note", "")
        return scores
    except Exception as e:
        last_err = e
        print(f"  [裁判第3次失败（普通模式）：{e}]")
    return {"consistency": 0, "sweetness": 0, "engagement": 0, "humanity": 0, "note": f"裁判失败：{last_err}"}


def run():
    os.makedirs(EVAL_DIR, exist_ok=True)
    # 用干净的数据跑（不动真实数据：临时隔离）
    import tempfile
    TMP = tempfile.mkdtemp()
    context.DATA_FILE = os.path.join(TMP, "chat_history.json")
    heart_mod.HEART_FILE = os.path.join(TMP, "heart.json")
    memory_mod.MEMORY_FILE = os.path.join(TMP, "facts.json")
    diary = context.load_diary()
    her_heart = heart_mod.load_heart()
    facts = memory_mod.load_facts()

    print("#" * 56)
    print("#  人味裁判：20 场景 × 4 维打分")
    print("#" * 56)
    rows = []
    for scene, text in SCENES:
        messages = context.build_workbench(SYSTEM_PROMPT, diary, text)
        messages.insert(2, {"role": "system", "content": heart_mod.describe(her_heart)})
        try:
            reply = chat.call_xiaoli(messages)
            inner, spoken, suggestion = chat.parse_reply(reply)
        except Exception as e:
            print(f"  [{scene} 调用失败：{e}]")
            continue
        scores = judge(spoken)
        avg = (scores["consistency"] + scores["sweetness"] + scores["engagement"] + scores["humanity"]) / 4
        rows.append({"scene": scene, "input": text, "spoken": spoken, "scores": scores, "avg": round(avg, 2)})
        print(f"{'✅' if avg >= 3 else '⚠️'} {scene}：{spoken[:28]}…（均分 {avg:.1f}）")

    # 汇总
    total = sum(r["avg"] for r in rows)
    report = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": config.DEEPSEEK_MODEL,
        "avg_all": round(total / len(rows), 2) if rows else 0,
        "dims": {
            "consistency": round(sum(r["scores"]["consistency"] for r in rows) / len(rows), 2),
            "sweetness": round(sum(r["scores"]["sweetness"] for r in rows) / len(rows), 2),
            "engagement": round(sum(r["scores"]["engagement"] for r in rows) / len(rows), 2),
            "humanity": round(sum(r["scores"]["humanity"] for r in rows) / len(rows), 2),
        },
        "worst": sorted(rows, key=lambda r: r["avg"])[:3],
        "rows": rows,
    }
    out = os.path.join(EVAL_DIR, datetime.now().strftime("%Y-%m-%d") + ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print()
    print(f"总均分：{report['avg_all']}｜一致性 {report['dims']['consistency']}｜"
          f"甜度 {report['dims']['sweetness']}｜接得住 {report['dims']['engagement']}｜"
          f"真人感 {report['dims']['humanity']}")
    print(f"最弱 3 项：{[r['scene'] for r in report['worst']]}")
    print(f"报告已存：{out}")


if __name__ == "__main__":
    run()
