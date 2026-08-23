# ============================================
# 统一大脑客户端工厂（2026-08-23 C1：API 超时修复）
# 之前 13 处各自 new OpenAI(api_key, base_url)，全部用 SDK 默认超时
# （连接 600 秒/读取 600 秒）——断网/网络挂起时请求挂 10 分钟才超时，
# 主循环卡死（用户实测过"卡住"）。统一：连接 5 秒 / 读取 30 秒。
# 超时后的命运交给各调用点的兜底链（重试/降级/静默跳过），对话不断。
# 所有"调大脑"的地方必须走这里，新增调用点也走这里（防再次裸 new）。
# ============================================

import httpx
from openai import OpenAI

from xiaoli import config

# 连接 5 秒（找不到服务器立刻放弃）；读取 30 秒（生成慢/搜索慢也够；
# 实测一次带 web_search 的问答远小于 30s；真超时走重试链，比卡死强）
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def get_client():
    """统一 DeepSeek 客户端。测试可 patch 这个函数（@patch("xiaoli.llm.get_client")）。"""
    return OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
        timeout=DEFAULT_TIMEOUT,
    )
