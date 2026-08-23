# ============================================
# 小李的配置文件
# 密钥不在这里填了（2026-08-22 S1 密钥轮换 + keyring 化）：
#   明文密钥 = 安全泄漏面（路径穿越漏洞实测证明本地程序能读出它）。
#   密钥存在 Windows 凭据管理器（系统级加密）。
#   首次使用前先运行：python scripts/setup_keys.py --set deepseek
# ============================================

import os
import keyring


def _get_key(account):
    """从 Windows 凭据管理器读密钥；没有则看环境变量 XIAOLI_*；再没有返回 None。
    绝不回退明文（明文就是不存在的）。"""
    try:
        k = keyring.get_password("xiaoli", account)
        if k:
            return k
    except Exception:
        pass  # 凭据管理器不可用时走环境变量
    return os.environ.get("XIAOLI_" + account.upper())


# 你的 API Key（在 DeepSeek 平台的"API Keys"页面创建）
# 换 key：python scripts/setup_keys.py --set deepseek
DEEPSEEK_API_KEY = _get_key("deepseek")

# 你的服务地址：
# - DeepSeek 官网：https://api.deepseek.com
# - 如果是第三方平台，填平台给你的地址（通常以 /v1 结尾）
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 模型名称：
# - DeepSeek 官网当前支持：deepseek-v4-pro（更强更贵）和 deepseek-v4-flash（更快更便宜）
# - 我们选 flash：速度快、成本低，聊天体验合适；deepseek-chat 是旧别名，也能用
DEEPSEEK_MODEL = "deepseek-v4-flash"

# 视觉模型（2026-08-21 上线）：看照片用（单张最多 384 tokens ≈ 0.0012 元）。
# 接口同 OpenAI 兼容 chat.completions，图片 base64 内联（vision.py）
DEEPSEEK_VISION_MODEL = "deepseek-v4-flash-vision-exp"

# ============================================
# 火山引擎豆包语音（TTS）—— 小李的正式声音
# 注册：https://console.volcengine.com/auth/signup → 开通"语音技术"
# 新版控制台用 API Key 接入（不用 AppID）：控制台「API Key」页面创建/查看
# 音色 ID 在控制台「体验中心」试听、复制
# 填好 Key，语音自动走火山（未填则退回 edge-tts 晓晓）
# 换 key：python scripts/setup_keys.py --set volc
# ============================================
VOLC_API_KEY = _get_key("volc")

# 正式音色（2026-08-20 用户实测选定）：小何2.0 = zh_female_xiaohe_uranus_bigtts
# 试听过程：vivi2.0 最像真人但无台湾味；柔美女友/甜美悦悦偏软但不够自然；
# 湾湾小何(moon,1.0) 单独授权失败；体验中心里"小何2.0"用户认可 → 选定
# 换音色：去体验中心试听 → 复制 ID 填这里（uranus 结尾走2.0已开通，moon 结尾走1.0）
VOLC_VOICE = "zh_female_xiaohe_uranus_bigtts"

# ============================================
# 语音识别路线（2026-08-22 新增）
# 火山流式识别 2.0 免费 20 小时后 3.5~4.5 元/小时，是每月的开销大头（100+ 元）。
# → 换成本地 faster-whisper（RTX 5060 8GB 跑 large-v3 int8 ≈4GB 显存），0 元/月。
# STT_LOCAL=True：本地优先，本地失败/模型没就绪 → 自动降级火山（永远有耳朵）。
# 想强制用火山 → 改成 False。
# ============================================
STT_LOCAL = True

# ============================================
# 语音合成路线（2026-08-22 新增）
# 本地 Qwen3-TTS 声音克隆（1.7B，模型 models/Qwen3-TTS-12Hz-1.7B-Base，ModelScope 下载）：
#   参考音频 = 火山"甜美台妹"合成的小李台词 → 克隆出她自己的声音
#   多段参考（data/ref_audio_1~5.wav，覆盖疑问/撒娇/兴奋/温柔/叙述 5 种语气，
#   比单段 10 秒保真度高）；没有多段则回退单段 ref_audio.wav。
#   → 合成全本地，火山 TTS 月费（20-30 元）也省了。
# TTS_LOCAL=True：本地克隆优先，模型没就绪/合成失败 → 自动降级火山（永远有声音）。
# 注意：本地克隆没有火山的"情绪变声"参数（音色统一优先）——生气时语速变化暂时只在火山路径生效。
# 想强制用火山 → 改成 False。
# ============================================
TTS_LOCAL = True

# 本地克隆的说话节奏/响度校准（2026-08-22 试听迭代后的最终结论）：
# ❌ 变速被否：用户对比三轮（无变速/phase vocoder 变速/WSOLA 变速）——
#   任何变速都会损伤音色（phase vocoder"重音延迟"、WSOLA"有点哑"），
#   而原始语速只比火山"快一点、轻快一点"，用户接受 → 保持 1.0 不变速。
# 如果将来想变速：WSOLA（xiaoli/wsola.py）比 phase vocoder 好，但仍非零损。
TTS_LOCAL_SPEED = 1.0

# 响度目标（RMS 均方根音量，0.1 ≈ 火山版实测 10.1%）：合成后统一归一化，
# 切音色时不会觉得"变小声了"
TTS_LOCAL_TARGET_RMS = 0.10
