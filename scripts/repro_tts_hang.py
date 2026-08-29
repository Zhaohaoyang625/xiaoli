# ============================================
# 复现"聊天进程内 TTS 本地克隆加载卡死"（2026-08-23 排查）
# 现象：聊天进程（python -m xiaoli.chat --voice）里 whisper 就绪后，
#       TTS preload 线程 >125s 无"就绪/失败"打印；但新进程手动主线程
#       tts_local._load() 8.4 秒成功。
# 本脚本逐步模拟聊天进程加载顺序，每步打印耗时，精确定位卡点。
# 注意：占真显存（whisper 加载 ~1.5G + TTS 峰值 4.5G+），跑完释放。
# ============================================

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

t0 = time.time()


def log(msg):
    print(f"[{time.time() - t0:8.1f}s] {msg}", flush=True)


def mem_gb():
    try:
        import torch
        a = torch.cuda.memory_allocated() / 1e9
        r = torch.cuda.memory_reserved() / 1e9
        return f"alloc {a:.2f}G / reserved {r:.2f}G"
    except Exception as e:
        return f"(torch mem 不可读: {e})"


log("== 启动（模拟 chat.py） ==")
from xiaoli import config, tts_local, whisper_stt

log(f"config.STT_LOCAL={config.STT_LOCAL} TTS_LOCAL={config.TTS_LOCAL}")

log(">> whisper preload()（后台线程）")
whisper_stt.preload()
ok = whisper_stt.wait_ready(timeout=120)
log(f"<< whisper wait_ready → {ok}")

log(">> tts_local.preload()（后台线程，串行修复后的顺序）")
tts_local.preload()
threads = [t for t in threading.enumerate() if t.name and "Thread" in t.name]
t_thread = [t for t in threads if t != threading.main_thread()][-1]
log(f"   TTS 线程: {t_thread}")

# 轮询：以"线程存活 + _loading"为准（避免 preload 后立刻 poll 的 race）
deadline = time.time() + 180
start_poll = time.time()
while time.time() < deadline:
    if not t_thread.is_alive():
        if tts_local._model is not None:
            log(f"<< TTS 线程完成：_model 就绪（poll 耗时 {time.time()-start_poll:.1f}s）")
        else:
            log(f"<< TTS 线程完成：_model 为空（失败，原因见上方线程打印）")
        break
    if (time.time() - start_poll) % 20 < 0.2:
        log(f"… 等 TTS 中 {mem_gb()}")
    time.sleep(1)
else:
    log(f"!! 超时：TTS 线程 180s 仍活着（卡死），{mem_gb()}")

log("== 对照：主线程 _load() ==")
tts_local._model = None
r = tts_local._load()
log(f"主线程 _load() → {r}")

log("== 结束 ==")
