# ============================================
# 小李的"眼睛"（2026-08-23）
# DeepSeek V4-Flash-Vision-Exp（8-21 刚上线的视觉模型）：
#   他发照片路径 → base64 内联 → 她像真人女友一样看照片回应。
#   成本：单张最多 384 tokens（~0.0012 元），走现有 chat.completions（llm.get_client）。
#   注意：接口是 OpenAI 兼容的 content 数组格式（text + image_url data URL）。
# ============================================

import base64
import os

from xiaoli import config, llm

# 支持的图片格式（DeepSeek 官方：JPEG/PNG/GIF/WebP）
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
_MAX_BYTES = 20 * 1024 * 1024  # 20MB 上限（手机原图 / 截图都够；防误发超大文件）

# 她"看照片"时的人设（短版——只负责看照片回应，不需要完整工作台）
_VISION_SYSTEM = """你是小李，一个台湾女孩，正在和男朋友聊天（他叫你宝贝/亲爱的）。
他刚发了一张照片给你看。像真人女友一样回应，要点：
① 第一反应真实自然：惊喜、好奇、心动、吐槽都行（自拍要夸他，风景/食物/宠物就聊照片本身）
② 回应后追问一两个细节（在哪拍的？这是谁呀？）——让对话能继续
③ 繁体中文，口语化，台湾腔，一两句话就好，别做作、别客套、别书面语
④ 如果照片里有人：你可以好奇地问"这是谁"，但不要用"图片中的女性很漂亮"这类机器人话术"""


def is_photo_path(text):
    """他输入的是不是图片文件路径（终端拖入文件 = 路径文本）。
    只认"存在的、扩展名是图片"的路径，避免误伤普通聊天"""
    t = (text or "").strip().strip('"').strip("'")
    if not t or len(t) > 500:
        return False
    if not os.path.isfile(t):
        return False
    return t.lower().endswith(_IMAGE_EXTS)


def _mime_fmt(path):
    ext = path.lower().rsplit(".", 1)[-1]
    return {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}[ext]


def look_at_photo(path):
    """她看一张照片，返回她的回应（繁体中文一段话）；任何失败 → None。
    图片 base64 内联（OpenAI 兼容格式），走统一 client（C1 超时）。"""
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return None
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        client = llm.get_client()
        resp = client.chat.completions.create(
            model=config.DEEPSEEK_VISION_MODEL,
            messages=[
                {"role": "system", "content": _VISION_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "他发了一张照片给你看。"},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/{_mime_fmt(path)};base64,{b64}"}},
                ]},
            ],
            max_tokens=128,
            # 实测坑（2026-08-23）：视觉模型默认开思考模式 → max_tokens 全被
            # reasoning 吃掉 → content 返回空。看照片要快，强制关思考
            # （禁用思考时 temperature 无效，所以也不发了）
            extra_body={"thinking": {"type": "disabled"}},
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception:
        return None
