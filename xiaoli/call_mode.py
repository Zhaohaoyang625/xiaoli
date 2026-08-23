# ============================================
# 小李的通话模式（v2 O2）—— 像打电话一样聊
# 常开监听：麦克风一直开着，你说完一句（静音1.2s）→ 识别 → 回调塞进聊天
# 半双工门控：她说话（voice 播放中）→ 暂停监听——不然她自己的声音会被识别成
#   你的话，形成"她说一句自己被识别一句"的鬼打墙
# 互斥：与"按键说话"（stt.listen_once）共用锁，不会同时抢麦克风
# 触发：终端输入"通话开 / 通话关"，或网页按钮（WebBridge /call_mode）
# 设计：零依赖能量阈值 VAD（不装 webrtcvad）——峰值>500 开口（与 listen_once
#   同阈值）、连续0.3s有声才算"真开口"（防咳嗽/爆音误触发）、静音1.2s判停、
#   20s 上限截断（识别服务上限）。
# 2026-08-22：火山识别是"推流式"，但通话场景句子短，一次性发完+PCM 更快
#   （stt.recognize_pcm）；识别期间聊天线程正常跑，识别完回调塞 input_queue，
#   主循环的打断机制会让她停下来说听。
# ============================================

import threading
import time

import numpy as np

from xiaoli import stt
from xiaoli import voice

PEAK_VOICE = 500        # 开口阈值（与 listen_once 一致，避免两处行为不一致）
MIN_VOICE_BLOCKS = 3    # 连续 0.3s 有声才算开口（防爆音/咳嗽误触发识别）
SILENCE_BLOCKS = 12     # 1.2s 静音 = 说完了（通话节奏比按键模式略宽松）
MAX_BLOCKS = 200        # 20s 说不停 → 截断送走（识别服务 20s 上限）

_call = None  # 全局实例（chat.py / WebBridge 引用）


def get():
    """获取全局通话模式实例（懒创建）"""
    global _call
    if _call is None:
        _call = CallMode()
    return _call


class CallMode:
    """通话模式：常开监听线程。start/stop 可反复开关。"""

    def __init__(self, lock=None):
        self._stop = threading.Event()
        self._thread = None
        # 与按键说话（listen_once）互斥：默认自带一把，chat.py 会注入主循环
        # 的 listen_lock 共用同一把（否则"通话模式监听中按🎤"会抢麦克风）
        self._lock = lock or threading.Lock()
        self.on_text = None            # 识别出完整一句 → 回调(text)

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.active:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()  # 循环下一拍退出（听不清残余：主动关不识别半句）

    def _loop(self):
        try:
            import sounddevice as sd
            stream = sd.InputStream(samplerate=stt.RATE, channels=1,
                                    dtype="int16", blocksize=stt.CHUNK)
            stream.start()
        except Exception as e:
            print(f"  [通话模式打不开麦克风：{e}]")
            return
        print("  📞 通话模式已开：直接说话就行；她说话时你等她说完；"
              "想停就说「通话关」或点网页上的按钮")
        buf = []             # 已收的有声块（字节）
        voice_blocks = 0     # 累计有声块数
        silent_blocks = 0    # 连续静音块数
        try:
            while not self._stop.is_set():
                # ① 半双工门控：她在播放语音 → 暂停监听（核心：防鬼打墙）
                if voice.is_playing():
                    buf, voice_blocks, silent_blocks = [], 0, 0
                    time.sleep(0.1)
                    continue
                # ② 与按键说话互斥：她正在被你"按住说话"录音 → 让路
                if not self._lock.acquire(blocking=False):
                    time.sleep(0.1)
                    continue
                try:
                    data, _ = stream.read(stt.CHUNK)
                    peak = int(np.abs(data).max())
                    if peak > PEAK_VOICE:
                        voice_blocks += 1
                        silent_blocks = 0
                        buf.append(data.tobytes())  # 从第一块有声就开始收
                    elif buf:
                        # 已开口的静音：收着（尾音/断句），超 1.2s 判停
                        silent_blocks += 1
                        buf.append(data.tobytes())
                        if silent_blocks >= SILENCE_BLOCKS:
                            self._finish(buf, voice_blocks)
                            buf, voice_blocks, silent_blocks = [], 0, 0
                    # 未开口的静音：不收
                    if len(buf) >= MAX_BLOCKS:  # 20s 截断
                        self._finish(buf, voice_blocks)
                        buf, voice_blocks, silent_blocks = [], 0, 0
                finally:
                    self._lock.release()
        except Exception as e:
            print(f"  [通话模式异常：{e}]")
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            print("  📞 通话模式已关")

    def _finish(self, buf, voice_blocks):
        """一段话说完了：识别 → 回调。没真开口（咳嗽一声）→ 丢弃"""
        if not buf or voice_blocks < MIN_VOICE_BLOCKS:
            return
        pcm = b"".join(buf)
        if len(pcm) > MAX_BLOCKS * stt.CHUNK * 2:  # 20s 上限（防超长）
            pcm = pcm[:MAX_BLOCKS * stt.CHUNK * 2]
        print(f"  🎤（听你说完 {len(pcm) / stt.CHUNK / 2:.1f} 秒，识别中…）")
        try:
            text = stt.recognize_pcm(pcm)
        except Exception as e:
            print(f"  [识别失败：{e}]")
            text = None
        if text and self.on_text:
            self.on_text(text)


if __name__ == "__main__":
    # 自测：开通话模式，说一句，打印识别结果（需要能联网火山）
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    m = get()
    m.on_text = lambda t: print("识别到：", t)
    m.start()
    try:
        time.sleep(60)  # 说一句话试试
    finally:
        m.stop()
