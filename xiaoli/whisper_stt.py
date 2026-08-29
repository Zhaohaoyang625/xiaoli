# ============================================
# 小李的本地语音识别（2026-08-22，用户："免费额度用完就要充值，感觉挺贵的"）
# 火山流式识别 2.0 免费 20 小时后约 3.5~4.5 元/小时 → 每月 100+ 元，是开销大头。
# 换 faster-whisper（CTranslate2，本地 GPU 推理）：large-v3 int8 量化 ≈4GB 显存，
# RTX 5060 8GB 余量充足，中文识别质量与火山相当（whisper 中文 ~90%+）。
# 设计原则：永远有耳朵——
#   懒加载 + 启动后台预热（聊天头几秒模型就好，第一次说话不等）；
#   加载失败/模型未就绪/识别失败 → 返回 None，stt.py 自动降级火山。
# 模型：models/faster-whisper-large-v3（ModelScope 下载，keepitsimple 镜像）
# ============================================

import os
import threading
import time

import numpy as np

from xiaoli import config, paths

_MODEL_PATH = os.path.join(paths.MODELS_DIR, "faster-whisper-large-v3")
_model = None
_model_lock = threading.Lock()
_loading = False


def _add_nvidia_dlls():
    """把 pip 装的 nvidia-cublas/cudnn 运行库目录加进 dll 搜索路径。
    （实测教训 2026-08-22：ctranslate2 GPU 版报 "cublas64_12.dll is not found"——
    需要 nvidia-cublas-cu12 + nvidia-cudnn-cu12 两个 pip 包提供运行库；
    add_dll_directory 对 ctranslate2 的加载方式无效 → 注入 PATH，实测通过）"""
    dirs = []
    try:
        import site
        for base in site.getsitepackages():
            for d in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
                p = os.path.join(base, d)
                if os.path.isdir(p):
                    dirs.append(p)
    except Exception:
        pass
    for p in dirs:
        try:
            os.add_dll_directory(p)
        except Exception:
            pass
        if p not in os.environ.get("PATH", ""):
            os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")


def _load():
    """加载模型（int8 量化 GPU）。并发安全：正在加载时别的线程直接返回 False（降级）。"""
    global _model, _loading
    with _model_lock:
        if _model is not None:
            return True
        if _loading:
            return False
        if not os.path.isdir(_MODEL_PATH):
            print(f"  [本地识别：模型不存在 {_MODEL_PATH}，请先下载]", flush=True)
            return False
        _loading = True
    _add_nvidia_dlls()
    try:
        from faster_whisper import WhisperModel
        _model = WhisperModel(_MODEL_PATH, device="cuda", compute_type="int8")
        print("  [本地识别就绪：faster-whisper large-v3 (GPU)]", flush=True)
        return True
    except Exception as e:
        _model = None
        print(f"  [本地识别模型加载失败：{e}]（自动用火山识别）", flush=True)
        return False
    finally:
        _loading = False


_preload_thread = None


def preload():
    """启动时后台预热（不阻塞聊天）"""
    global _preload_thread
    if not config.STT_LOCAL:
        return
    _preload_thread = threading.Thread(target=_load, daemon=True)
    _preload_thread.start()


def wait_ready(timeout=120):
    """等后台加载线程完成（2026-08-23 修复串行化用）：
    成功 → True；加载失败/超时 → False（失败原因已打印，调用方降级不阻塞）。
    用途：TTS 预热必须等 whisper 就绪——两个大模型**并行**加载时显存峰值叠加
    （whisper int8 ~1.5G + Qwen bf16 加载峰值 4.5G+ → 逼近 8G 上限）实测 TTS 加载
    卡死 2.5 分钟无结果（克隆音色永远不出现）。串行后 6~9 秒正常就绪。"""
    if _preload_thread is None:
        return True  # 没启动预热（STT_LOCAL=False）→ 无显存竞争，直接放行
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _model is not None:
            return True
        if _preload_thread is not None and not _preload_thread.is_alive():
            return False  # 线程已结束且没成功 = 加载失败（原因已打印）
        time.sleep(0.5)
    return False


def transcribe(pcm, sample_rate=16000):
    """识别一段完整 PCM（int16 bytes）→ 文本；失败/未就绪 → None（调用方降级火山）。
    中文识别：language="zh" 锁中文（不会把中文听成英文）；
    beam_size=5（2026-08-23 用户实测"识别有点差距"：beam=1 贪心快但同音字/多音字
    易错；GPU 上 beam=5 延迟几乎无感，large-v3 换更准的解码搜索）；
    vad_filter 内置 Silero VAD 滤静音（环境噪声自动忽略）"""
    if not config.STT_LOCAL or not _load():
        return None
    try:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, info = _model.transcribe(
            audio, language="zh", beam_size=5,
            vad_filter=True,  # 静音自动滤掉，尾音/口误不会带进结果
        )
        text = "".join(s.text for s in segments).strip()
        return text or None
    except Exception as e:
        print(f"  [本地识别失败：{e}]（自动用火山识别）")
        return None
