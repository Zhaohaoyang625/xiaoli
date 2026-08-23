# ============================================
# 本地合成 tts_local 单测（2026-08-22）
# 原则：不碰真模型/真 GPU——mock qwen_tts，只测"降级链正确性"：
# 模型未就绪/失败 → None（voice.py 自动降级火山，永远有声音）
# ============================================

from unittest import mock

import numpy as np

from xiaoli import config, tts_local


class TestTtsLocal:
    def test_disabled_returns_none(self):
        """TTS_LOCAL=False → 直接 None（强制火山）"""
        with mock.patch.object(config, "TTS_LOCAL", False):
            assert tts_local.synthesize("你好") is None

    def test_model_dir_missing_returns_none(self):
        """模型目录不存在 → None（提示下载，不崩）"""
        with mock.patch("xiaoli.tts_local._MODEL_DIR", "C:/不存在/模型"), \
             mock.patch.object(config, "TTS_LOCAL", True):
            assert tts_local.synthesize("你好") is None

    def test_load_failure_returns_none(self):
        """qwen_tts 导入/加载失败 → None（降级火山）"""
        with mock.patch("xiaoli.tts_local._load", return_value=False), \
             mock.patch.object(config, "TTS_LOCAL", True):
            assert tts_local.synthesize("你好") is None

    def test_synthesize_ok(self):
        """正常合成 → (sr, int16 pcm bytes)（后处理有单独测试，这里隔离测核心合成）"""
        fake = mock.Mock()
        fake.wavs = [np.zeros(4800, dtype=np.float32)]
        with mock.patch("xiaoli.tts_local._load", return_value=True), \
             mock.patch.object(config, "TTS_LOCAL", True), \
             mock.patch("xiaoli.tts_local._model", fake), \
             mock.patch("xiaoli.tts_local._clone_prompt", [object()]), \
             mock.patch("xiaoli.tts_local._post_process",
                        side_effect=lambda w, sr=24000: w):
            fake.generate_voice_clone.return_value = ([np.zeros(4800, dtype=np.float32)], 24000)
            r = tts_local.synthesize("宝贝")
            assert r is not None
            sr, pcm = r
            assert sr == 24000
            assert len(pcm) == 4800 * 2  # int16
            fake.generate_voice_clone.assert_called_once()
            # 语言必须是中文（台湾腔文本）
            assert fake.generate_voice_clone.call_args.kwargs["language"] == "Chinese"

    def test_post_process_stretches_and_normalizes(self):
        """后处理：变速（显式 1.15 → 输出变长）+ 响度归一化到目标 RMS"""
        wav = np.ones(4800, dtype=np.float32) * 0.05  # 1 秒零杂讯，RMS 5%
        with mock.patch.object(config, "TTS_LOCAL_SPEED", 1.15):
            out = tts_local._post_process(wav)
        assert len(out) > len(wav)  # 1.15x 慢放 → 变长
        rms = float(np.sqrt((out ** 2).mean()))
        assert abs(rms - config.TTS_LOCAL_TARGET_RMS) < 1e-3  # 归一化到目标
        assert np.abs(out).max() <= 1.0  # 不溢出

    def test_post_process_disabled_speed_keeps_length(self):
        """TTS_LOCAL_SPEED=1.0（不变速）→ 长度不变，只做响度归一化"""
        wav = np.ones(4800, dtype=np.float32) * 0.05
        with mock.patch.object(config, "TTS_LOCAL_SPEED", 1.0):
            out = tts_local._post_process(wav)
        assert len(out) == len(wav)

    def test_post_process_failure_returns_original(self):
        """后处理任何一步失败 → 原样返回（永远有声音，不拖累合成）"""
        wav = np.ones(100, dtype=np.float32) * 0.05
        with mock.patch("xiaoli.tts_local.config.TTS_LOCAL_SPEED", 1.5), \
             mock.patch("librosa.effects.time_stretch",
                        side_effect=RuntimeError("librosa 挂了")):
            out = tts_local._post_process(wav)
        assert len(out) == len(wav)

    def test_synthesize_failure_returns_none(self):
        """模型推理抛异常 → None（降级火山，语音不断）"""
        with mock.patch("xiaoli.tts_local._load", return_value=True), \
             mock.patch.object(config, "TTS_LOCAL", True), \
             mock.patch("xiaoli.tts_local._model", mock.Mock()), \
             mock.patch("xiaoli.tts_local._clone_prompt", [object()]):
            tts_local._model.generate_voice_clone.side_effect = RuntimeError("显存不足")
            assert tts_local.synthesize("宝贝") is None

    def test_empty_result_guard(self):
        """空返回（没合成出东西）→ None"""
        with mock.patch("xiaoli.tts_local._load", return_value=True), \
             mock.patch.object(config, "TTS_LOCAL", True), \
             mock.patch("xiaoli.tts_local._model", mock.Mock()), \
             mock.patch("xiaoli.tts_local._clone_prompt", [object()]):
            tts_local._model.generate_voice_clone.return_value = ([], 24000)
            assert tts_local.synthesize("宝贝") is None
