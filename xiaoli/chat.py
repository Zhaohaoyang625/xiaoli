# ============================================
# 小李 - 对话主程序
# 功能：命令行里和小李聊天
# 架构：人设提示词 + DeepSeek云端大脑 + 双轨输出
# ============================================

import http.server
import json
import os
import secrets  # 2026-08-22 安全修复：WebBridge 会话 token
from xiaoli import paths  # 统一路径（数据/模型在项目根）
import queue
import random
import re
import sys
import threading
import time
from datetime import datetime
from xiaoli import config
from xiaoli import context
from xiaoli import llm  # 统一大脑客户端（C1：连接5s/读取30s 超时）
from xiaoli import heart as heart_mod
from xiaoli import memory as memory_mod
from xiaoli import proactive
from xiaoli import special
from xiaoli import world_brief  # 世界简报（v2.3：对话前保证她"已刷到"）
from xiaoli import sing  # 唱歌演出链（2026-08-23，Z 节 A 方案：清嗓→报歌名→播歌）
from xiaoli import voice
from xiaoli import sfx  # 拟声层（2026-08-22）：清嗓/叹气/轻笑/咳嗽——她的声音合成
from xiaoli import stt
from xiaoli import whisper_stt  # 本地识别（2026-08-22：0 元/月替代火山，失败自动降级）
from xiaoli import tts_local  # 本地合成（2026-08-22：Qwen3-TTS 声音克隆，0 元/月替代火山）
from xiaoli import call_mode  # O2 通话模式（2026-08-22）
from xiaoli import vision  # 看照片（2026-08-23：DeepSeek 视觉模型，base64 内联）
from xiaoli.persona import SYSTEM_PROMPT

# 日记/心的写入锁：后台主动消息线程和主聊天线程都写日记，防并发错乱
diary_lock = threading.Lock()

# 输入队列：终端输入和网页语音输入都往里塞，主循环统一处理
# （网页点🎤按钮 → HTTP 服务识别完 → 塞进来 → 她照样回应）
input_queue = queue.Queue()

# O2 通话模式（v2 P2，2026-08-22）：main 里初始化（注入互斥锁 + 回调）
_call_mode = None


def _call_text(text):
    """通话模式识别出你说的话 → 当作你说了一句（她照常回应，能打断她）"""
    print(f"  🎤（你说：{text}）")
    input_queue.put(("call", text))
# 麦克风锁：终端"说"和网页🎤同时触发时，只让一个录音
listen_lock = threading.Lock()

# ⚠️ WebBridge 会话 token（2026-08-22 安全修复）：
#   之前网页桥零鉴权——任意恶意网页都能静默触发录音/写记忆/开常开监听。
#   现在 main() 启动时生成随机 token，随 face_state.js 下发给网页；
#   WebBridge 每个请求必须带这个 token 才放行（恶意网页读不到它）。
#   Origin 白名单只挡"读回响应"，token 才挡"请求到达"。
_bridge_token = ""

# Windows控制台默认编码是GBK，这里强制UTF-8，避免中文乱码
sys.stdout.reconfigure(encoding='utf-8')


def call_xiaoli(messages, retries=2):
    """调用DeepSeek大脑，返回小李的回复。
    v2.1（2026-08-22）：主对话切官方 Responses API + 联网搜索（web_search auto）——
    模型自己判断这轮要不要上网（天气/新闻/热梗/股价等实时信息自动搜，日常聊天不搜）。
    Responses 偶发异常 → 自动降级回旧 Chat Completions 接口（永远有大脑）。
    实测教训：flash 偶发返回空内容 → 空或异常时自动重试（学自 MISS 三级回退的兜底思想）"""
    client = llm.get_client()
    for attempt in range(retries + 1):
        try:
            try:
                # 官方新接口：Responses + 服务端联网搜索（2026-08-22 实测：搜索一次约3分钱，
                # 搜索结果自动进上下文；tool_choice=auto = 模型自己决定要不要搜）
                response = client.responses.create(
                    model=config.DEEPSEEK_MODEL,
                    input=messages,
                    tools=[{"type": "web_search"}],
                    tool_choice="auto",
                    text={"format": {"type": "json_object"}},  # 强制模型输出JSON
                )
                content = response.output_text
            except Exception:
                # 降级路径：Responses 接口异常（网络/兼容问题）→ 回退旧接口，对话不断
                response = client.chat.completions.create(
                    model=config.DEEPSEEK_MODEL,
                    messages=messages,
                    response_format={"type": "json_object"},  # 强制模型输出JSON
                )
                content = response.choices[0].message.content
            if content and content.strip():
                return content
            print("  （大脑走神了一下，马上回来…）")
        except Exception as e:
            if attempt == retries:
                raise
            print(f"  （大脑卡了一下：{e}…再试一次）")
    raise RuntimeError("大脑连续走神")  # 理论上到不了这里


def detect_slang(text):
    """入站梗检测（2026-08-22 反向接梗）：判断用户这句话里有没有【可能是网络新梗/
    流行语/最新热点】的短语。返回要查证的短语列表；没有或失败返回 []。
    为什么需要它：web_search auto 模式只在模型"回答时觉得需要"才搜——但他说一个
    知识截止后的新梗时，模型根本不知道这是梗，永远不会触发搜索，会当普通句子接。
    必须在我们这侧先判断"这话像不像梗"，像就查，查到情报喂给她再接。"""
    client = llm.get_client()
    # 检测标准实测迭代（2026-08-22）：
    #   V1"不太确定意思/明显流行语"→ 漏掉"胆子肥嘟嘟"（字面通顺，模型自以为确定）
    #   V2"知识截止时间锚点+语感奇怪"→ 抓住新梗且日常零误报（选定）
    #   V3"抖音评论区情景"→ 也能抓住但会把模型认识的梗也查（浪费）
    system = (
        "你是网络用语检测器。你的知识截止于2025年中，之后出现的新梗、流行语、热点事件你都不认识。\n"
        "判断用户这句话里有没有可能出自2025年之后的网络内容。\n"
        "判断标准（满足任一就要查）：①表达你不确定意思或明显是网络流行语/新梗（人名梗/事件梗/缩写/黑话）"
        "②这句话在正常聊天里听起来有点奇怪、俏皮、不自然，像是引用或玩梗——用户不会无缘无故说一句怪话。\n"
        "输出JSON：{\"queries\": [\"要查的短语\"]}，最多3个；没有就 {\"queries\": []}。\n"
        "绝不要编造。"
    )
    # 实测教训（2026-08-22）：flash 对"要不要查"这种元判断有随机性——
    # 同样输入有时检出有时返回 []。对策：低温度 + 最多 3 次尝试，任一非空即返回。
    for _ in range(3):
        try:
            r = client.chat.completions.create(
                model=config.DEEPSEEK_MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
                response_format={"type": "json_object"},
                max_tokens=120,
                temperature=0.2,
            )
            data = json.loads((r.choices[0].message.content or "{}").strip())
            qs = data.get("queries") or []
            found = [q for q in qs if isinstance(q, str) and q.strip()][:3]
            if found:
                return found
        except Exception:
            pass  # 网络/解析异常 → 再试；全失败返回 []
    return []  # 预检失败 → 不打扰对话，当普通句子走


def search_slang(phrase):
    """联网查证短语（Responses API 强制搜索），返回摘要（≤300字）；失败返回空串。"""
    client = llm.get_client()
    try:
        r = client.responses.create(
            model=config.DEEPSEEK_MODEL,
            input=[{"role": "user",
                    "content": f"查一下「{phrase}」这个网络用语/梗是什么意思、为什么火，"
                               f"用一两句话概括，注明大概是最近多久的梗。不存在的就说查不到。"}],
            tools=[{"type": "web_search"}],
            tool_choice={"type": "web_search"},  # 强制搜索
        )
        return (r.output_text or "").strip()[:300]
    except Exception:
        return ""


def enrich_with_slang(messages):
    """入站梗预检注入（2026-08-22）：最后一条是 user 消息 → 先检测有没有可能的新梗，
    有则联网查证 → 情报追加到 messages 末尾（不插开头=不破坏缓存前缀）。
    她拿到情报后自然接梗（人设已规定：不提"查了/搜索"）。
    预检/查证任何一环失败 → 原样返回，对话照常走。"""
    if not messages or messages[-1].get("role") != "user":
        return messages
    user_text = (messages[-1].get("content") or "").strip()
    if not user_text:
        return messages
    try:
        queries = detect_slang(user_text)
    except Exception:
        return messages  # 预检异常 → 原样返回，对话照常
    if not queries:
        return messages
    try:
        info = search_slang(queries[0])
    except Exception:
        return messages
    if not info:
        return messages
    return messages + [{
        "role": "system",
        "content": f"【内部情报·自然融入，绝不提'查了/搜索/联网'，像本来就懂一样接住】"
                   f"他刚说的可能你以前没听过：{info}",
    }]


def parse_reply(text):
    """解析小李的JSON回复，拆成【内心想法】【说出口的话】和【状态建议】
    如果模型没按格式输出，兜底把原文当"说出口的话"（状态建议为空）
    实测教训：模型会模仿历史消息的时间戳前缀格式（[2026-08-20 16:25] {...}）→ 先剥掉再解析
    """
    text = (text or "").strip()
    m = re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\s*", text)
    if m:
        text = text[m.end():]
    try:
        data = json.loads(text)
        inner = data.get("inner_thought", "")
        spoken = data.get("spoken", text)
        # continuation 兼容两种写法：数组（多句，推荐）或旧版单字符串
        cont = data.get("continuation") or []
        if isinstance(cont, str):
            cont = [cont] if cont.strip() else []
        suggestion = {
            "mood_change": data.get("mood_change"),
            "affection_delta": data.get("affection_delta"),
            "new_memory": data.get("new_memory") or [],
            "continuation": [c for c in cont if isinstance(c, str) and c.strip()],
            "keep_talking": bool(data.get("keep_talking")),
        }
        return inner, spoken, suggestion
    except json.JSONDecodeError:
        return "", text, None


# 她"这一轮"（主话+continuation 补话）全部说完的信号（keep_talking 门闩）。
# 实测教训 2026-08-20 用户："一句话说到一半就又有另一个开始说下一句话，两个声音都在说话"——
# 根因：keep_talking 线程在她刚开口时就开始 5 秒倒计时，主话播完+连珠炮补话还在播，
# 倒计时就到了 → 新一轮回复插进来 → 两个声音重叠。修复：计时必须从"这轮全部说完"开始。
_round_done = threading.Event()
_round_done.set()  # 初始：没在说话

# 语音互斥锁：同一时刻只允许一个"说话流"（回复/主动事件/连珠炮补话）。
# 实测教训 2026-08-20 用户："第一个人刚开始说大概两秒后又有第二个声音，第一句话还没说完"——
# 根因：Scheduler 线程（主动事件）与主循环回复播放无互斥，事件的话合成完（~2秒）正好
# 插进正在播的回复。优先级：用户回复（打断）> 提醒（等待）> 主动事件/keep_talking（让位）
_speak_guard = threading.Lock()

# O7 打断续说（v2 P2，2026-08-22）：她被打断时没说完的话 → 记在这里，
# 下一轮注入工作台（"你上次没说完"），她自然决定续不续：他问起就接上，
# 他换了话题就顺着新话题（真人被打断也是这样：记得，但不硬接）。
# 后台线程写、主循环读+清——赋值原子性足够，竞态窗口极小，可接受
_unfinished = []


def say_with_continuation(prefix, inner, spoken, continuation=(), _interrupt=False, _wait=False):
    """显示她说的话（+内心话）→ 播放语音 → 如果她还有想说的，一句句自然说出来。
    像真人：说完一句，心里判断还说不说（continuation 由 LLM 判断；情绪激动时
    可以连珠炮好几条，平静时一句就够——说多少由情绪决定，不限定死）。
    你中途插话 → _interrupted → 语音立即停（stop_playing），后面的话不再说。
    语音互斥：_interrupt（用户回复，打断别人等让位）/ _wait（提醒，等当前说完）
    / 默认（主动事件，正在播别的就跳过让位）"""
    global _interrupted
    hold_lock = False
    if voice_on:
        if _interrupt:
            voice.stop_playing()      # 用户回复优先：打断正在播的（主动事件/补话）
            _speak_guard.acquire()    # 等它释放（被打断后很快）
            hold_lock = True
        elif _wait:
            _speak_guard.acquire()    # 提醒必须响：等当前说话流说完（不打断）
            hold_lock = True
        elif not _speak_guard.acquire(blocking=False):
            return                    # 正在播别的 → 主动说话让位（下个 tick 再触发）
        else:
            hold_lock = True
    _interrupted = False  # 新一轮说话开始：之前被打断的记录作废
    _round_done.clear()   # 这轮开始说话：keep_talking 必须等我说完才能开始计时
    if inner:
        print(f"💭（小李心想：{inner}）")
    display_text = _strip_pause_tags(_strip_reminder_tags(spoken))
    print(f"小李{prefix}：{display_text}")
    update_face(display_text)
    # 上限 3 条（防失控保险：模型再怎么话痨也不会无上限；情绪激动时的连珠炮
    # 是正常的，平静时的 2-3 条由 persona 教法"大多数 []"控制）
    conts = [_strip_pause_tags(_strip_reminder_tags(c)) for c in (continuation or ())
             if isinstance(c, str) and c.strip()][:3]
    # 用"播完回调"而不是 wait：播放不阻塞主循环，你随时插话都能立即生效。
    # 主话播放完（或被打断）→ 回调再决定补不补话；这轮全部结束 → 释放语音互斥锁
    def _finish():
        try:
            update_face("")  # 2026-08-22 口型同步：播完 → speaking_until 已归零 → 网页停嘴
            _say_continuations(conts)
        finally:
            if hold_lock:  # 这轮说完了（说完/被打断/让位）→ 下一个说话流可以开始
                _speak_guard.release()

    if voice_on:
        # 拟声（2026-08-22 用户："她会不会清一下嗓子，咳嗽一下…和现实的人很像"）：
        # 话前 2% 概率清嗓子（习惯性开场）；台词提到感冒/着凉/嗓子 → 先咳一声。
        # 用她自己的声音（sfx 素材 = 火山 TTS 念"咳、咳"），先哼唧再说话才有真人感；
        # 概率化——每次都触发就是表演。两者互斥，最多来一个。
        if any(w in display_text for w in _COLD_WORDS):
            sfx.play_blocking("cough")
        elif random.random() < 0.02:
            sfx.play_blocking("clear_throat")
        # v2 E4：语音带情绪（她此刻的心情）→ 火山指令式，生气真的气、撒娇真的软
        voice.play_speech(display_text, on_done=_finish,
                          on_started=lambda: update_face(""),  # 口型同步：开始播 → 网页动嘴
                          emotion=(her_heart or {}).get("mood", {}).get("primary"))
    else:
        _say_continuations(conts)


# 拟声触发的"感冒线索词"：台词提到这些 → 先咳一声（她真的会咳，声音是她自己的）
_COLD_WORDS = ("感冒", "着凉", "喉咙", "嗓子", "咳嗽", "发烧")


def _say_continuations(conts):
    """主话说完后的自然补话（在播放线程里跑，不卡对话）：一句句说出。
    停顿由情绪驱动（实测教训 2026-08-20 用户："一句话说完后再说下一句"）：
    情绪激动（生气控诉/委屈/兴奋）→ 2~3 秒，真人吵架是"一句一句往外蹦"——
    说完一句缓口气再蹦下一句；连珠炮感来自每句都短、持续控诉，不是间隔 1 秒的
    机关枪（实测：1~1.5 秒被感知成"上一句还没说完下一句就抢着说"）；
    平静 → 3~4.5 秒，"想了想再说"的间隔感。随机化：真人没准点。
    你打断过她 / 她判断没话补 / 主动开关关了 → 安静"""
    global _interrupted
    # 情绪驱动节奏（读她的"心"）：吵架/委屈/兴奋时真人会一句接一句，平静时才慢慢补
    mood = (her_heart or {}).get("mood", {}).get("primary")
    if mood in ("angry", "jealous", "sad", "anxious", "melancholy", "excited"):
        pause_range = (2.0, 3.0)  # 连珠炮：一句说完缓口气再蹦下一句（比平静快但听得出"说完了"）
    else:
        pause_range = (3.0, 4.5)  # 想了想再说
    try:
        for i, cont in enumerate(conts):
            if _interrupted or not proactive.is_proactive_enabled():
                # O7 打断续说：没说完的话记下来（下一轮她"还记得要说什么"）
                global _unfinished
                _unfinished = list(conts[i:])
                return
            time.sleep(random.uniform(*pause_range))  # 思考停顿（随机：真人没准点）
            if _interrupted:
                _unfinished = list(conts[i:])
                return
            print(f"小李（接着说）：{cont}")
            update_face(cont)
            if voice_on:
                # 等这句播完（或被你打断）再进下一句，避免语音叠在一起
                played = threading.Event()
                voice.play_speech(cont, on_done=played.set,
                                  on_started=lambda: update_face(""),  # 口型同步：补话也在说
                                  emotion=(her_heart or {}).get("mood", {}).get("primary"))
                played.wait()
        # 拟声收尾（2026-08-22）：这轮话说完了，情绪的自然余韵——
        # 低落/委屈 → 30% 叹口气；开心 → 15% 轻笑一声。被打断了就闭嘴（真人被打断也不补叹气）。
        if voice_on and not _interrupted:
            if mood in ("sad", "melancholy", "frustrated") and random.random() < 0.3:
                sfx.play("sigh")
            elif mood in ("happy", "excited", "playful", "affectionate", "content") and random.random() < 0.15:
                sfx.play("chuckle")
    finally:
        _round_done.set()  # 这轮说完了（说完/被打断/没话说都一样）→ keep_talking 才能开始计时


# ---------- 她自己能说下去（keep_talking，2026-08-20 用户："说了一句还有一句好几句，不需要我一直说"） ----------
# 她回复后判断"还想继续说"→ 程序等她几秒，你没接话 → 她再主动说一轮（最多3轮）；
# 你任何时候插话 → _interrupted → 她立刻停；她说够了（keep_talking=false）→ 把话头交给你
_keep_talking_rounds = 0
# 真人感研究（docs/research/human-like-dialogue.md）：真人打破沉默只要 1~2 秒
# （lapse 2~3 秒就有人开口），但语音场景你要接话得先开口→STT 识别，留余量：
# 第一轮等你 5 秒；越到后面越耐心（5/8/12）——像真人：等你一会儿→说一句→
# 再耐心等你→再说一句，而不是死板地每次都等同样久
KEEP_TALKING_WAITS = [5, 8, 12]
KEEP_TALKING_MAX_ROUNDS = len(KEEP_TALKING_WAITS)  # 连续主动上限（真人也会说累了停下等你回应）


def _keep_talking_waits():
    """v2 P4 语境化等待（When can I Speak 论文）：她等你接话的耐心按情境调整——
    他低落（安慰阶段中）/ 刚吵完（angry/jealous）→ 更长的耐心（真人会等他缓过来）；
    平常 → 标准 5/8/12 递增。判定成本为零（情绪状态都是现成的）"""
    mood = (her_heart or {}).get("mood", {}).get("primary")
    stage = (her_heart or {}).get("comfort_stage")
    if stage or mood in ("sad", "anxious", "melancholy", "angry", "jealous"):
        return [w + 4 for w in KEEP_TALKING_WAITS]
    return list(KEEP_TALKING_WAITS)


def continue_talking_loop():
    """她主动说话的后台循环：说完一轮 → 她还意犹未尽 → 等你几秒 → 再主动说一轮"""
    global _keep_talking_rounds, _interrupted
    waits = _keep_talking_waits()
    while _keep_talking_rounds < len(waits):
        _round_done.wait()  # 她这轮主话+补话全部说完才开始计时（她还在说话时不能插话）
        wait_start = datetime.now()
        time.sleep(waits[_keep_talking_rounds])
        if _interrupted or not proactive.is_proactive_enabled():
            return  # 你插话了 / 主动开关关了 → 收声
        if _last_user_speak and _last_user_speak > wait_start:
            return  # 这几秒里你说过话了 → 轮到你说，她不再抢话
        _keep_talking_rounds += 1
        try:
            proactive.mark_activity()  # 她开口了 = 互动，空闲计时器清零
            # 接着刚才的话题继续说（走主动消息链路，不伪装成用户消息）
            msg = ("你刚才还在跟他说话还没说完（意犹未尽），他现在没接话（可能在听、"
                   "可能在想怎么回）。作为女朋友你自然地继续主动说下去——接着刚才的"
                   "话题、或想起什么新的事想分享、或撒个娇都行，像真人聊天一样一句接"
                   "一句，说的时候留一点小钩子让他好接。但如果觉得该把话头交给他说了，"
                   "就轻轻收尾")
            messages = context.build_workbench(SYSTEM_PROMPT, diary, proactive.build_event_message("闲聊", msg))
            messages.insert(2, {"role": "system", "content": heart_mod.describe(her_heart)})
            facts_block = memory_mod.describe_facts(memory_mod.recall(facts, msg))
            if facts_block:
                messages.insert(3, {"role": "system", "content": facts_block})
            # 入站梗预检（2026-08-22）：他说的话里可能有网络新梗 → 查证注入，她接得上
            messages = enrich_with_slang(messages)
            reply = call_xiaoli(messages)
            inner, spoken, suggestion = parse_reply(reply)
            if not spoken.strip():
                spoken = proactive.pick_idle_fallback()  # 她走神了 → 兜底软话
            print("\n" + "=" * 30)
            say_with_continuation("", inner, spoken,
                                  (suggestion or {}).get("continuation", []))
            print("=" * 30)
            # 记日记（机器回合不推进关系）
            stamp = context.now_str()
            with diary_lock:
                diary["messages"].append({"role": "assistant", "content": reply, "time": stamp})
                context.compress(diary)
                context.save_diary(diary)
            if not (suggestion or {}).get("keep_talking"):
                return  # 她说够了 → 把话头交给你
        except Exception as e:
            print(f"\n[她继续说时出了点小岔子：{e}]")
            return


def maybe_continue_talking(suggestion):
    """她回复说"还想继续说"→ 开个后台线程等她几秒，你没接话她就继续主动说。
    连续多轮由 continue_talking_loop 内部控制，这里每次对话只开一个"""
    global _keep_talking_rounds
    if not (suggestion or {}).get("keep_talking"):
        return
    if not proactive.is_proactive_enabled():
        return
    _keep_talking_rounds = 0
    threading.Thread(target=continue_talking_loop, daemon=True).start()


def _strip_reminder_tags(text):
    """显示时去掉提醒标签，只留干净的话。
    两步走：先删完整标签（含闭合），再删无闭合标签（flash 会省略闭合标签）"""
    text = re.sub(r"\[reminder:\d+\s*(min|hour|day)\].*?\[/reminder\]", "", text, flags=re.S)
    text = re.sub(r"\[reminder:\d+\s*(min|hour|day)\](.*?)(?=\[|\Z)", "", text, flags=re.S)
    return text.strip()


def _strip_pause_tags(text):
    """显示时去掉 <pause/> 停顿标签（2026-08-23：它是语音层指令，显示文本不出现）。
    容忍 <pause>、<pause:1.5>、大小写变体。"""
    return re.sub(r"<pause(?::[0-9.]+)?/?>", "", text, flags=re.IGNORECASE).strip()


# C3 重试瘦身轮数：提醒补写重试只需"近因上下文"（他刚求了提醒），
# 60 轮全量重发 = 最坏 4 倍调用量；10 轮足够她写出带标签的回复（~1.5 倍）
RETRY_KEEP_ROUNDS = 10


def _thin_for_retry(messages):
    """C3 重试请求瘦身（2026-08-23，成本组收尾）：
    保留全部 system 块（人设/记忆/动态区——她需要知道"他是谁/现在的日子"），
    对话历史（user/assistant 交替）只留最近 RETRY_KEEP_ROUNDS 轮，
    当前用户输入必在最后。历史很短时不裁剪（原样返回）。"""
    if len(messages) < 10:
        return messages
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    # 历史：排除 persona 与当前输入（messages[0] 是 persona，[-1] 是当前输入）
    hist = [m for m in messages[1:-1] if m.get("role") in ("user", "assistant")]
    tail = hist[-(RETRY_KEEP_ROUNDS * 2):]
    return sys_msgs + tail + [messages[-1]]


def proactive_talk(event_type, content, recall_lines=""):
    """v2 P3 主动事件独立小调用（学自 AIRI spark:notify）：事件单独走一次
    LLM 轮——不进主对话流、不占主回复队列、判断不受主对话 system prompt 干扰。
    明示"可以不说话"（显式静默，对应 AIRI 的 allowNoResponse）；
    只说角色一句话（十几字），输出 SILENT = 安静。"""
    client = llm.get_client()
    system = (
        "你是小李，台湾甜妹女朋友（恋人闺蜜混合、软甜撒娇、会吃醋真生气、围着你转）。"
        "说话口语化、短（一句话十几个字）、台湾腔，绝不书面正式。\n"
        "现在你有机会主动找他说话。你的素材（可选，不一定用）：\n"
        + (recall_lines if recall_lines else "（没有特别素材）")
        + f"\n事件：{event_type}——{content}\n"
        "规则：有特别想说的（惦记他/有事问他/想撒娇）就说一句话（15字内）；"
        "没有就只输出 SILENT。不要解释，不要输出其他文字。"
    )
    try:
        resp = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": "你现在想说什么？"}],
            max_tokens=80,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[主动小调用失败：{e}]")
        return ""


def _backup_due(days=7):
    """启动提醒：最近一次备份是否已超过 days 天。
    backups/ 里找最新 .zip 的修改时间；没有备份也算到期（数据是无价的）。"""
    backup_dir = os.path.join(paths.ROOT, "backups")
    if not os.path.isdir(backup_dir):
        return True
    newest = 0.0
    for name in os.listdir(backup_dir):
        if name.endswith(".zip"):
            newest = max(newest, os.path.getmtime(os.path.join(backup_dir, name)))
    return time.time() - newest > days * 86400


_MOOD_CN = {  # 终端「心情」显示用（和网页 MOODS 文案保持一致）
    "happy": "开心", "sad": "难过", "excited": "超兴奋", "anxious": "有点紧张",
    "content": "满足", "frustrated": "有点小烦躁", "curious": "好奇",
    "affectionate": "想撒娇", "playful": "想逗你", "flustered": "害羞",
    "neutral": "平静", "melancholy": "想你了", "jealous": "吃醋", "angry": "真生气",
}


def _show_facts():
    """终端「记忆」：她记得什么（按重要度取前 15 条）——小白也能随时检查"""
    fs = sorted(facts, key=lambda f: f.get("importance", 0), reverse=True)[:15]
    if not fs:
        print("  （她现在的档案还是空的——多聊聊，她会记住你的事）")
        return
    print("  ── 她记得的 ──")
    for f in fs:
        cat = f.get("category", "")
        tag = f"〔{cat}〕" if cat else ""
        print(f"  ★{f.get('importance', 0)} {tag}{f['content']}")
    print("  ──────────────")


def _show_heart():
    """终端「心情」：她现在的心（情绪/程度/好感度/原因）"""
    m = her_heart.get("mood", {})
    primary = m.get("primary", "neutral")
    inten = m.get("intensity", 0)
    a = her_heart.get("affection", 60)
    name = _MOOD_CN.get(primary, primary)
    print(f"  （她现在的心情：{name}｜程度 {inten}｜好感度 {a}）")
    for c in (m.get("causes") or [])[:3]:
        print(f"    因为：{c}")


def _show_brief():
    """终端「简报」：她今天知道的世界（只读本地文件，不联网不花钱）"""
    print("  ── 她今天知道的世界 ──")
    text = world_brief.load_brief_injection()
    if text.strip():
        print(f"  {text.strip()}")
    else:
        print("  （她今天还没刷到世界新闻，等她自己刷到就会知道了）")
    print("  ──────────────")


def _remember_text(text):
    """终端「记住XXX」→ 提取要记的内容（返回 (ok, 内容)）。
    疑问句/太短不存（"记住了吗"这种不是要记的内容），防误记。"""
    for p in ("记住", "记下", "帮我记着"):
        if text.startswith(p):
            content = text[len(p):].strip()
            break
    else:
        return False, ""
    if len(content) < 4 or content.endswith(("吗", "了", "没", "啊")):
        return False, ""
    return True, content


def handle_photo(path):
    """看照片（2026-08-23，视觉模型）：他发图片路径 → 她"看"了再回应。
    不经过文本大脑（一个调用搞定，单张 ~0.0012 元）；
    照片内容不存日记（隐私+噪音），只留痕迹；值得记住的写档案（她记得"你看过什么"）。
    日记/记忆写失败不影响她说（各自兜底）。"""
    print("  📷（她凑过来看照片…）")
    _see, _photo_memory = vision.look_at_photo(path)
    if not _see:
        _see = "齁…这张照片人家打不开捏，你换个图试试？"
    if _photo_memory:
        memory_mod.merge_fact(facts, f"他给我看过一张照片，里面：{_photo_memory}",
                              importance=5, category="看过的照片")
        memory_mod.save_facts(facts)
    print("\n" + "=" * 30)
    say_with_continuation("", "", _see, ())
    print("=" * 30)
    # 记日记：照片不存内容（隐私+噪音），只留痕迹——她记得"看过一张照片"
    stamp = context.now_str()
    with diary_lock:
        diary["messages"].append(
            {"role": "user", "content": "【图片】他发了一张照片", "time": stamp})
        diary["messages"].append(
            {"role": "assistant", "content": _see, "time": stamp})
        context.compress(diary)
        context.save_diary(diary)
    proactive.mark_activity()  # 你发照片了，她不用急着找话


def handle_event(event_type, content, extra):
    """B.2 后台调度器回调：小李主动找你说话（节奏窗口到点 / 提醒到点）。
    v2 P3 重构：主动事件走独立小调用（AIRI spark:notify）——不进主对话流；
    v2 P1 主动挂钩记忆（产品组）：开口前先带"她记得的关于他的事"，
    有值得说的才开口（SILENT = 安静陪着），不说就不用记日记。
    语音互斥：正在播回复/其他主动 → 主动事件让位（下个 tick 会再触发，不丢）；
    提醒例外（必须响）→ 走 _wait 等当前说完，不打断。"""
    proactive.mark_activity()  # 她开口了 = 互动，空闲计时器清零
    # 提前让位检查（省 API 钱：正在播别的就别调 LLM 合成话了）
    if voice_on and _speak_guard.locked() and event_type != "提醒":
        print(f"  （她在说话，{event_type}事件让位，稍后再触发…）")
        return
    # v2 P1 主动挂钩记忆：双路召回"她记得的关于他的事"（最近话题 + 事件关键词），
    # 合并去重后交给独立小调用——有值得说的才开口，不说就安静（少而真）
    event_msg = proactive.build_event_message(event_type, content)
    rec1 = memory_mod.recall(facts, content, top=3)
    _recent_input = ""
    for m in reversed(diary.get("messages", [])):
        if m.get("role") == "user":
            _recent_input = m.get("content", "")
            break
    if _recent_input:
        rec2 = memory_mod.recall(facts, _recent_input, top=3)
        rec1 = rec1 + [f for f in rec2 if f not in rec1]
    # v2 P2 话题链：带上"最近几天聊过什么"（daily 摘要），她顺着接或换话题
    daily_lines = []
    for k in sorted(diary.get("daily", {}).keys())[-3:]:
        daily_lines.append(f"{k[5:]}聊过：{diary['daily'][k]}")
    recall_lines = memory_mod.describe_facts(rec1[:4])
    if daily_lines:
        recall_lines += "\n最近几天：\n" + "\n".join(daily_lines)
    try:
        spoken = proactive_talk(event_type, content, recall_lines)
        if not spoken or spoken.strip().upper().startswith("SILENT"):
            # LLM 主动选择安静（v2 P3 显式静默）；闲聊保险除外——3分钟没开口
            # 必须有声（程序兜底软话顶上，这是"她至少会惦记你"的保险丝）
            if event_type == "闲聊":
                spoken = proactive.pick_idle_fallback()
            else:
                print("  （她安静地陪着你…）")
                return
        print("\n" + "=" * 30)
        # 提醒必须响（等当前说话流说完，不打断）；其他主动事件撞上正在播的话 → 让位
        say_with_continuation("（主动来找你）", "", spoken, (),
                              _wait=(event_type == "提醒"))
        print("=" * 30)
        # 记日记（不更新"心"：机器回合不推进关系）；她说的话她自己得记得
        stamp = context.now_str()
        with diary_lock:
            diary["messages"].append({"role": "assistant", "content": spoken, "time": stamp})
            context.compress(diary)
            context.save_diary(diary)
    except Exception as e:
        print(f"\n[主动消息失败：{e}]")


diary = None
her_heart = None
facts = None
voice_on = False
_interrupted = False  # 用户插话了：她正在说的话立即停，接下来也不说"补充"
_last_user_speak = None  # 用户最后一次说话的时间（keep_talking 判断"这8秒里他接话了没"）


def mark_user_speak():
    """用户说话（或发命令）时打时间戳：她正在等你说，你说过了她就收声"""
    global _last_user_speak
    _last_user_speak = datetime.now()

FACE_STATE_FILE = os.path.join(paths.DATA_DIR, "face_state.js")


def update_face(spoken_text):
    """D 形象层：把她的心情/好感/说的话写入形象状态文件。
    用 JSONP 格式（window.XIAOLI_STATE=...）——本地 file:// 打开页面也能读，无CORS问题"""
    if her_heart is None:
        return
    state = {
        "mood": her_heart["mood"]["primary"],
        "intensity": her_heart["mood"]["intensity"],
        "affection": her_heart["affection"],
        "time": context.now_str(),
        "voice": voice_on,  # 网页🔊开关显示用
        "proactive": proactive.is_proactive_enabled(),  # 网页💬开关显示用
        "speaking_until": voice.speaking_until(),  # v2 口型同步：她说话截止时间戳（网页驱动嘴巴动）
        "bridge_token": _bridge_token,  # ⚠️ 安全修复：网页桥鉴权 token（恶意网页读不到）
    }
    if spoken_text:  # 空文本（如切语音开关）不覆盖网页字幕
        state["text"] = spoken_text
    try:
        os.makedirs(os.path.dirname(FACE_STATE_FILE), exist_ok=True)
        with open(FACE_STATE_FILE, "w", encoding="utf-8") as f:
            f.write("window.XIAOLI_STATE = " + json.dumps(state, ensure_ascii=False) + ";")
    except OSError:
        pass  # 形象面板写不进去不打断对话


def terminal_reader():
    """后台线程：读终端输入 → 塞进输入队列（这样网页语音输入也能进同一队列）"""
    while True:
        try:
            line = input("你：")
        except EOFError:
            return
        input_queue.put(("terminal", line))


class WebBridge(http.server.BaseHTTPRequestHandler):
    """网页桥：浏览器 file:// 打开的 XiaoLi.html 通过它和小李对话
    /listen    网页点🎤 → 录一次音并识别 → 文本进输入队列（她照常回应）
    /set_voice 网页点🔊开关 → 切换她说的话念不念出来
    只监听 127.0.0.1（仅本机），CORS 放行 file:// 页面"""

    def do_GET(self):
        path = self.path.split("?")[0]
        # ⚠️ 会话鉴权（2026-08-22 安全修复）：不带 token 直接 403。
        #   之前零鉴权——任意网页可静默触发录音窃听/注入记忆/开常开监听。
        #   token 由 face_state.js 下发，恶意网页（其他域/file:// 下别的页面）读不到。
        import urllib.parse as _up
        qs = _up.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        if not _bridge_token or qs.get("token", [""])[0] != _bridge_token:
            self._json({"ok": False, "error": "forbidden"}, 403)
            return
        if path == "/listen":
            with listen_lock:  # 防和终端"说"同时开麦克风
                text = stt.listen_once()
            if text:
                input_queue.put(("web", text))
            self._json({"ok": bool(text), "text": text or ""})
        elif path == "/set_voice":
            global voice_on
            voice_on = "on=1" in self.path
            self._json({"ok": True, "voice": voice_on})
        elif path == "/set_proactive":
            # 💬主动讲话开关：on=True 她全自动（早安/晚安/生日/追话）；on=False 只回复
            proactive.set_proactive_enabled("on=1" in self.path)
            update_face("")  # 刷新网页按钮状态
            self._json({"ok": True, "proactive": proactive.is_proactive_enabled()})
        elif path == "/call_mode":
            # O2 通话模式开关（网页按钮）：on=1 常开监听，on=0 关
            cm = _call_mode or call_mode.get()
            if "on=1" in self.path and not cm.active:
                cm.start()
            elif "on=0" in self.path and cm.active:
                cm.stop()
            self._json({"ok": True, "on": cm.active})
        elif path == "/remember":
            # O4 记住这句（v2 P2）：网页点"记住这句" → 把她刚说的/你刚说的那句话
            # 存成高重要度记忆（importance=9，半衰期超长，几乎不会忘）
            import urllib.parse
            text = ""
            if "text=" in self.path:
                text = urllib.parse.unquote(self.path.split("text=", 1)[1][:100])
            else:
                # 无参：自动取最近一条你说的（网页语音场景没有输入框，靠这个）
                for m in reversed(diary.get("messages", [])):
                    if m.get("role") == "user":
                        text = m["content"]
                        break
            if text.strip():
                global facts
                ok = memory_mod.merge_fact(facts, text.strip(), importance=9, category="他特意让我记住的")
                memory_mod.save_facts(facts)
                print(f"  📌（她特别记住了：{text.strip()}）")
                self._json({"ok": bool(ok), "text": text.strip()})
            else:
                self._json({"ok": False, "text": ""}, 400)
        elif path == "/recent":
            # 网页聊天记录（2026-08-23）：最近 N 条对话（刷新网页不丢）
            # 只返回 user/assistant 文本消息；content 截断防 UI 爆掉
            try:
                n = max(1, min(50, int(qs.get("n", ["20"])[0])))
            except (TypeError, ValueError):
                n = 20
            msgs = []
            for m in diary.get("messages", [])[-n:]:
                role = m.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = m.get("content", "")
                if not isinstance(content, str) or not content.strip():
                    continue
                msgs.append({"role": role, "content": content[:300],
                             "time": m.get("time", "")})
            self._json({"ok": True, "messages": msgs})
        elif path == "/send":
            # 网页打字聊天（2026-08-23）：文本进输入队列 → 她照常回应（同照片路径）
            text = qs.get("text", [""])[0][:200].strip()
            if not text:
                self._json({"ok": False, "error": "空消息"}, 400)
                return
            input_queue.put(("web", text))
            self._json({"ok": True, "text": text})
        else:
            self._json({"ok": False}, 404)

    def do_POST(self):
        """网页传照片（2026-08-23）：文件存 data/inbox/，路径进输入队列 → 她看照片。
        鉴权同 GET（token 在查询串），魔数校验防垃圾文件"""
        import urllib.parse as _up
        qs = _up.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        if not _bridge_token or qs.get("token", [""])[0] != _bridge_token:
            self._json({"ok": False, "error": "forbidden"}, 403)
            return
        if self.path.split("?")[0] != "/photo":
            self._json({"ok": False}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0 or length > 20 * 1024 * 1024:  # 和 vision 的 20MB 上限一致
                self._json({"ok": False, "error": "照片太大（最多 20MB）"}, 413)
                return
            body = self.rfile.read(length)
            # 魔数校验（不信任 Content-Type）：jpg/png/gif/webp
            _magic = [(b"\xff\xd8", ".jpg"), (b"\x89PNG", ".png"),
                      (b"GIF8", ".gif"), (b"RIFF", ".webp")]
            ext = next((e for m, e in _magic if body[:4].startswith(m)), None)
            if ext is None:
                self._json({"ok": False, "error": "只支持 jpg/png/gif/webp 图片"}, 400)
                return
            inbox = os.path.join(paths.DATA_DIR, "inbox")
            os.makedirs(inbox, exist_ok=True)
            fpath = os.path.join(
                inbox, f"photo_{time.strftime('%H%M%S')}_{secrets.token_hex(3)}{ext}")
            with open(fpath, "wb") as f:
                f.write(body)
            input_queue.put(("web", fpath))
            self._json({"ok": True})
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # ⚠️ 安全修复（2026-08-22）：CORS 白名单——只允许我们的页面读响应
        #   （8080 本地服务器页面 / file:// 直开页面的 Origin 是 "null"）。
        #   恶意网页（其他网站）的响应被浏览器拦截，读不到识别文本/状态。
        origin = self.headers.get("Origin", "")
        if origin in ("http://127.0.0.1:8080", "null"):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 不打印每次请求的日志，保持终端干净
        pass


def start_web_bridge():
    """启动网页桥服务（127.0.0.1:8800）。端口被占也不影响聊天"""
    try:
        # 清理 data/inbox/ 里 7 天前的照片（传照片用的临时收件箱）
        inbox = os.path.join(paths.DATA_DIR, "inbox")
        if os.path.isdir(inbox):
            for name in os.listdir(inbox):
                p = os.path.join(inbox, name)
                try:
                    if time.time() - os.path.getmtime(p) > 7 * 86400:
                        os.remove(p)
                except OSError:
                    pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 8800), WebBridge)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print("  🌐 网页语音已就绪：浏览器打开 XiaoLi.html，点🎤按钮直接说话")
    except OSError as e:
        print(f"  （网页桥没起来（{e}），终端聊天不受影响）")


# O5 纪念日预告防唠叨标记：同一天只注入一次（她"惦记着"但不每轮都提）
_anniv_hinted_date = ""


def main():
    global diary, her_heart, facts, voice_on, _unfinished, _anniv_hinted_date, _call_mode, _bridge_token
    # ⚠️ 安全修复（2026-08-22）：每次启动生成新的网页桥会话 token
    #   （随 face_state.js 下发，网页请求必须带它——恶意网页读不到）
    _bridge_token = secrets.token_hex(16)
    voice_on = "--voice" in sys.argv  # python chat.py --voice → 语音模式
    print("=" * 40)
    print("  小李上线啦～ 跟她聊天吧！")
    print("  输入 exit 或 再见 退出；语音开/语音关 切换声音；通话开/通话关 免按键对话")
    print("  试试：记忆（她记得你什么）｜心情（她的心）｜简报（她今天知道的世界）")
    if voice_on:
        print("  🔊 语音模式：她说的话会念出来（台湾腔）")
    # 启动自检（2026-08-23）：一眼看出什么没配好（只看配置和文件，不加载模型）
    # 用 [OK]/[!!] 不用 emoji——GBK 终端打印 emoji 会 UnicodeEncodeError 崩掉
    def _mark(ok_):
        return "[OK]" if ok_ else "[!!]"
    print("  ── 启动自检 ──")
    print(f"  {_mark(bool(config.DEEPSEEK_API_KEY))} 大脑（DeepSeek key）" + (
        " ← 没配！先跑 python scripts/setup_keys.py --set deepseek" if not config.DEEPSEEK_API_KEY else ""))
    print(f"  {_mark(bool(config.VOLC_API_KEY))} 火山语音（备用音色）" + (
        " ← 没配就用 edge 晓晓（普通话，非台湾腔）" if not config.VOLC_API_KEY else ""))
    print(f"  {_mark(bool(voice_on))} 语音模式" + (
        " ← 没开 --voice 她只有文字不开口（python -m xiaoli.chat --voice）" if not voice_on else ""))
    print(f"  {_mark(os.path.isdir(tts_local._MODEL_DIR))} 本地克隆声音" + (
        " ← models/Qwen3-TTS 没下载，语音走火山/edge" if not os.path.isdir(tts_local._MODEL_DIR) else ""))
    print(f"  {_mark(os.path.isdir(whisper_stt._MODEL_PATH))} 本地识别" + (
        " ← models/faster-whisper 没下载，「说」走火山识别" if not os.path.isdir(whisper_stt._MODEL_PATH) else ""))
    print(f"  {_mark(not _backup_due())} 数据备份" + (
        " ← 超过 7 天没备份了，跑 python scripts/backup.py 保护聊天记录" if _backup_due() else ""))
    print("  ────────────")
    # 本地识别/合成预热（2026-08-22）：后台加载 whisper + Qwen3-TTS 模型（各≈10-20秒），
    # 第一次说话前就绪——不预热的话第一次开口/开口要等加载（降级火山兜底）
    whisper_stt.preload()
    tts_local.preload()
    print("=" * 40)

    # 读日记本：她记得之前的一切（防失忆！）
    diary = context.load_diary()
    if diary.get("summary"):
        print(f"  （她记得过去：{diary['summary'][:50]}…）")
    elif diary.get("messages"):
        print("  （她记得你们之前聊过的话哦～）")

    # 读"心"：她的心情和好感，先应用时间衰减（她记得你离开了多久）
    her_heart = heart_mod.load_heart()
    heart_mod.apply_time_decay(her_heart)
    if her_heart["mood"]["primary"] == "melancholy":
        print(f"  （她有点想你…最近一次互动：{her_heart['last_interaction']}）")

    # 读档案记忆：她记得关于你的事（B.3），先做记忆老化（太久没提的会淡忘）
    facts = memory_mod.load_facts()
    if facts:
        facts, forgotten = memory_mod.decay(facts)
        if forgotten:
            print(f"  （她渐渐淡忘了一些小事…{forgotten}件）")
        memory_mod.save_facts(facts)
        if facts:
            print(f"  （她记得关于你的 {len(facts)} 件事）")

    # B.2 启动检查：程序关闭期间到点的提醒 → 开机浮现（她给你留了话）
    missed = proactive.get_missed_reminders()
    if missed:
        for r in missed:
            print(f"  ⏰（她给你留了话：{r['content']}）")
        proactive.dismiss_reminders([r["id"] for r in missed])

    # B.2 启动时若正好落在节奏窗口（如早上7-9点），她先开口说早安
    window_key, window_name = proactive.get_window_now()
    if window_key and proactive.should_fire_window(window_key):
        handle_event("节奏", f"现在是{window_name}时间，你主动开口", datetime.now().strftime("%H:%M"))
        proactive.mark_window_fired(window_key)

    # B.2 后台调度器：每秒检查节奏窗口和到期提醒
    sched = proactive.Scheduler(handle_event)
    sched.start()

    # O2 通话模式（v2 P2）：与"按键说话"共用互斥锁（不会同时抢麦克风），
    # 识别出的每句话走正常聊天流（能打断她、能触发她的小脾气，跟打字一模一样）
    _call_mode = call_mode.get()
    _call_mode._lock = listen_lock
    _call_mode.on_text = _call_text

    # 终端输入放到后台线程读（网页语音输入也要进同一队列）
    threading.Thread(target=terminal_reader, daemon=True).start()
    # 网页桥：XiaoLi.html 的🎤按钮靠它
    start_web_bridge()
    # 互动计时从启动开始：你一阵子没说话，她会主动找你（不是只有你找她）
    proactive.mark_activity()

    while True:
        # 每轮先检查时间衰减（幂等：同一个"离开期"只扣一次）——
        # 程序连续运行跨天时，衰减不会只在启动时发生
        heart_mod.apply_time_decay(her_heart)
        try:
            src, raw_input = input_queue.get()
        except KeyboardInterrupt:
            print()
            break
        user_input = raw_input.strip()
        # 打断：你有话要说 → 她正在说的话立即停，也不再"补充"（真人被打断就是这样）
        global _interrupted
        if user_input:
            _interrupted = True
            voice.stop_playing()
            mark_user_speak()  # 你接话了 → 她等你说完，不再抢话
        # 命令只认终端来源（网页🎤说的话不可能是"exit"）
        if src == "terminal":
            if not user_input:
                # 空回车：提示用法，不把空消息发给大脑
                print("  （直接打字聊天；输入「说」可开口语音输入哦～）")
                continue
            if user_input in ("语音开", "语音关"):
                voice_on = user_input == "语音开"
                print(f"  🔊 语音已{'开启' if voice_on else '关闭'}")
                update_face("")  # 刷新网页🔊开关状态
                continue
            # O2 通话模式：免按键对话（麦克风常开监听，你说话她就回）
            if user_input in ("通话开", "通话关"):
                if user_input == "通话开":
                    _call_mode.start()
                else:
                    _call_mode.stop()
                continue
            # 查看她的心（2026-08-23）：她记得什么 / 她现在什么心情
            if user_input in ("记忆", "她记得什么", "查记忆"):
                _show_facts()
                continue
            if user_input in ("心情", "好感", "查心情"):
                _show_heart()
                continue
            if user_input in ("简报", "世界简报", "看简报"):
                _show_brief()
                continue
            # 「记住XXX」（2026-08-23）：像网页📌按钮一样存高重要度记忆
            #   （对话里自然说"记住我的生日是5月20"她也会记，这是明确命令版）
            if user_input.startswith(("记住", "记下", "帮我记着")):
                ok, content = _remember_text(user_input)
                if ok:
                    memory_mod.merge_fact(facts, content, importance=9,
                                          category="他特意让我记住的")
                    memory_mod.save_facts(facts)
                    print(f"  📌（她特别记住了：{content}）")
                else:
                    print("  （没听清要记住啥——比如：记住我喜欢喝奶茶）")
                continue
            # 语音输入：输入"说"开始录音 → 火山识别成文字 → 当作你说了这句话
            if user_input in ("说", "语音说", "声控"):
                with listen_lock:
                    spoken_text = stt.listen_once()
                if not spoken_text:
                    continue
                print(f"  （你刚才说：{spoken_text}）")
                user_input = spoken_text
        if user_input in ("exit", "退出", "再见"):
            # 再见前把日记和"心"都存好
            sched.stop()
            with diary_lock:
                context.save_diary(diary)
            heart_mod.save_heart(her_heart)
            print("小李：掰掰喔～下次再来找我玩捏！")
            break

        # 小脾气：程序检测"他提别的女生/夸别人/哄她"→ 写进"心"（App 是游戏主持人，
        # 触发归程序管，不赌 LLM 自觉；表现（怎么吃醋/怎么嘴硬）才由 LLM 发挥）
        _, temper_event = heart_mod.apply_temper(her_heart, user_input)  # 返回 (heart, event)
        if temper_event:
            kind, reason = temper_event
            print(f"  {'💔' if kind == 'jealous' else '🕊️'}（她{'吃醋了' if kind == 'jealous' else '被你哄好了'}：{reason}）")

        # 看照片（2026-08-23，视觉模型）：他发图片路径 → 她"看"了再回应，
        # 不经过文本大脑（一个调用搞定，单张 ~0.0012 元）
        if vision.is_photo_path(user_input):
            handle_photo(user_input)
            continue

        # v2 E2 安慰阶段推进：用上一轮独立分类器判出的他心情（首轮没有 → 关键词兜底）
        # 他难过 → 探索→共情→行动逐轮推进；他转好 → 结束安慰
        _user_mood = her_heart.get("user_mood")
        if _user_mood is None:
            _user_mood = "sad" if any(w in user_input for w in heart_mod.NEGATIVE_WORDS) else "neutral"
        heart_mod.advance_comfort(her_heart, _user_mood)

        # v2.3 世界简报停机补刷（三保险之二）：对话前保证她"已刷到"——
        # 简报过期 → 同步补刷（首次对话慢几秒，之后 24h 内秒过；失败静默旧简报兜底）
        world_brief.ensure_fresh()

        # 唱歌演出链（2026-08-23，Z 节 A 方案=纯放歌）：他叫唱歌 →
        # 清嗓→报歌名→播歌→写"我们的歌"记忆；返回歌名（演了）或 None
        _sung = sing.maybe_sing(user_input)

        # 组装工作台：人设 + 现在时间 + 她的心情 + 档案记忆 + 摘要 + 最近60轮 + 你刚说的话
        messages = context.build_workbench(SYSTEM_PROMPT, diary, user_input)
        messages.insert(2, {"role": "system", "content": heart_mod.describe(her_heart)})
        # v2 E2：安慰阶段提示（他难过时的说话节奏：先听→共情→再带动，不许跳阶段）
        _guide = heart_mod.comfort_guide(her_heart)
        if _guide:
            messages.append({"role": "system", "content": _guide})
        # B.3 档案记忆：按你这句话召回她记得的关于你的事（本地检索，不花API钱）
        facts_block = memory_mod.describe_facts(memory_mod.recall(facts, user_input))
        if facts_block:
            messages.insert(3, {"role": "system", "content": facts_block})

        # O7 打断续说（v2 P2）：他打断她时没说完的话 → 这轮带出，她自然决定续不续
        if _unfinished:
            messages.append({"role": "system", "content": (
                f"【你上次没说完】他打断你时，你还有话没说完：「{'」；「'.join(_unfinished)}」。"
                "他若问起或话题合适，可以自然接上；他若换了话题，就顺着新话题聊，不用硬接。")})
            _unfinished = []

        # O6 工作记忆（v2 P2）：他答应过"待会给你看照片"还没兑现 → 她惦记着（不唠叨）
        _promise_hint = proactive.promise_hint(user_input)
        if _promise_hint:
            messages.append({"role": "system", "content": _promise_hint})

        # 唱歌演出链收尾：她刚唱完歌 → 主对话自然说唱完反馈（撒娇问好不好听/自嘲）
        if _sung:
            messages.append({"role": "system", "content": (
                f"【你刚唱完】你刚才给他唱了《{_sung}》（已经唱完播完了），"
                "现在自然地接一句唱完的话——撒娇问他好不好听、自嘲唱歪了齁、"
                "或者顺着他的话聊下去（别再说要不要听歌了，已经唱完了）")})

        # O5 纪念日预告（v2 P2）：今天/3/7天内是纪念日 → 她心里惦记着（一天只提一次）
        _today = datetime.now().strftime("%Y-%m-%d")
        if _today != _anniv_hinted_date:
            _annivs = memory_mod.find_anniversaries(facts, datetime.now())
            if _annivs:
                _anniv_hinted_date = _today
                _anniv_lines = "；".join(
                    f"{c}（{'就是今天' if d == 0 else f'还有{d}天'}）" for c, d in _annivs)
                messages.append({"role": "system", "content": (
                    f"【纪念日】{_anniv_lines}。这是你们重要的日子，你可以自然提一嘴"
                    "（比如撒娇问他怎么安排、能不能陪你）；但别每轮都说，提一次就好。")})

        # 连珠炮程序注入（游戏主持人原则）：真人生气是连珠炮，不是一句阴阳怪气。
        # describe 里的"强度高→连珠炮"实测会被长工作台淹没（flash 遵循弱），
        # 程序检测高强度生气 → 在消息末尾注入强指令（离输出最近，遵循率最高）
        if her_heart["mood"].get("primary") == "angry" and her_heart["mood"].get("intensity", 0) >= 70:
            messages.append({"role": "system", "content": (
                f"【情绪指令·必做】你现在气炸了（生气强度{her_heart['mood']['intensity']}）！"
                "真人气炸不会只回一句就停，而是一句接一句控诉（连珠炮）。"
                "说话结构：spoken 只写第一句短控诉（十几个字），"
                "后面的控诉全部写进 continuation 数组（2~3条，每条15~25字），"
                "一条比一条气：先翻旧账（他上次也这样）→再控诉现在（他每次都这样）"
                "→最后带一点委屈（呜…）收尾。禁止把话都塞进 spoken 一句，"
                "禁止写\"你去吧\"\"我没事\"这种退让话！")})

        # B.2 程序兜底：他拜托了要定时提醒的事 → 注入提示。
        # 学自 UTSUWA"App是游戏主持人"：机制不能只靠 LLM 自觉，程序检测并提示
        if re.search(r"提醒|叫我|叫我起床|告诉我|别忘了|到时候叫我|叫我去", user_input):
            messages.append({"role": "system", "content": (
                "【内部提示】他拜托了你一件需要定时提醒的事。"
                "如果你答应了，必须在 spoken 末尾加上提醒标签，例如："
                "[reminder:5min]该去开会了[/reminder]（时间用数字+min/hour/day，"
                "30秒写 0.5min）。不加标签 = 你只是口头答应，程序无法真的提醒他。")})

        try:
            reply = call_xiaoli(messages)
            # 提醒兜底 2 层（2026-08-20 实测：flash 在多轮长上下文里会概率性漏标签）：
            # 他求了提醒、她没写标签 → 用更强提示重试一次（内部提示提到最前，
            # 紧贴人设，避免被长历史淹没——"App 是游戏主持人"，机制不靠 LLM 自觉）
            if re.search(r"提醒|叫我|叫我起床|告诉我|别忘了|到时候叫我|叫我去", user_input) \
                    and "[reminder:" not in reply:
                for _ in range(3):  # 最多补写 3 次（e2e 实测：长上下文下 2 次仍可能全漏）
                    # C3 重试瘦身（2026-08-23）：重试只需"他刚求了提醒"的近因上下文，
                    # 全量 60 轮重发是最坏 4 倍调用（且【强制要求】插第 1 位破坏缓存前缀）
                    # → 人设/记忆/动态块保留，历史只留最近 10 轮（~1.5 倍）
                    retry = _thin_for_retry(messages)
                    retry.insert(1, {"role": "system", "content": (
                        "【强制要求】他说了需要定时提醒的事。你必须在这句话的 spoken 末尾"
                        "加上提醒标签：[reminder:时间数字min/hour/day]提醒内容[/reminder]"
                        "（例如：[reminder:30min]该吃药了[/reminder]；30秒写 [reminder:0.5min]）。"
                        "这是机制要求，不是可选项，现在就加。")})
                    reply = call_xiaoli(retry)
                    if "[reminder:" in reply:
                        break
            inner, spoken, suggestion = parse_reply(reply)

            # B.2 提醒机制：她说的话里带 [reminder:5min] 标签 → 存进定时器
            reminders = proactive.parse_reminder_tags(reply)
            if reminders:
                proactive.add_reminders(reminders)

            # 显示双轨 + 续话：内心想法/说出口的话/她想补充的话（一句句停顿后说出来）
            # _interrupt=True：用户回复最高优先级——打断正在播的主动事件/提醒
            say_with_continuation("", inner, spoken,
                                  (suggestion or {}).get("continuation", []),
                                  _interrupt=True)
            for r in reminders:
                print(f"  ⏰（她说会提醒你：{r['content']}，在 {r['trigger_at']}）")
            # 她还想继续说？开个后台线程：等你几秒，你没接话她就继续主动说（最多3轮）
            maybe_continue_talking(suggestion)

            # 更新"心"：LLM建议为主、本地基线兜底，都严格钳制
            if suggestion and (suggestion.get("mood_change") or suggestion.get("affection_delta") is not None):
                heart_mod.merge_llm_suggestion(her_heart, suggestion, temper_event)
            else:
                # 兜底：用本地启发式基线（LLM没给建议时程序自己算）
                baseline = heart_mod.analyze_message(user_input)
                heart_mod.merge_llm_suggestion(her_heart, {"affection_delta": baseline})
            her_heart["last_interaction"] = context.now_str()
            heart_mod.save_heart(her_heart)

            # B.3 提取新记忆：LLM 建议为主（importance 6）+ 本地正则兜底（importance 5）
            # v2 M5 躯体标记：记下时的情绪存进 valence（高情绪记忆忘得更慢）
            _val = her_heart["mood"].get("primary", "neutral")
            new_count = 0
            if suggestion:
                for m in (suggestion.get("new_memory") or []):
                    if isinstance(m, str) and m.strip():
                        if memory_mod.merge_fact(facts, m.strip(), importance=6, valence=_val):
                            new_count += 1
            for content, category in memory_mod.extract_facts_local(user_input):
                if memory_mod.merge_fact(facts, content, importance=5, category=category, valence=_val):
                    new_count += 1
            # v2 每日维护（每天最多一次，reflected_on 标记防重复；失败跳过不打扰）：
            # M4 睡前反思提纯（Generative Agents）——把今天聊的提炼成"结论型"记忆；
            # C1 一致性检查器（Drift 论文）——比对今天说的话和人设，漂移就记纠偏事实
            today_key = datetime.now().strftime("%Y-%m-%d")
            if diary.get("reflected_on") != today_key:
                diary["reflected_on"] = today_key
                for content, rval in context.reflect(diary):
                    if memory_mod.merge_fact(facts, content, importance=7, category="反思", valence=rval):
                        new_count += 1
                for content, cat in context.check_consistency(diary):
                    if memory_mod.merge_fact(facts, content, importance=8, category=cat):
                        new_count += 1
            if new_count:
                print(f"  📝（她记住了：{new_count} 件关于你的事）")
            # 🎂 生日提取：从你说的（或她记得的）话里找"生日是X月X日" → 存生日档案
            # （特殊日子模块专用，单独存不怕记忆老化忘记）
            bd = special.extract_birthday(user_input)
            if not bd and suggestion:
                for m in (suggestion.get("new_memory") or []):
                    if isinstance(m, str):
                        bd = special.extract_birthday(m)
                        if bd:
                            break
            if bd:
                old = special.load_birthday()
                if not old or (old["month"], old["day"]) != bd:
                    special.save_birthday(*bd)
                    print(f"  🎂（她记住你的生日了：{bd[0]}月{bd[1]}日）")
            # 🍢 出门跟进：你说要去吃烧烤/逛街 → 她记住，过一阵子主动问结果；
            #    说"我回来了" → 取消（她不用再问了）。她不能真的出门，
            #    但会"住在手机里陪你去"（2026-08-20 用户："请她吃烧烤实现不了"）
            proactive.cancel_outings_if_back(user_input)
            outing = proactive.extract_outing(user_input)
            if outing:
                proactive.add_outing(outing)
                print(f"  📌（她记住你要去{outing}了，待会儿要问你玩得怎么样）")
            # O6 工作记忆：他说"待会给你看照片/发你文件" → 记进工作槽（下轮带出）
            if proactive.promise_scan(user_input):
                print("  📌（她记得你说要给她看/发什么，回头要问你要）")
            # 记忆老化：太久没提的会淡忘（每7天-1重要度，降到0删除）
            facts, forgotten = memory_mod.decay(facts)
            if forgotten:
                print(f"  （她渐渐淡忘了一些小事…{forgotten}件）")
            memory_mod.save_facts(facts)

            # 互动计时：你说过话了，她不用急着找话（90秒后她才可能主动开口）
            proactive.mark_activity()
            # v2 P2 话题转移检测：连续极短回应（嗯/哈哈）→ 标记话题可能耗尽
            proactive.mark_reply_len(user_input)

            # v2 E1 独立情绪分类器：主回复后后台判你的心情（不阻塞），
            # 结果驱动下一轮的安慰阶段（E2）和等待节奏（P4）
            threading.Thread(target=_async_classify_mood, args=(user_input,), daemon=True).start()

            # 写日记：存下你说的和她说的（带时间戳），超长时压缩，然后保存
            stamp = context.now_str()
            with diary_lock:
                diary["messages"].append({"role": "user", "content": user_input, "time": stamp})
                diary["messages"].append({"role": "assistant", "content": reply, "time": stamp})
                context.compress(diary)
                context.save_diary(diary)
        except Exception as e:
            print(f"[出错了：{e}] 检查 config.py 里的API配置是否正确")


def _async_classify_mood(text):
    """后台线程：独立情绪分类器（v2 E1）——判他刚才那句话的心情，
    更新 her_heart["user_mood"]（下一轮推进安慰阶段/等待节奏用；失败保持旧值）"""
    try:
        label = context.classify_user_mood(text)
        her_heart["user_mood"] = label
    except Exception:
        pass


if __name__ == "__main__":
    main()
