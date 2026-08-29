# ============================================
# 小李的语音输入层（STT）—— 你说，她听
# 2026-08-22 升级：本地 faster-whisper 优先（0 元/月，火山流式识别 20 小时免费
# 额度用完后 3.5~4.5 元/小时，是每月开销大头）→ 本地失败自动降级火山。
# 流程：录音（安静1秒自动停）→ 本地识别 → 本地不可用再走火山流式
# 依赖：sounddevice（录音）、faster-whisper（本地识别）、websocket-client（火山降级）
# ============================================

import gzip
import json
import os
import struct
import time
import uuid

import websocket

from xiaoli import config
from xiaoli import proactive  # 录音标志：你在说话 → 她绝不抢话（打断机制）
from xiaoli import whisper_stt  # 本地识别（2026-08-22 新增）

# 调试开关：STT_DEBUG=1 时打印详细过程（定位"听不到"问题时用，平时不影响）
DEBUG = os.environ.get("STT_DEBUG") == "1"


def _dbg(*args):
    if DEBUG:
        print("[stt调试]", *args)

# 识别配置
WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
RESOURCE_ID = "volc.seedasr.sauc.duration"  # 流式识别2.0 小时版
RATE = 16000        # 采样率（本地 whisper 和火山都用 16k）
CHUNK = 1600        # 每块 0.1 秒
MAX_SECONDS = 20    # 最长说话时间（兜底）
SILENCE_SECONDS = 1.0  # 安静这么久就认为说完了（2026-08-20 从1.2s调到1.0s，等太久）


def _frame(msg_type, flags, payload, serialization=0):
    """SAUC 帧（火山降级用，2026-08-20 实测验证）：
    4字节头 + 4字节长度 + 数据；数据一律 Gzip 压缩（服务端要求）
    不带客户端序列号；音频结束用 flags=0x2(LastPacket) 标记"""
    body = gzip.compress(payload)
    h = bytearray(4)
    h[0] = 0x11                      # 协议版本1，头大小4字节
    h[1] = (msg_type << 4) | flags   # 消息类型 + 标志
    h[2] = (serialization << 4) | 1  # JSON/raw 序列化 + Gzip 压缩
    return bytes(h) + struct.pack(">I", len(body)) + body


def _record():
    """录音：16k int16 单声道，安静 1 秒自动停，最长 20 秒。
    返回完整 PCM bytes（含句末静音，VAD 会滤掉）；失败/没声音 → None"""
    import numpy as np
    import sounddevice as sd
    stream = None
    try:
        stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                                blocksize=CHUNK)  # 每块 0.1 秒
        stream.start()
        _dbg("麦克风已打开")
        print("  🎤 说（安静1秒自动停，最长20秒）…", flush=True)
        voiced = False   # 是否已开口（未开口的静音不收录，防误触发）
        silent_blocks = 0
        chunks = []
        start_time = time.time()
        while time.time() - start_time < MAX_SECONDS:
            data, _ = stream.read(CHUNK)
            peak = int(np.abs(data).max())
            if peak > 500:
                voiced = True
                silent_blocks = 0
                chunks.append(data.tobytes())
            elif voiced:
                silent_blocks += 1
                chunks.append(data.tobytes())
                if silent_blocks >= int(SILENCE_SECONDS / 0.1):
                    break
        if not voiced:
            print("  （没听到声音，取消了）", flush=True)
            _dbg("20秒内没检测到声音（peak 都 < 500）")
            return None
        if time.time() - start_time >= MAX_SECONDS:
            _dbg("20秒到点强制结束（说话超过20秒？）")
        return b"".join(chunks)
    except Exception as e:
        print(f"  [麦克风打不开：{e}]", flush=True)
        _dbg(f"录音异常：{type(e).__name__}: {e}")
        return None
    finally:
        if stream is not None:
            try:
                stream.stop(); stream.close()
            except Exception:
                pass


def _recognize_volcano(pcm):
    """火山流式识别（降级用）：一次性推流已录 PCM → 文本。失败返回 None 不抛异常"""
    if not config.VOLC_API_KEY or not pcm:
        return None
    req = json.dumps({
        "user": {"uid": "xiaoli"},
        "audio": {"format": "pcm", "rate": RATE, "bits": 16, "channel": 1},
        "request": {"model_name": "bigmodel", "enable_itn": True,
                    "enable_punc": True},
    }).encode("utf-8")
    try:
        ws = websocket.create_connection(
            WS_URL,
            header={
                "X-Api-Key": config.VOLC_API_KEY,     # 新版 API Key 鉴权
                "X-Api-Resource-Id": RESOURCE_ID,
                "X-Api-Connect-Id": str(uuid.uuid4()),
            },
            timeout=30,
        )
    except Exception as e:
        print(f"  [识别服务连不上：{e}]", flush=True)
        _dbg(f"WebSocket 连接失败：{e}")
        return None
    last_text = ""
    try:
        ws.settimeout(15)  # 没结果 15 秒就放弃（兜底）
        ws.send_binary(_frame(0x1, 0x0, req, serialization=1))
        chunk_bytes = CHUNK * 2  # 0.1s × 16bit
        for i in range(0, len(pcm), chunk_bytes):
            chunk = pcm[i:i + chunk_bytes]
            ws.send_binary(_frame(0x2, 0x2 if i + chunk_bytes >= len(pcm) else 0x0, chunk))
            time.sleep(0.05)  # 发包间隔规范（100~200ms）
        # 收结果：响应是二进制帧（FullServerResponse 0x9）
        while True:
            resp = ws.recv()
            if not isinstance(resp, bytes) or len(resp) < 8:
                continue
            hdr = resp[:4]
            msg_type = hdr[1] >> 4
            flags = hdr[1] & 0x0F
            compression = hdr[2] & 0x0F
            pos = 4 + (4 if flags else 0)  # 服务端响应带 sequence
            size = struct.unpack(">I", resp[pos:pos + 4])[0]
            body = resp[pos + 4:pos + 4 + size]
            _dbg(f"收到帧：msg_type={msg_type} flags={flags} 长度={len(resp)}")
            if msg_type == 0xF:  # 错误帧：错误码(4B) + 错误信息
                err_code = struct.unpack(">I", resp[4:8])[0] if len(resp) > 8 else 0
                print(f"  [识别服务错误码：{err_code}]")
                break
            if msg_type != 0x9:
                continue
            if compression == 1:
                body = gzip.decompress(body)
            d = json.loads(body.decode("utf-8", errors="replace"))
            # 结构（实测）：result 是字典，增量文本在 result.text，
            # 逐帧变长，最后 flags=3 帧是完整结果（definite=true）
            r = d.get("result")
            if isinstance(r, dict) and r.get("text"):
                last_text = r["text"]  # 增量覆盖：最后一条=完整结果
            if flags == 0x3 or r.get("definite"):  # 最后一包结果 / 语句稳定
                _dbg(f"收到结束帧，最后文本={last_text!r}")
                break
    except (websocket.WebSocketTimeoutException, TimeoutError):
        _dbg("收结果超时（服务端 15 秒没回结果——限流？服务异常？）")  # 超时兜底：有部分结果就用部分结果
    except Exception as e:
        print(f"  [识别过程出错：{e}]", flush=True)
        _dbg(f"异常：{type(e).__name__}: {e}")
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return last_text.strip() or None  # 识别文本（没识别到 → None）


def listen_once():
    """一步到位：录音 + 识别（本地 whisper 优先 → 火山降级）+ 返回文字。
    任何失败返回 None。2026-08-22 重构：录音与识别分离——
    本地优先（0 元），本地没就绪/失败才走火山（永远有耳朵）"""
    if not config.STT_LOCAL and not config.VOLC_API_KEY:
        print("  [没配火山 API Key 也没本地模型，语音输入不可用]", flush=True)
        return None
    proactive.set_recording(True)  # 你开口了 → 她安静听，不抢话
    try:
        pcm = _record()
        if pcm is None:
            return None
        # ① 本地 faster-whisper（0 元/月，2026-08-22）
        text = whisper_stt.transcribe(pcm)
        if text:
            _dbg(f"本地识别：{text}")
            return text
        _dbg("本地不可用 → 降级火山流式识别")
        return _recognize_volcano(pcm)
    finally:
        proactive.set_recording(False)  # 你说完了 → 她恢复自主


def recognize_pcm(pcm):
    """识别一段完整 PCM（通话模式用，2026-08-22 v2 O2）：
    本地 whisper 优先 → 火山降级。失败不抛异常（通话循环每轮都调用，不能让它崩）。"""
    if not pcm:
        return None
    text = whisper_stt.transcribe(pcm)
    if text:
        return text
    return _recognize_volcano(pcm)


if __name__ == "__main__":
    # 自测：说一句话，看识别结果
    text = listen_once()
    if text:
        print("识别到：", text)
    else:
        print("（没识别到内容）")
