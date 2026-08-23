# ============================================
# 火山引擎 TTS（豆包语音 v3 新版接口）
# 学自：火山引擎官方示例 agentkit-samples/byted-text-to-speech（gh-proxy 抓取验证）
# 路线决策（2026-08-20）：edge-tts 台湾声线质量差（逐字慢读）、晓晓没台湾腔 →
# 换火山引擎豆包语音。注意：新版控制台用 API Key（X-Api-Key 头），不是旧版 AppID！
# 优雅降级：未配置 Key 或调用失败 → 返回 None，由 voice.py 退回 edge-tts
# ============================================

import base64
import json
import urllib.request
import uuid

from xiaoli import config

# V3 新版接口（HTTP 单向流式/SSE）
TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
TTS_RESOURCE_ID = "seed-tts-2.0"  # 默认资源（音色 *_uranus_bigtts）


def _resource_id(voice):
    """音色后缀决定资源：_moon_bigtts → 语音合成1.0（seed-tts-1.0）；其余 → 2.0
    实测（2026-08-20）：湾湾小何等 moon 音色用 2.0 资源会报 requested resource not granted"""
    if voice.endswith("_moon_bigtts"):
        return "seed-tts-1.0"
    return TTS_RESOURCE_ID


# ============================================
# 情绪化 TTS（v2 E4，2026-08-22 修正：指令式废弃 → 参数模拟）
# ⚠️ 实测教训（用户反馈"每句话前都会念整体情绪"）：
#   火山豆包语音 v3 不解析 "<整体情绪：生气>" 这类文本指令，
#   会把指令当正文一字一句念出来！
#   证据：同一句话 无指令 4.22s vs 带指令 5.96s / 8.42s——
#   生气本该更快，反而变长 = 在念指令文字（2026-08-21 试听误判为"指令变慢"）。
# 修正方案：情绪 → speech_rate/loudness/pitch 参数（零文本注入，绝不可能念出指令）。
#   情绪色彩主要由台词本身承载（persona 已让 LLM 写出带情绪的台词），
#   语音参数只做轻微辅助，避免戏剧化。
# ============================================
EMOTION_PARAMS = {
    "angry":        {"speech_rate": 25},                    # 生气：快而冲（真人生气语速快）
    "jealous":      {"speech_rate": -5, "loudness": -10},   # 吃醋：酸溜溜，低声委屈
    "sad":          {"speech_rate": -20, "loudness": -15},  # 伤心：慢而轻
    "afraid":       {"speech_rate": -10, "loudness": -15},  # 害怕：声音放轻
    "excited":      {"speech_rate": 20},                    # 兴奋：语速快
    "happy":        {"speech_rate": 5},                     # 开心：轻快
    # 2026-08-22 实测：pitch（音调偏移）让声音变尖，用户听感"完全不对"（真人撒娇不会突然变声）
    # → 情绪只用语速/音量表达，音色永远不变。要"俏皮感"靠台词本身（persona 已覆盖）
    "playful":      {"speech_rate": 10},                    # 俏皮：语速略快
    "flustered":    {"speech_rate": 5, "loudness": -5},     # 害羞：小声
    "content":      {"speech_rate": -5},                    # 温柔：稍慢
    "affectionate": {"speech_rate": -10, "loudness": 5},    # 爱意：低缓，音量稍足
    "anxious":      {"speech_rate": 15},                    # 着急：略快
    "frustrated":   {"speech_rate": 10},                    # 无奈：稍快
    "melancholy":   {"speech_rate": -25, "loudness": -20},  # 低落：又慢又轻
}


def emotion_params(emotion):
    """情绪 → 语音参数 dict（语速/音量/音调）；未知情绪 → None（用默认音色）"""
    return EMOTION_PARAMS.get(emotion)


def synthesize(text, voice=None, emotion=None):
    """豆包语音 v3 合成，返回 24kHz 单声道 int16 PCM 音频字节。失败/未配置 → None
    （2026-08-21 实测教训 M.4.2：format 用 pcm 而非 mp3——播放器直接播 PCM，
    零解码零格式转换；edge-tts 降级路径的 mp3 用 miniaudio 解码）
    emotion（v2 E4 修正）：情绪 → 语音参数（speech_rate/loudness/pitch），
    绝不往文本注入指令（火山实测会把指令当正文念出来，2026-08-22）"""
    if not config.VOLC_API_KEY:
        return None
    params = emotion_params(emotion) or {}
    payload = {
        "user": {"uid": "xiaoli_tts_user"},
        "req_params": {
            "text": text,   # 纯台词，无任何指令注入
            "speaker": voice or config.VOLC_VOICE,
            "sample_rate": 24000,
            "audio_params": {
                "format": "pcm",    # PCM 直出（24kHz int16 单声道，播放零解码）
                "speech_rate": params.get("speech_rate", 0),   # 语速 [-50,100]，0=正常
                "loudness_rate": params.get("loudness", 0),    # 音量 [-50,100]
                "bit_rate": 64000,
            },
            "additions": json.dumps({
                "post_process": {"pitch": params.get("pitch", 0)},  # 音调 [-12,12]
                "disable_markdown_filter": True,
                "enable_latex_tn": False,
                "latex_parser": "v2",
            }),
        },
    }
    req = urllib.request.Request(
        TTS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": config.VOLC_API_KEY,          # 新版鉴权：你的 API Key
            "X-Api-Resource-Id": _resource_id(payload["req_params"]["speaker"]),
            "X-Api-Request-Id": str(uuid.uuid4()),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        # SSE 流：每行 "data: {json}"，json 的 data 字段是 base64 音频分片，拼起来
        chunks = []
        for line in body.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                d = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            code = d.get("code", 0)
            if code not in (0, 20000000):
                print(f"  [火山TTS错误 code={code}: {d.get('message', '')}]")
                return None
            if d.get("data"):
                chunks.append(base64.b64decode(d["data"]))
        if not chunks:
            print("  [火山TTS：没有音频数据]")
            return None
        return b"".join(chunks)
    except Exception as e:
        print(f"  [火山TTS调用失败：{e}]")
        return None
