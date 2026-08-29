# ============================================
# 小李的语音层（C）
# 合成三层降级链（2026-08-22）：本地克隆小李音色（tts_local.py，0 元/月）
#   → 火山引擎"甜美台妹"（tts_api.py 直出 24k PCM，情绪变声只在火山路径）
#   → 降级：edge-tts 晓晓（miniaudio 解码）
# 播放：sounddevice（PortAudio，跨线程安全）
# 2026-08-21 实测教训 M.4.2：mci 播放的"播完检测"（status mode 轮询）在另一个线程执行
# 不可靠——Windows MCI 要求同线程调用，跨线程 status 立即失败 → on_done 提前触发 →
# "一句话还没说完第二个声音就来了"（连珠炮下一句抢着播，双声重叠）。
# 换 sounddevice：sd.play + sd.wait 在播放线程内完成，播完才回调，时序准确。
# ============================================

import asyncio
import collections
import os
import queue
import re
import tempfile
import threading
import time

import edge_tts
import miniaudio
import numpy as np
import sounddevice as sd

from xiaoli import tts_api, tts_local

# 2026-08-22 语音黑匣子：每次合成走哪条路（火山/降级edge）都记进 voice.log，
# 排查"声音变成普通话"这类问题不用猜——看日志就知道
_VOICE_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "voice.log")


def _vlog(msg):
    try:
        with open(_VOICE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


# 播放状态（v2 O2 半双工门控）：她正在说话 → 通话模式暂停监听
# （不然她自己的声音会触发识别，形成"她说一句自己被识别一句"的死循环）
_playing = threading.Event()

# 2026-08-22 口型同步：她正在说话的"截止时间戳"（epoch 秒）。
# 合成成功 → now + 音频时长 + 缓冲；播完/打断/失败 → 0。
# chat.py update_face 把它写进 face_state.js，网页轮询到 → Live2D 嘴巴动
_speaking_until = 0.0


def is_playing():
    """她是否正在播放语音（通话模式用；键控说话用不上）"""
    return _playing.is_set()


def speaking_until():
    """她这次说话的截止时间戳（网页口型同步用；0 = 没在说话）"""
    return _speaking_until

# 语音路线（2026-08-20 实测迭代）：
# 1. 微软台湾声线（zh-TW）→ 逐字慢读，人机感强（用户A/B确认）
# 2. 微软晓晓（zh-CN）→ 自然但普通话，没有台湾腔
# 3. 火山引擎"甜美台妹"BV025_streaming → 台湾腔+自然（当前方案）
# 降级链：火山 → edge-tts 晓晓（未配置火山时）
VOICE = "zh-CN-XiaoxiaoNeural"  # edge-tts 降级声线
RATE = "+0%"  # 正常语速

# 台语/方言特有词 → 普通话说法（晓晓读不出的词，配音时替换；显示文字不变）
_SPEAK_MAP = {
    "呷飽未": "吃飽了沒",
    "呷飽": "吃飽",
    "呷": "吃",
}


def speakable(text):
    """把她说的话转成"语音可读文本"（显示文本保持不变）：
    1. 去掉提醒标签、波浪号（~ 会让TTS怪停顿）
    2. 整段剥掉 <...>（2026-08-22：LLM 偶发输出尖括号标注，内容残留会被念出来）
    3. 去掉 emoji/特殊符号
    4. 台语特有词替换为普通话说法
    （<pause...> 停顿标签在调用方已被 _protect_pauses 转成占位符，不受影响）"""
    t = re.sub(r"\[.*?\]", "", text)
    t = re.sub(r"<[^>]*>", "", t)  # 尖括号整段剥（含内容，防残留念出）
    t = t.replace("～", "，").replace("~", "，")
    # 只保留中英文、数字、常用标点
    t = re.sub(r"[^一-鿿A-Za-z0-9，。！？、；：…·\"'—\s]", "", t)
    # 连续标点归一（2026-08-23 对照 GPT-SoVITS：多连标点会干扰模型停顿判断/触发误静音）
    t = re.sub(r"[！!]{2,}", "！", t)
    t = re.sub(r"[？?]{2,}", "？", t)
    t = re.sub(r"[。]{2,}", "。", t)
    t = re.sub(r"…{3,}", "……", t)  # 保留双省略号的拖沓感
    for tw, cn in _SPEAK_MAP.items():
        t = t.replace(tw, cn)
    return t.strip()


# ============================================
# <pause/> 停顿标签（2026-08-23，学自 Open-LLM-VTuber 的 [pause] 设计）：
#   真人研究点名项（human-like-dialogue.md）——"停顿由她决定"比固定停顿自然。
#   LLM 在句尾写 <pause/>（或 <pause:1.5> 指定秒数）→ 程序转静音段入队播放。
#   实现：speakable 会剥尖括号 → 先保护成占位符 PH<毫秒>（PH=pause 缩写；
#   **2026-08-23 两代占位符都踩坑**：①13 字符 PAUSEHOLD1500 被 16 字硬切从
#   中切开 → 停顿错位+碎片句；②单字符 $¶ 是符号，speakable 清洗只留字母
#   数字 → $¶ 被删、裸数字残留文本里 → 停顿变"1500你聽我說"噪音。PH+数字
#   两字符：speakable 杀不到（字母数字）、16 字硬切落点都在逗号（占位符前
#   的逗号优先）→ 完整进入下一段）→ 逐句解析 → 该句播完后插静音段。
# ============================================
_PAUSE_RE = re.compile(r"<pause(?::([0-9]+(?:\.[0-9]+)?))?/?>", re.IGNORECASE)
_PAUSE_HOLD_RE = re.compile(r"PH[0-9]*")  # 占位符：PH + 毫秒（纯数字，小数点会被 speakable 删掉）
_PAUSE_DEFAULT = 0.8  # 真人深吸一口气 ≈ 0.8s（句间默认只有 0.25s）
_PAUSE_MIN = 0.3
_PAUSE_MAX = 2.5


def _protect_pauses(text):
    """<pause:1.5> → PH1500（毫秒整数；speakable 之前调用，防清洗杀掉）"""
    return _PAUSE_RE.sub(
        lambda m: f"PH{int(float(m.group(1)) * 1000) if m.group(1) else ''}",
        text)


def _parse_pauses(s):
    """句子里的停顿占位 → (干净句, 停顿秒数)。PH（默认0.8s）/PH1500（毫秒）。"""
    m = _PAUSE_HOLD_RE.search(s)
    if not m:
        return s, 0.0
    clean = _PAUSE_HOLD_RE.sub("", s).strip()
    raw = m.group(0)[2:]  # 去掉 PH 前缀取数字部分（毫秒）
    pause = float(raw) / 1000.0 if raw else _PAUSE_DEFAULT
    return clean, max(_PAUSE_MIN, min(pause, _PAUSE_MAX))


def _silence_pcm(sec):
    """静音段（24k int16 零 PCM）——播放线程当普通音频播，停顿自然衔接"""
    return b"\x00\x00" * int(24000 * sec)


# ============================================
# 流式朗读切句器（2026-08-22，移植 dsh-voice-ai-girlfriend）：
#   dsh-plugin/src/client/voice/sentences.ts 的 splitSentences——
#   "整段合成完再播" → "按句切分、逐句合成、第一句响起后面在合成"（说话延迟减半）
#   移植要点：
#   ① 句号级终结符切分（。！？!?；；）——语义完整段，真人句间有停顿。
#     省略号不算句尾（2026-08-23 用户实测"有省略号会截止得很突然"）：
#     "我跟你說喔……今天超開心"在……处切开 → 第一句播完停 0.25s，听着像话没说完。
#     省略号留在句内，TTS 自己会停顿（省略号=话没说完的延续，不是句号）
#   ② 过滤碎片（纯标点/单字符，dsh 的 isTrivial）——"！"单蹦出来绝不送 TTS
#   ③ 超长句硬切（>16 字）：断点优先落在弱标点（，、：），没有才硬切——
#      长句不切的话第一声要等整句合成完，流式就白做了。
#      48→24→16（2026-08-23 实测数据驱动）：本地克隆合成实测只有 0.53x 实时
#      （1 秒音频要 1.9 秒算，非文档预期 2x——torch.compile/do_sample/双线程
#      并行全试过无效，flash-attn Windows 装不了），播放永远追不上合成 →
#      句间必有空等。空等 = 1.9×下句音频 − 上句播放时长：16 字 ≈ 2.6s 音频，
#      空等压到 1.4~2.3s（听感"她想了一下"，不是"卡住"）；首声 = 第一个
#      弱断点前的短段（通常 5~10 字 ≈ 1.5~3s 出声，之前 24 字要 7s+）。
#   ④ 尾部无标点也算整句（小李是整段拿到文本，没有 dsh 的"生成中 partial"状态）
# ============================================
_TERMINATORS = "。！？!?；;"  # …不在里面：省略号=话没说完的延续，切开语流会断（2026-08-23）
_WEAK_BREAKS = "，、：:"
_MAX_SENTENCE = 16  # 48→24→16（2026-08-23 实测 0.53x 合成速度下最优段长，见模块头 ③）
_SENTENCE_SPLIT_RE = re.compile("(?<=[%s])" % _TERMINATORS)


def _is_trivial(s):
    """碎片过滤：去掉空白后长度 < 2，或全是标点符号 → 不播。
    PH 占位符段含字母 → 天然不算碎片（"齁～好想你。<pause/>"切出的纯停顿
    尾段才能走到 _synth_worker 的"只停不播"分支）"""
    t = re.sub(r"\s", "", s)
    if len(t) < 2:
        return True
    return not re.search(r"[一-鿿A-Za-z0-9]", t)


def _hard_split(s):
    """超长句硬切：断点落在最后面的弱标点（，、：），没有则硬切 _MAX_SENTENCE"""
    if len(s) <= _MAX_SENTENCE:
        return [s]
    out = []
    while len(s) > _MAX_SENTENCE:
        head = s[:_MAX_SENTENCE]
        cut = max(head.rfind(c) for c in _WEAK_BREAKS)
        if cut <= 0:
            cut = _MAX_SENTENCE - 1  # 无弱断点 → 硬切（宁断词不断义）
        out.append(head[:cut + 1])
        s = s[cut + 1:]
    if s.strip():
        out.append(s)
    return out


def split_sentences(text):
    """把一段话切成"逐句合成"用的句子列表（纯函数，可单测）。
    句号级切分 → 过滤碎片 → 超长句硬切。返回非空列表。"""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text)]
    parts = [p for p in parts if p and not _is_trivial(p)]
    sentences = []
    for p in parts:
        sentences.extend(_hard_split(p))
    return sentences


async def _synthesize_edge(text, out_path):
    """edge-tts 降级合成（mp3）"""
    tts = edge_tts.Communicate(text, VOICE, rate=RATE)
    await tts.save(out_path)


def _edge_pcm(text):
    """edge-tts 降级：合成 mp3 → miniaudio 解码成 PCM。返回 (sample_rate, bytes)；失败 → None"""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        asyncio.run(_synthesize_edge(text, tmp_path))
        decoded = miniaudio.decode_file(tmp_path, output_format=miniaudio.SampleFormat.SIGNED16)
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return decoded.sample_rate, decoded.samples.tobytes()
    except Exception:
        try:
            os.remove(tmp_path)
        except (OSError, UnboundLocalError):
            pass
        return None


# 流式朗读（2026-08-22，学 dsh-voice-ai-girlfriend 的句子级管线）：
#   旧版：整段合成完才播（第一声 = 整段合成耗时）
#   新版：切句 → 逐句合成入队（合成线程 T）→ FIFO 逐句播（播放线程 P）——
#         第一句合成完就响（第一声 ≈ 短句合成耗时，长回复延迟减半），
#         播第一句的同时 T 在合成第二句，火山合成快于播放 → 句间几乎无感
# 两个新机制（dsh 的 speaker.ts 移植）：
#   _gen 生成代：stop_playing / 新 play_speech → 代+1 → 排队没播的、合成中的句子作废
#   _tts_queue：T 生产 (sr, pcm, kind, gap)，P 消费；None 哨兵 = 合成完毕（P 播完收工）
#   kind: "speech"=正常句 / "silence"=静音段（<pause/> 停顿、纯停顿尾句）
#   gap: 这句播完后的句间停顿秒数（标点感知，见 _gap_for）
_gen = 0
_tts_queue = queue.Queue()

# 播放历史（AEC far 源，2026-08-23）：播放线程每段记录已送出的 PCM——
# call_mode 打断分支取"最近 ~200ms"当 AEC 的 far 信号（音箱里正在播什么）。
# 上限 ~2s，随播随清；锁保护（播放线程写 / call_mode 读）。
_far_history = collections.deque()
_far_lock = threading.Lock()

# 正在播的句子（A-P1-3 打断续说，2026-08-23）：播放线程 P 每句播前写入，
# chat.py 打断瞬间调 interrupted_tail() 读出"说到哪了"带给她 → 她接着说完。
# 锁保护（P 写 / chat.py 读）。
_cur_text = ""
_cur_dur = 0.0
_cur_t0 = 0.0
_cur_lock = threading.Lock()


def interrupted_tail():
    """打断瞬间她正说到哪了？按已播时长比例估算残余文本。
    残余 < 4 字 → ""（就说完了/刚开口，带进去反而是噪音）。
    格式：'她正说到「今天天气真……」'（聊天注入用，见 chat.py 打断点）。"""
    with _cur_lock:
        if not _cur_text:
            return ""
        played = min(_cur_dur, time.time() - _cur_t0)
        ratio = played / _cur_dur if _cur_dur > 0 else 0.0
        n = int(len(_cur_text) * ratio)
        tail = _cur_text[n:]
        if len(tail) < 4:
            return ""
        return f"她正说到「{tail}……」"


def _record_playback(sr, pcm):
    """播放线程记录一段已送出的 PCM（AEC far 用）"""
    with _far_lock:
        _far_history.append((sr, pcm))
        # 清掉超过 2s 的旧段（坑：不能在遍历 deque 时 popleft——RuntimeError:
        # deque mutated during iteration，播放线程会被吞异常杀掉，测到过）
        while len(_far_history) > 1:
            total = sum(len(p) / (s * 2) for s, p in _far_history)
            if total <= 2.0:
                break
            _far_history.popleft()


def get_recent_playback(ms=200, sr=16000):
    """最近 ~ms 毫秒的播放内容（16k int16 ndarray）——AEC far 信号。
    播放采样率 ≠ 16k（24k 合成）→ 线性降采样；没在播/没有历史 → 空数组。
    （2026-08-24 保留：AEC 修复时用作 far 源）"""
    with _far_lock:
        items = list(_far_history)
    if not items:
        return np.zeros(0, dtype=np.int16)
    target = ms / 1000.0
    picked, t = [], 0.0
    for s, p in reversed(items):
        d = len(p) / (s * 2)
        if picked and t + d > target:
            break
        picked.append((s, p))
        t += d
        if t >= target:
            break
    picked.reverse()
    seg = np.concatenate([np.frombuffer(p, dtype=np.int16) for s, p in picked])
    src_sr = picked[0][0]
    if src_sr != sr:
        idx = np.arange(0, len(seg), src_sr / sr).astype(int)  # 先降采样
        seg = seg[np.minimum(idx, len(seg) - 1)]
    want = int(target * sr)
    if len(seg) > want:
        seg = seg[-want:]  # 再截尾（保留最近 ms）
    return seg

# 起播保护期（2026-08-23 对照 Hermes Agent barge_in 的 grace_seconds）：
# 全双工监听下，她自己的起播声（无 AEC 时会从麦克风听到）会立刻被当成
# "用户插话"打断她自己 → 起播后 GRACE_SECONDS 内不响应打断
GRACE_SECONDS = 0.4
_grace_until = 0.0


# 2026-08-24 已删 in_grace()：打断功能移除后无调用方（_grace_until 保留，播放线程仍维护）


# 标点感知句间停顿（2026-08-23 对照 sherpa-onnx silence_scale 思路：
# 停顿归 TTS 引擎管、播放层只补引擎不念的地方）：
#   句尾是终结符（。！？；）→ 引擎已经念了 0.5-0.8s 停顿，播放层只补 0.3s 衔接
#   句尾是弱标点（，、：）→ 超长句硬切断点，0.15s（别打断语流，机关枪感来源之一）
#   句尾无标点 → 引擎没念任何停顿，0.5s 全靠播放层（真人说完一句的呼吸感）
# 2026-08-23 对照 GPT-SoVITS 实测：无标点句尾 + 0.25s 固定间隔 = "机关枪"听感重灾区
# 2026-08-23 用户实测"间隔有点长"：句尾终结符引擎已自带 0.5-0.8s 停顿，
# 程序再补 0.30 偏多 → 0.20；无标点句尾 0.50 → 0.35（引擎没停全靠程序，留 0.35 的呼吸感）
_GAP_TERMINATOR = 0.20
_GAP_HARD_CUT = 0.15
_GAP_NO_PUNCT = 0.35


def _gap_for(s):
    """这句播完后的停顿秒数（按句尾字符查表）。s 是切句器产出的句子（含句尾标点）。"""
    t = s.strip()
    if not t:
        return _GAP_NO_PUNCT  # 空串：无引擎可依，播放层兜底
    tail = t[-1]
    if tail in "。！？!?；;":
        return _GAP_TERMINATOR
    if tail in "，、：:,":
        return _GAP_HARD_CUT
    return _GAP_NO_PUNCT


def _synth_worker(text, emotion, gen):
    """合成线程 T：逐句合成入队（串行链）。gen 变了 → 立即停手（被打断/被顶掉）。
    单句失败 → 跳过继续（dsh 的 catch 后继续链；语音失败绝不打断整段话）"""
    try:
        sentences = split_sentences(text)
        for s in sentences:
            if gen != _gen:
                return
            # <pause/> 停顿：剥占位符 → 干净句 + 该句播完后的停顿秒数
            clean, pause = _parse_pauses(s)
            if _is_trivial(clean):
                # 整句只有停顿标签（"齁～<pause/>"切出的尾巴）→ 只停不播
                # （闭嘴由播放线程管：silence item 播前 _speaking_until = 0）
                if pause:
                    _tts_queue.put((gen, (24000, _silence_pcm(pause), "silence", 0.0, "")))
                continue
            s = clean
            # 三层降级链（2026-08-22）：本地克隆小李音色 → 火山甜妹 → edge 晓晓。
            # 逐句做，单句失败跳过不拖累整段（dsh catch 后继续链同款）。
            sr, pcm = None, None
            local = tts_local.synthesize(s)  # ① 本地克隆（零费用；没就绪/失败 → None）
            if local:
                sr, pcm = local
                _vlog(f"本地OK {len(pcm)/(sr*2):.1f}s 句: {s[:20]}")
            else:
                # ② 火山甜妹（重试 1 次防瞬时抖动——注意：情绪变声只在此路径生效）
                for attempt in range(2):
                    pcm = tts_api.synthesize(s, emotion=emotion)
                    if pcm:
                        break
                    _vlog(f"火山失败(第{attempt+1}次) 句: {s[:20]}")
                    time.sleep(0.5)
                if pcm:
                    sr = 24000
                    _vlog(f"火山OK {len(pcm)/48000:.1f}s 句: {s[:20]}")
            if pcm is None:
                # ③ edge 晓晓（音色完全不同，最后手段）
                edge = _edge_pcm(s)
                if not edge:
                    _vlog(f"edge也失败 句: {s[:20]}")
                    continue  # 这句跳过，后面的继续说
                _vlog(f"降级edge-tts 句: {s[:20]}")
                sr, pcm = edge
            if gen != _gen:
                return
            # 口型/停顿统一由播放线程管（播前张、静音段闭、播完归零——时序才准，
            # 合成线程提前设会"嘴动了声还没响"）。这里只入队：
            #   句子 → (sr, pcm, "speech", gap)   gap = 标点感知句间停顿
            #   <pause/> → (24000, 静音, "silence", 0)  静音段自带停顿，不再叠加 gap
            # 5 元组（2026-08-23 批3 A-P1-3）：末位 text = 这句的文本——播放线程用它
            # 跟踪"正在播到哪"，打断时算出残余（他插话时她正说到「…」）
            # 6A：队列项带代际 (gen, item)——P 按代际丢弃陈旧项
            _tts_queue.put((gen, (sr, pcm, "speech", _gap_for(s), s)))
            if pause:
                # <pause/> → 该句播完插静音段（她"深吸一口气"再往下说）
                _tts_queue.put((gen, (24000, _silence_pcm(pause), "silence", 0.0, "")))
    except Exception as e:
        # 4B 修复：T 裸奔异常会杀死线程 → None 哨兵永不入队 → P 永久等 → 卡 True。
        # 任何异常都兜住，至少让 P 收到哨兵收工（声音没了但门控不坏）
        _vlog(f"合成线程异常：{e}")
    finally:
        _tts_queue.put((gen, None))  # 哨兵：全部合成完毕（或中断/异常）——必须投，
        # 否则 P 永久阻塞在 get()（gen 代际保证被打断的旧哨兵不会掐断新话）


def _play_worker(gen, on_done, on_started):
    """播放线程 P：FIFO 逐句播。队列空 → 等 T；None 哨兵 → 收工。
    被打断（gen 变了）→ 丢弃剩余句子立即收工。
    口型唯一真源（2026-08-23，静音段嘴张着的 bug 根治）：
    speech 播前张嘴（滚动延长）、silence 播前闭嘴、收工归零——
    合成线程不再碰 _speaking_until，嘴张的时机 = 声音响的时机"""
    global _speaking_until, _grace_until
    _playing.set()  # 开始播 → 通话模式暂停监听（半双工门控）
    first = True
    try:
        while True:
            # 2026-08-23 全面排查（高危）：无超时 get() + T 被打断不投哨兵 →
            # P 永久阻塞在队列上 → is_playing() 永久 True → 通话模式失聪且
            # stop_playing 救不了（sd.stop 对 queue.get 无效）。get 加超时，
            # 每 0.2s 醒来检查 gen——被打断后最迟 0.2s 收工。
            try:
                item = _tts_queue.get(timeout=0.2)
            except queue.Empty:
                if gen != _gen:
                    break  # 被打断且无新句可等 → 收工
                continue  # T 还在合成 → 继续等
            item_gen, item = item  # 6A：队列项带代际——陈旧项（旧 T 迟到入队的
            # 句子/哨兵）被丢弃，不插进新一代播放（旧 None 会掐断新话）
            if item_gen != gen:
                continue
            if item is None or gen != _gen:
                break  # 哨兵收工 / 被打断（sd.stop 已让 sd.wait 返回）
            sr, pcm, kind, gap, *rest = item
            text = rest[0] if rest else ""  # 5 元组兼容（A-P1-3：残余估算用）
            if kind == "speech":
                _speaking_until = time.time() + len(pcm) / (sr * 2) + gap + 0.5
            else:
                _speaking_until = 0.0  # 静音段 → 闭嘴（<pause/> 时嘴不能张着）
            with _cur_lock:
                if kind == "speech":
                    # 正在播的句子 → 打断时按已播时长比例估算"说到哪了"
                    _cur_text = text
                    _cur_dur = len(pcm) / (sr * 2)
                    _cur_t0 = time.time()
                else:
                    _cur_text = ""
                    _cur_dur = 0.0
            if on_started:
                try:
                    on_started()  # 第一段开播 → 网页动嘴（chat.py 写 face_state.js）
                except Exception:
                    pass
                on_started = None
            data = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            _record_playback(sr, pcm)  # AEC far：记录"即将播出"的内容（播完再记就晚了）
            if first:
                # 5A 高危修复：起播保护从"第一声响起"算起（不是 play_speech 入队
                # 时刻——合成耗时几百 ms~数秒，原来锚错位置 → 声音响时保护已过期）
                _grace_until = time.time() + GRACE_SECONDS
                first = False
            sd.play(data, sr)
            sd.wait()  # 这句播完（或被 sd.stop 打断）返回
            if gen != _gen:
                break
            if kind == "speech" and gap:
                time.sleep(gap)  # 标点感知句间停顿（静音段自带停顿，不叠加）
    except Exception as e:
        # 2026-08-23 静音排查：P 线程异常被吞 → 合成 OK 但无声、无日志。任何
        # 播放异常必须打印出来（静音比日志吵更糟）
        print(f"[播放线程异常] {e!r}", flush=True)
    with _cur_lock:
        _cur_text = ""  # 收工 → 不再有"正说到一半"的句子
        _cur_dur = 0.0
    # 正常播完才归零口型时间戳；被新话顶掉时留给新线程管（防旧线程清掉新话的标记）
    # 1B 高危修复：_playing.clear() 也要同样的代际守卫——旧线程收工时无脑 clear，
    # 会清掉新线程刚 set 的播放标记 → is_playing() 假 False → 通话模式恢复监听 →
    # 她的话还在音箱响 → 鬼打墙（她识别到自己）。
    if gen == _gen:
        _speaking_until = 0.0
        _playing.clear()  # 播完/被打断 → 通话模式恢复监听
    if on_done:
        try:
            on_done()
        except Exception:
            pass


def play_speech(text, speak=True, on_done=None, on_started=None, emotion=None):
    """把一段话变成语音并播放（流式：按句边合成边播，第一声快一半）。
    声音：火山甜妹，未配置时晓晓；文本自动转成语音可读版。
    非阻塞：合成+播放都由后台线程负责，不卡对话；被 stop_playing 打断时立即停。
    speak=False 或任何一步失败 → 静默返回（文字对话照常进行）
    on_done：整段播放完成（或被打断/失败）后回调——"说完再接着说"的钩子
    on_started（口型同步）：第一句合成成功、马上开始播时回调——
    chat.py 借它把 speaking_until 写进 face_state.js（网页驱动 Live2D 嘴巴动）
    emotion：情绪 → 火山语音参数（语速/音量/音调），不注入任何文本指令"""
    global _gen, _speaking_until
    if not speak or not text or not text.strip():
        _gen += 1  # 静默返回也作废旧线程（旧合成线程可能还在跑，不写口型状态）
        _speaking_until = 0.0
        try:
            sd.stop()  # 6B 修复：旧句还在播时清了 _playing 会鬼打墙——先停掉声音
        except Exception:
            pass
        _playing.clear()
        if on_done:
            try:
                on_done()
            except Exception:
                pass
        return
    text = speakable(_protect_pauses(text))  # 清洗（指令由 tts_api 在清洗之后拼）；<pause> 先保护
    _gen += 1  # 新的话来了 → 旧队列让位（旧合成/播放线程下次检查会收手）
    gen = _gen
    try:
        sd.stop()  # 4A 修复：旧句还在播时新线程 sd.play 会开第二个并发流而失败
        # （新话静默丢失）——先停旧流，让旧 P 收工，新 P 独占设备
    except Exception:
        pass
    # 清掉旧线程可能残留的队列（竞态残留无害：带代际的陈旧项会被新 P 丢弃）
    while True:
        try:
            _tts_queue.get_nowait()
        except queue.Empty:
            break
    threading.Thread(target=_synth_worker, args=(text, emotion, gen),
                     daemon=True).start()
    threading.Thread(target=_play_worker, args=(gen, on_done, on_started), daemon=True).start()


def stop_playing():
    """打断：立刻停止她正在说的话（用户插话时调用）。
    生成代 +1（排队没播的、合成中的句子全部作废）+ sd.stop
    （播放线程的 sd.wait() 立即返回 → 检查 gen 变了 → 收工 → on_done 触发）"""
    global _gen, _speaking_until
    _gen += 1
    _speaking_until = 0.0  # 口型同步立即停
    try:
        sd.stop()
    except Exception:
        pass




if __name__ == "__main__":
    # 自测：合成一句话并播放
    play_speech("寶貝，人家好想你喔～今天有沒有想我呀？")
    print("（若没听到声音，检查：1.edge-tts联网 2.声卡/音量 3.sounddevice设备）")
