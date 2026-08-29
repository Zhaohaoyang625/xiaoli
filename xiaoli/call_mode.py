# ============================================
# 小李的通话模式（v2 O2）—— 像打电话一样聊
# 常开监听：麦克风一直开着，你说完一句（静音1.2s）→ 识别 → 回调塞进聊天
# 半双工门控：她说话（voice 播放中）→ 暂停监听——不然她自己的声音会被识别成
#   你的话，形成"她说一句自己被识别一句"的鬼打墙
# 互斥：与"按键说话"（stt.listen_once）共用锁，不会同时抢麦克风
# 触发：终端输入"通话开 / 通话关"，或网页按钮（WebBridge /call_mode）
# 设计：Silero VAD（2026-08-23 批2 换掉纯能量阈值，faster-whisper 内置 ONNX）
#   ——prob≥0.4 且 dB≥45 双条件、3 连击开口（0.1s）、24 miss 收尾（0.8s）、
#   1.2s 判停、20s 上限截断；VAD 未就绪 → 能量阈值回退（永远能听）。
# 打断：她播放时继续听，AEC（pyaec，可选）消掉她的回声 → VAD/正常灵敏度判你
#   的细语插话，原始峰值留双保险；无 AEC → 高阈值峰值判定（防回声误打断）。
# 2026-08-22：火山识别是"推流式"，但通话场景句子短，一次性发完+PCM 更快
#   （stt.recognize_pcm）；识别期间聊天线程正常跑，识别完回调塞 input_queue，
#   主循环的打断机制会让她停下来说听。
# ============================================

import threading
import time

import numpy as np

from xiaoli import aec
from xiaoli import config
from xiaoli import sherpa_stream  # 流式识别（2026-08-24：说完即全文，替代"说完等 whisper 1~2s"）
from xiaoli import stt
from xiaoli import vad
from xiaoli import voice

PEAK_VOICE = 500        # 开口阈值（能量回退用：VAD 未就绪时；与 listen_once 一致）
MIN_VOICE_BLOCKS = 3    # 连续 0.3s 有声才算开口（防爆音/咳嗽误触发识别）
SILENCE_BLOCKS = 8      # 0.8s 静音 = 说完了（2026-08-23 用户实测"回复延迟长"：1.2s→0.8s，说玩话少等 0.4s）
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
        self._stream = None            # 当前麦克风流（stop 卡死时强制 close 打断 read）
        # 与按键说话（listen_once）互斥：默认自带一把，chat.py 会注入主循环
        # 的 listen_lock 共用同一把（否则"通话模式监听中按🎤"会抢麦克风）
        self._lock = lock or threading.Lock()
        self.on_text = None            # 识别出完整一句 → 回调(text)
        self._sess = None              # 流式识别会话（一句话一个；None=不可用走 whisper）

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.active:
            return
        # 2026-08-23 排查（2.3/5.1）：跨会话状态必须重置——VAD 单例残留
        # （speech/hits）会让重开后的静音被当说话（MIN_VOICE_BLOCKS 护栏失效，
        # 白识别一段静音）。
        vad.reset()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()  # 循环下一拍退出（听不清残余：主动关不识别半句）
        t = self._thread
        if t is not None:
            # 等旧线程收尾（2026-08-23）：否则立刻 start() 会被 `if self.active` 跳过
            # （旧线程 is_alive 还是 True）→ 按钮显示"开"实际没监听。
            # 线程正常时 <0.1s 退出；极端卡在 stream.read → 1s 超时不阻塞 start
            t.join(timeout=1.0)
            if t.is_alive():
                # 2026-08-23 全面排查发现：read(frames) 没有 timeout——麦克风设备
                # 故障/被其他程序独占时，线程会无限卡在 read 里 → join 超时 →
                # active 永远 True → 再点"开"被跳过 → "听不到"且毫无报错。
                # 自救：强制 close 流打断 read（close 会唤醒阻塞中的 read 抛异常）。
                print("  [通话模式线程未退出：强制关流]", flush=True)
                s = self._stream
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass
                t.join(timeout=1.0)
                if t.is_alive():
                    # 极端（close 也没能唤醒）→ 放弃旧线程，下次 start 直接新建
                    print("  [通话模式旧线程仍存活：强制重建]", flush=True)
                    self._thread = None

    def _loop(self):
        try:
            import sounddevice as sd
            stream = sd.InputStream(samplerate=stt.RATE, channels=1,
                                    dtype="int16", blocksize=stt.CHUNK)
            stream.start()
        except Exception as e:
            print(f"  [通话模式打不开麦克风：{e}]", flush=True)
            return
        self._stream = stream  # stop() 卡死自愈要关它
        print("  📞 通话模式已开：直接说话就行；她说话时你等她说完；"
              "想停就说「通话关」或点网页上的按钮", flush=True)
        buf = []             # 已收的有声块（字节）
        voice_blocks = 0     # 累计有声块数
        silent_blocks = 0    # 连续静音块数
        vv = None            # Silero VAD（2026-08-23 批2：换掉纯能量阈值；None = 未就绪回退能量）
        # AEC 回声消除：2026-08-23 全面排查确认生产链路从未真正运行——aec.feed
        # 收到 (1600,1) 二维输入 → pad 负宽必抛异常 → 回退返回原始音频前 320 样本，
        # 打断的"clean 峰值 > 500"变成 500 低阈值后门 → 回声/杂音误打断（用户实测
        # "容易被杂音打断"的直接根因，装了 pyaec 反而更糟）。且 far 参考信号与真实
        # 播放位置错位、VAD 窗长不匹配（AEC 320 < VAD 512，细语通道是死代码）。
        # 三处断链修完需要真声学验证——验证前强制走纯动态阈值路径（打断 = raw 峰值
        # > max(2000, 播放峰值×0.4)，回声被抬高的阈值挡住，行为正确且可预测）。
        # TODO(AEC)：修 aec.feed 形状/帧长/far 时序后重新启用（见 xiaoli/aec.py）
        #（2026-08-23 打断设计已移除，AEC 无消费方——但保留 TODO 备以后恢复打断）
        ec = None
        read_fails = 0       # 1.1：连续 read 失败计数（≥5 → 整线程退出）
        try:
            while not self._stop.is_set():
                if vv is None:
                    vv = vad.get()  # 后台预热就绪后自动切换
                    if vv is None:
                        time.sleep(1.0)  # 2026-08-23 排查：加载失败冷却 1s 再试
                        continue        #（否则每 0.1s 同步重载，慢失败卡死监听节奏）
                # ① 她正在说话：只听不掐（2026-08-23 用户实测拍板：不加打断设计）。
                #    原打断链路（起播保护→动态阈值→AEC 三路投票→stop_playing 掐话）
                #    被删：她刚开口你插话 → 整段话被掐掉，听感"她半天不说话/话说不
                #    完"（她确实说了，是打断把余下的话丢了）。现在她说话期间麦克风
                #    数据直接丢弃，她说完了（_playing clear）→ 回外层正常监听，你的
                #    话进下一轮识别回复——一来一回像打电话，延迟感知也低一些。
                #    打断链路全部代码已删（2026-08-24 清理僵尸），想恢复打断从
                #    git 历史捞（commit 0d5229b 之前的 call_mode.py）。
                if voice.is_playing():
                    if buf:  # 她开口时你正在说（buf 收了一半）→ 丢弃（她的声音优先）
                        buf, voice_blocks, silent_blocks = [], 0, 0
                    while voice.is_playing() and not self._stop.is_set():
                        try:
                            stream.read(stt.CHUNK)  # 数据丢弃：她说话时你插的话
                        except Exception:
                            time.sleep(0.1)  # 2.2：read 异常不空转（防 100% CPU 热循环）
                            break
                    continue
                # ② 与按键说话互斥：她正在被你"按住说话"录音 → 让路
                if not self._lock.acquire(blocking=False):
                    time.sleep(0.1)
                    continue
                do_finish = False
                try:
                    try:
                        data, _ = stream.read(stt.CHUNK)
                    except Exception:
                        # 1.1：瞬时读错误（设备抖动/CPU 尖峰/PortAudio 溢出）→ 跳过这轮，
                        # 不整线程死亡；连续 5 次失败才退出（外层打印 → 网页按钮变"关"）
                        read_fails += 1
                        if read_fails >= 5:
                            raise
                        time.sleep(0.1)
                        continue
                    read_fails = 0
                    talking = self._talking(vv, data)  # 状态机：开口+保持（0.4 灵敏）
                    # 判停（2026-08-23 用户实测"听不到"根因修复）：状态机可能被环境
                    # 人声（游戏/视频里的人话，prob 常在 0.4~0.6）拖住 speech=True，
                    # 判停等到十几秒甚至 20s 截断。改数"连续 0.8s 无强语音窗"——
                    # vv.last_voice 是块级判定（prob≥0.5 且 dB≥45，见 vad.py PROB_STOP），
                    # 环境人声很快漏出空隙判停；近麦真说话 prob 0.7+ 不受影响。
                    # vv 未就绪（能量回退）→ 保持旧语义：talking 重置，否则累计。
                    if talking:
                        voice_blocks += 1
                        if vv is not None and not vv.last_voice:
                            silent_blocks += 1
                        else:
                            silent_blocks = 0
                        buf.append(data.tobytes())  # 从第一块有声就开始收
                        # 流式识别（2026-08-24）：边说边喂 sherpa，说完即全文。
                        # STT_STREAM=False 或模型不可用 → _sess 保持 None，走老路 whisper
                        if config.STT_STREAM and self._sess is None:
                            self._sess = sherpa_stream.begin()
                        if self._sess is not None:
                            self._sess.feed(data.tobytes())
                    elif buf:
                        # 已开口的静音：收着（尾音/断句），超 0.8s 判停
                        silent_blocks += 1
                        buf.append(data.tobytes())
                    do_finish = (talking or buf) and silent_blocks >= SILENCE_BLOCKS
                    if not do_finish and len(buf) >= MAX_BLOCKS:  # 20s 截断
                        do_finish = True
                finally:
                    self._lock.release()
                if do_finish:
                    # 识别是同步网络调用（1~3s）——移到锁外：识别期间麦克风本来就
                    # 该停（半双工），但锁不能让给"按键说话"也拿不到（2026-08-23
                    # 全面排查发现：锁内识别 → 网页🎤/终端「说」被卡到识别完，
                    # 且监听线程阻塞在识别 → 这 1~3 秒里你说的全听不到）
                    self._finish(buf, voice_blocks)
                    buf, voice_blocks, silent_blocks = [], 0, 0
        except Exception as e:
            print(f"  [通话模式异常：{e}]", flush=True)
        finally:
            self._stream = None
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            print("  📞 通话模式已关", flush=True)

    def _talking(self, vv, data):
        """这一块数据算"有人在说话"吗？VAD 就绪用 VAD（prob+dB 双条件，细语也能判中），
        未就绪回退能量峰值（永远能听）。2026-08-23 批2 提取成方法便于单测。"""
        if vv is not None:
            return vv.feed(data)
        return int(np.abs(data).max()) > PEAK_VOICE

    def _finish(self, buf, voice_blocks):
        """一段话说完了：识别 → 回调。没真开口（咳嗽一声）→ 丢弃。"""
        sess, self._sess = self._sess, None  # 先取走清掉（没真开口也重置，防残留到下一句）
        if not buf or voice_blocks < MIN_VOICE_BLOCKS:
            return
        pcm = b"".join(buf)
        if len(pcm) > MAX_BLOCKS * stt.CHUNK * 2:  # 20s 上限（防超长）
            pcm = pcm[:MAX_BLOCKS * stt.CHUNK * 2]
        text = None
        # 流式识别（2026-08-24）：边说边识别完了，finalize 即全文——零推理等待。
        # 空/不可用 → whisper 兜底（模型可能没听清短促词）
        if sess is not None:
            text = sess.finalize()
            print(f"  🎤（流式识别：{text or '（没听清）'}）", flush=True)
        if not text:
            print(f"  🎤（听你说完 {len(pcm) / stt.CHUNK / 2:.1f} 秒，识别中…）", flush=True)
            try:
                text = stt.recognize_pcm(pcm)
            except Exception as e:
                print(f"  [识别失败：{e}]", flush=True)
                text = None
        if text and self.on_text:
            self.on_text(text)
        elif not text:
            # 2026-08-23 用户"点开没反应"：识别空 → 静默无反馈。至少让用户知道
            # 系统在听但没听清（环境吵/离麦克风远/说太快都可能是原因）
            print(f"  （识别到 {len(pcm) / stt.CHUNK / 2:.1f} 秒声音，但没听出内容——"
                  f"环境有点吵或离麦克风远的话，靠近一点再说一次）", flush=True)


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
