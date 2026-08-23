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
    4. 台语特有词替换为普通话说法"""
    t = re.sub(r"\[.*?\]", "", text)
    t = re.sub(r"<[^>]*>", "", t)  # 尖括号整段剥（含内容，防残留念出）
    t = t.replace("～", "，").replace("~", "，")
    # 只保留中英文、数字、常用标点
    t = re.sub(r"[^一-鿿A-Za-z0-9，。！？、；：…·\"'—\s]", "", t)
    for tw, cn in _SPEAK_MAP.items():
        t = t.replace(tw, cn)
    return t.strip()


# ============================================
# 流式朗读切句器（2026-08-22，移植 dsh-voice-ai-girlfriend）：
#   dsh-plugin/src/client/voice/sentences.ts 的 splitSentences——
#   "整段合成完再播" → "按句切分、逐句合成、第一句响起后面在合成"（说话延迟减半）
#   移植要点：
#   ① 句号级终结符切分（。！？!?…；；）——语义完整段，真人句间有停顿
#   ② 过滤碎片（纯标点/单字符，dsh 的 isTrivial）——"！"单蹦出来绝不送 TTS
#   ③ 超长句硬切（>48 字）：断点优先落在弱标点（，、：），没有才硬切——
#      长句不切的话第一声要等整句合成完，流式就白做了
#   ④ 尾部无标点也算整句（小李是整段拿到文本，没有 dsh 的"生成中 partial"状态）
# ============================================
_TERMINATORS = "。！？!?…；;"
_WEAK_BREAKS = "，、：:"
_MAX_SENTENCE = 48
_SENTENCE_SPLIT_RE = re.compile("(?<=[%s])" % _TERMINATORS)


def _is_trivial(s):
    """碎片过滤：去掉空白后长度 < 2，或全是标点符号 → 不播"""
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
#   _tts_queue：T 生产 (sr, pcm)，P 消费；None 哨兵 = 合成完毕（P 播完收工）
_gen = 0
_tts_queue = queue.Queue()
_SENTENCE_GAP = 0.25  # 句间微停顿（真人说话句与句之间本来就有的呼吸感）


def _synth_worker(text, emotion, gen, on_started):
    """合成线程 T：逐句合成入队（串行链）。gen 变了 → 立即停手（被打断/被顶掉）。
    单句失败 → 跳过继续（dsh 的 catch 后继续链；语音失败绝不打断整段话）"""
    global _speaking_until
    sentences = split_sentences(text)
    first = True
    for s in sentences:
        if gen != _gen:
            return
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
        # 口型同步：滚动延长"说话截止时间"（每句入队 + 音频时长 + 缓冲）
        _speaking_until = time.time() + len(pcm) / (sr * 2) + 0.5
        _tts_queue.put((sr, pcm))
        if first and on_started:
            first = False
            try:
                on_started()  # 第一句入队 → 网页动嘴（chat.py 写 face_state.js）
            except Exception:
                pass
    _tts_queue.put(None)  # 哨兵：全部合成完毕


def _play_worker(gen, on_done):
    """播放线程 P：FIFO 逐句播。队列空 → 等 T；None 哨兵 → 收工。
    被打断（gen 变了）→ 丢弃剩余句子立即收工"""
    _playing.set()  # 开始播 → 通话模式暂停监听（半双工门控）
    try:
        while True:
            item = _tts_queue.get()
            if item is None or gen != _gen:
                break  # 哨兵收工 / 被打断（sd.stop 已让 sd.wait 返回）
            sr, pcm = item
            data = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            sd.play(data, sr)
            sd.wait()  # 这句播完（或被 sd.stop 打断）返回
            if gen != _gen:
                break
            if _SENTENCE_GAP:
                time.sleep(_SENTENCE_GAP)  # 句间微停顿（被打断时最多多停 0.25s，无妨）
    except Exception:
        pass
    # 正常播完才归零口型时间戳；被新话顶掉时留给新线程管（防旧线程清掉新话的标记）
    global _speaking_until
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
        _playing.clear()
        if on_done:
            try:
                on_done()
            except Exception:
                pass
        return
    text = speakable(text)  # 清洗（指令由 tts_api 在清洗之后拼）
    _gen += 1  # 新的话来了 → 旧队列让位（旧合成/播放线程下次检查会收手）
    gen = _gen
    # 清掉旧线程可能残留的队列（竞态残留无害：P 线程播到它时 gen 检查会丢弃）
    while True:
        try:
            _tts_queue.get_nowait()
        except queue.Empty:
            break
    threading.Thread(target=_synth_worker, args=(text, emotion, gen, on_started),
                     daemon=True).start()
    threading.Thread(target=_play_worker, args=(gen, on_done), daemon=True).start()


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


def speak_voice(text, emotion=None):
    """给 chat.py 用的简单接口：只说，不阻塞"""
    play_speech(text, emotion=emotion)


if __name__ == "__main__":
    # 自测：合成一句话并播放
    play_speech("寶貝，人家好想你喔～今天有沒有想我呀？")
    print("（若没听到声音，检查：1.edge-tts联网 2.声卡/音量 3.sounddevice设备）")
