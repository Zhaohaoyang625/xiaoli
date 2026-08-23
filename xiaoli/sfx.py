# ============================================
# 小李的拟声层（2026-08-22，用户："她会不会清一下嗓子，咳嗽一下…和现实的人很像"）
# 四种真人感小音效：清嗓子/叹气/轻笑/咳嗽。
# 关键设计：音效素材不用下载——用她自己的声音合成！
#   火山 TTS 官方优化过"语气词/副语言"（咳、咳/嗯…/嘻嘻能自然念出）→
#   合成一次存 wav，之后直接读文件。音色和她说话完全一致——
#   "咳嗽也是她的声音"，这正是真人感的关键。
# 生成：懒加载（第一次用到才合成，火山失败自动降级 edge-tts）
# 播放：sounddevice 同一条通道 → 与语音播放天然串行，绝不重叠
# 触发决策在 chat.py（这边只管素材+播放，不碰对话逻辑）
# ============================================

import os
import threading
import wave

import numpy as np
import sounddevice as sd

from xiaoli import paths, tts_api

# 音效定义：名字 → 她"念"出来的文字（合成就是她的声音）
_SFX_DEF = {
    "clear_throat": "咳、咳",   # 清嗓子（话前习惯性开场）
    "sigh":         "呼……",     # 叹气（话后低落）
    "chuckle":      "嘻嘻",     # 轻笑（话后开心）
    "cough":        "咳！咳！",  # 咳嗽（聊到感冒/着凉）
}
_SFX_DIR = os.path.join(paths.DATA_DIR, "sfx")

# 缓存：名字 → (sample_rate, np.float32 数组)；None = 生成失败（本次不再试）
_cache = {}
_lock = threading.Lock()


def _gen_all():
    """预生成全部音效（启动时调用一次，避免说话时首次触发卡顿）"""
    os.makedirs(_SFX_DIR, exist_ok=True)
    for name, text in _SFX_DEF.items():
        _load(name)


def _load(name):
    """读音效（wav 缓存）→ (sr, np.float32)；没有 → 现场合成存盘"""
    with _lock:
        if name in _cache:
            return _cache[name]
        text = _SFX_DEF.get(name, name)  # 名字 → 她念的文字
        pcm = None
        wav_path = os.path.join(_SFX_DIR, name + ".wav")
        if os.path.exists(wav_path):
            try:
                with wave.open(wav_path, "rb") as w:
                    sr = w.getframerate()
                    raw = w.readframes(w.getnframes())
                if raw:
                    pcm = raw
            except Exception:
                pass
        if pcm is None:
            # 现场合成（火山 → 自动降级 edge）：第一次启动那几秒多花一点时间
            for attempt in range(2):
                pcm = tts_api.synthesize(text)
                if pcm:
                    break
            if not pcm:
                _cache[name] = None  # 全失败 → 本次静默（下次再试）
                return None
            sr = 24000
            try:
                with wave.open(wav_path, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(sr)
                    w.writeframes(pcm)
            except OSError:
                pass
        data = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        _cache[name] = (sr, data)
        return _cache[name]


def play_blocking(name):
    """同步播完（话前清嗓用：清完嗓再开口说话，顺序自然）"""
    got = _load(name)
    if not got:
        return
    sr, data = got
    try:
        sd.play(data, sr)
        sd.wait()
    except Exception:
        pass


def play(name):
    """后台播放（不阻塞：话后叹气/轻笑）"""
    got = _load(name)
    if not got:
        return
    sr, data = got

    def _play():
        try:
            sd.play(data, sr)
            sd.wait()
        except Exception:
            pass

    threading.Thread(target=_play, daemon=True).start()


if __name__ == "__main__":
    # 自测：预生成 4 个音效并逐个试听
    _gen_all()
    for name in _SFX_DEF:
        print(f"试听 {name}（{_SFX_DEF[name]}）…")
        play_blocking(name)
    print("（若没听到，检查声卡/音量；音效已存在 data/sfx/ 下可检查 wav）")
