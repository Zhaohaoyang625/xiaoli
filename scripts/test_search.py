# ============================================
# 联网搜索实测（2026-08-22 新增）
# 验证 DeepSeek 官方 Responses API 的 server-side web_search 工具：
#   1. responses.create 接口是否可用
#   2. web_search 是否能返回实时结果（热梗/天气等）
# 密钥从 Windows 凭据管理器读取（不写明文）
# ============================================

import sys
import os

# Windows 终端默认 GBK，emoji/特殊字符打不出来 → 强制 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from xiaoli import llm  # 统一大脑客户端（C1：连接5s/读取30s 超时）

# 1. 确认 SDK 版本支持 responses
import openai
print(f"openai SDK: {openai.__version__}")

client = llm.get_client()

# 2. 不带搜索的对照（确认 responses API 本身可用）
print("\n=== 对照：responses.create 不带搜索 ===")
r0 = client.responses.create(
    model=config.DEEPSEEK_MODEL,
    input="说一句：你好",
)
print("输出:", r0.output_text)

# 3. 带 web_search 工具（强制搜索）
print("\n=== 实测：web_search 联网搜索 ===")
r = client.responses.create(
    model=config.DEEPSEEK_MODEL,
    input="今天有什么新出的网络热梗？查一下再回答",
    tools=[{"type": "web_search"}],
    tool_choice={"type": "web_search"},  # 强制联网
)
for item in r.output:
    if item.type == "web_search_call":
        print(f"  [联网] {item.action}")
    elif item.type == "message":
        for c in item.content:
            if hasattr(c, "text"):
                print(f"\n  [回答] {c.text[:500]}")

print("\n=== 费用明细 ===")
u = r.usage
print(f"  输入 tokens: {u.input_tokens}")
print(f"  输出 tokens: {u.output_tokens}")
print(f"  缓存命中: {getattr(u, 'input_tokens_details', None)}")
if hasattr(r, "_raw_response") and r._raw_response is not None:
    raw = r._raw_response.headers.get("x-search-cost", "") if hasattr(r._raw_response, "headers") else ""
    if raw:
        print(f"  搜索费用头: {raw}")
