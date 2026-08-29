# ============================================
# 唱歌演出链（2026-08-23，Z 节研究结论落地：A 方案=纯放歌+演出链）
# 研究结论（docs/research/project-excellence.md Z 节）：
#   - GPT-SoVITS 是 TTS 不是唱歌工具（唱=无调念白，比跑调更糟）
#   - Neuro-sama 唱歌=预渲染翻唱 wav 播放（非实时生成）——我们就走这条路
#   - 真人唱歌行为链：清嗓（A 级文献）→ 报歌名 → 唱 → 唱完求反馈（KTV 民族志）
# 演出链 v1（触发归程序管，不赌 LLM）：
#   1. 他提出"唱首歌给我听" → 程序检测关键词（排除"我想唱"这类他自己唱）
#   2. 清嗓（data/sfx/clear_throat.wav，她自己的声音）
#   3. 曲库 data/songs/ 有歌 → LLM 短台词报歌名（她声音说"齁～那我唱《xx》齁"）
#      → 播歌（miniaudio 解码 wav/mp3，歌=预渲染文件，用户放进去）
#   4. "我们的歌"记忆：程序直接写 facts（importance 高，歌→两人关系）
#   5. 返回歌名 → 主循环注入"她刚唱完《xx》"→ 主对话 LLM 自然说唱完反馈
#      （"齁～唱得还可以齁？"）——反馈并入主对话，演出链只做动作层
# 曲库空 → 不演出（返回 None）：她正常对话，persona 会自然说"还没练好齁"，
#   不装、不念白（无调念白是负效果）
# 失败静默：任何一步炸了都不影响主流程（她照常说话）
# ============================================

import os
import re
import threading

import miniaudio
import numpy as np
import sounddevice as sd

from xiaoli import config
from xiaoli import llm  # 统一大脑客户端（C1：连接5s/读取30s 超时）
from xiaoli import memory as memory_mod
from xiaoli import paths
from xiaoli import sfx
from xiaoli import voice

SONGS_DIR = os.path.join(paths.DATA_DIR, "songs")

ANNOUNCE_WAIT_TIMEOUT = 30  # 报歌名播完的最长等待（on_done 兜底几乎必触发，这是双保险）


def _speak_wait(text):
    """播报歌名并等她说完（2026-08-23 竞态修复）：voice.play_speech 非阻塞，
    直接播歌的话 sd.play 是"替换"语义——歌名常被歌顶掉/歌的开头被歌名顶掉。
    on_done 在播完/失败/被打断都会触发 → 不会卡死；播放系统本身炸了 → 不等继续演。"""
    _done = threading.Event()
    try:
        voice.play_speech(text, on_done=_done.set)
    except Exception:
        _done.set()
    _done.wait(ANNOUNCE_WAIT_TIMEOUT)

# 触发词：他叫"她"唱歌（"我想听你唱歌"也要触发——她要唱给他听）
TRIGGER_RE = re.compile(r"唱((一|壹)?(首|支|段|个|只))?歌|唱给我听|来一首|来一段|唱一个|K歌|k歌")
# 排除：他自己唱（"我想唱""我要去唱K""咱们一起唱"……）
EXCLUDE_RE = re.compile(r"我(想|要|去|们|也)(要|去)?唱|自己唱|我来唱|我唱了|一起唱")


def _song_list():
    """曲库：data/songs/ 下的音频文件（wav/mp3 通吃）→ [(歌名, 路径)]"""
    if not os.path.isdir(SONGS_DIR):
        return []
    out = []
    for f in sorted(os.listdir(SONGS_DIR)):
        if f.lower().endswith((".wav", ".mp3", ".m4a", ".flac", ".ogg")):
            out.append((os.path.splitext(f)[0], os.path.join(SONGS_DIR, f)))
    return out


def _play_song(path):
    """播歌：miniaudio 解码（wav/mp3 通吃）→ sounddevice 阻塞播放（同 voice 播放器）"""
    try:
        decoded = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.FLOAT32)
        data = np.frombuffer(decoded.samples, dtype=np.float32)
        if decoded.nchannels > 1:
            data = data.reshape(-1, decoded.nchannels)
        sd.play(data, decoded.sample_rate)
        sd.wait()
        return True
    except Exception:
        return False


def _announce(songs):
    """报歌名：LLM 从歌单挑一首生成撒娇台词 → 她声音播出。
    失败 → 模板兜底（第一首）。返回 (实际歌名, 路径)。"""
    names = [n for n, _ in songs]
    title, path = songs[0]
    try:
        client = llm.get_client()
        r = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是小李，台湾甜妹。他刚叫你唱歌，你答应了，"
                 "从歌单里挑一首报歌名——撒娇台湾腔，只说这一句台词，"
                 "像\"齁～那我唱《xxx》给你听齁～\"，不要列多首、不要加别的话"},
                {"role": "user", "content": "歌单：" + "、".join(names)},
            ],
            max_tokens=80,
        )
        line = (r.choices[0].message.content or "").strip()
        m = re.search(r"《([^》]+)》", line)
        if m:
            title = m.group(1)
        # 歌名可能被 LLM 改字/加副标题 → 模糊匹配（包含即可）；匹配不到 → 第一首（歌名也回退）
        found = False
        for n, p in songs:
            if n == title or n in title or title in n:
                path = p
                found = True
                break
        if not found:
            title, path = songs[0]
        if not line:
            line = f"齁～那我唱《{title}》给你听齁～"
        _speak_wait(line)  # 报歌名说完再开唱（竞态修复：不等会被歌顶掉）
    except Exception:
        _speak_wait(f"齁～那我唱《{title}》给你听齁～")
    return title, path


def maybe_sing(text):
    """他叫唱歌 → 演出链：清嗓→报歌名→播歌→写"我们的歌"记忆。
    返回实际歌名（演了）/ None（没触发/曲库空/失败）。"""
    if not text or not TRIGGER_RE.search(text):
        return None
    if EXCLUDE_RE.search(text):
        return None  # 他自己唱，不是叫她
    songs = _song_list()
    if not songs:
        return None  # 没歌可唱：不装，persona 兜底（她自然说还没练好）
    try:
        sfx.play_blocking("clear_throat")   # 1. 清嗓（她的声音）
        try:
            title, path = _announce(songs)   # 2. 报歌名（LLM 台词+她声音）
        except Exception:
            title, path = songs[0]           # 意外失败 → 第一首兜底，演出不中断
        _play_song(path)                     # 3. 播歌（预渲染文件）
        # 4. "我们的歌"记忆（程序写，不赌 LLM；importance 8=忘不掉）
        try:
            facts = memory_mod.load_facts()
            memory_mod.merge_fact(
                facts,
                f"他喜欢听我唱歌，我们俩的歌是《{title}》",
                importance=8, category="关系", confidence=0.9, valence="happy",
            )
            memory_mod.save_facts(facts)
        except Exception:
            pass
        return title
    except Exception:
        return None
