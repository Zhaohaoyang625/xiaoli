# ============================================
# 网页桥 URL 拼法静态回归（2026-08-29）
# 教训：token 安全修复把 BRIDGE_TOKEN 改成 "&token=…" 时，4 个无参端点
# （/remember /photo /call_mode 同步 /heart）拼出 "/endpoint&token=…"（无 ?）
# → 后端把整个串当路径 → 403 → 网页按钮"点了没反应"。
# 潜伏原因：修复前 _bridge_token 为空 → 后端放行所有请求 → 无 ? 也通；
# token 正常生效后才暴露。Python 测试 curl 测不到浏览器 JS 的拼法。
# 这道锁：扫描 XiaoLi.html 的 fetch 调用，token 参数前必须是 ? 或 &。
# ============================================

import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_HTML = os.path.join(_HERE, "..", "web", "XiaoLi.html")


class TestWebBridgeUrls(unittest.TestCase):
    def _fetch_urls(self):
        with open(_HTML, encoding="utf-8") as f:
            src = f.read()
        # fetch(BRIDGE + "..." + BRIDGE_TOKEN(...)) 或 fetch(BRIDGE + "..." + BRIDGE_TOKEN(...), {...})
        return re.findall(r'fetch\(BRIDGE\s*\+\s*"([^"]*)"\s*\+\s*BRIDGE_TOKEN\(\)', src)

    def test_token_query_is_wellformed(self):
        """每个带 BRIDGE_TOKEN 的 fetch：token 前必须有 ? 或 &（不能是裸字符串拼接）"""
        urls = self._fetch_urls()
        self.assertTrue(urls, "应能扫到 fetch(BRIDGE + ... + BRIDGE_TOKEN()) 调用")
        for u in urls:
            # BRIDGE_TOKEN() 返回 "&token=…"，前面必须已有 ? 起 query
            # （/recent?n=20 → ?n=20&token=… 合法；无 ? → /endpoint&token=…
            #  被后端当路径 → 403）
            self.assertIn(
                "?", u,
                f"URL 片段 [{u}] 缺 ?——拼成 {u}&token=… 后端当路径 → 403（网页按钮没反应）")


if __name__ == "__main__":
    unittest.main()
