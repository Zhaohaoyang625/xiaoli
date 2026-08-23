# ============================================
# 安全回归测试（2026-08-22 安全审计新增）
# 覆盖两个已实测复现的漏洞：
#   S2 serve_web 路径穿越——/data/../xiaoli/config.py 曾能读出项目根任意文件（含密钥）
#   S3 WebBridge 零鉴权——任意请求曾可触发录音/写记忆/开监听
# 原则：不碰真实端口/真实服务，纯逻辑层验证
# ============================================

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestServeWebTraversal(unittest.TestCase):
    """S2：/data/* 映射必须锁死在 data/ 目录内（路径穿越回归）"""

    @classmethod
    def setUpClass(cls):
        # 直接从 scripts/serve_web.py 取 translate_path（纯函数，不起服务器）
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "serve_web", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      "scripts", "serve_web.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.handler = mod.LogHandler

    def _translate(self, path):
        h = self.handler.__new__(self.handler)
        h.path = path
        return h.translate_path(path)

    def _norm(self, p):
        # Windows 盘符大小写差异（c:/ vs C:/）会让纯字符串 endswith 偶发误判
        return os.path.normcase(os.path.normpath(p))

    def test_normal_data_path_ok(self):
        """正常 /data/face_state.js → data/ 下真实路径"""
        p = self._translate("/data/face_state.js")
        self.assertTrue(self._norm(p).endswith(self._norm(os.path.join("data", "face_state.js"))))

    def test_data_with_query_ok(self):
        """带 ?t= 防缓存参数 → 剥掉后正常映射"""
        p = self._translate("/data/face_state.js?t=123")
        self.assertTrue(self._norm(p).endswith(self._norm(os.path.join("data", "face_state.js"))))

    def test_traversal_blocked(self):
        """路径穿越 → 不存在的路径（404），绝不能读出项目根文件"""
        p = self._translate("/data/../xiaoli/config.py")
        self.assertFalse(os.path.isfile(p))  # 关键断言：解析出的文件不存在
        # 更本质：解析结果绝不能指向真实的 config.py（注：不能用 assertNotIn("xiaoli")，
        # 项目根目录名 XiaoLi 转小写后含 "xiaoli"，会误伤）
        real_config = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "xiaoli", "config.py")
        self.assertNotEqual(os.path.normcase(os.path.normpath(p)),
                            os.path.normcase(os.path.normpath(real_config)))

    def test_deep_traversal_blocked(self):
        """深层穿越 /data/../../XiaoLi.html 同样封死"""
        p = self._translate("/data/../../XiaoLi.html")
        self.assertFalse(os.path.isfile(p))

    def test_data_itself_is_dir(self):
        """/data/ 映射到 data 目录本身（不应炸）"""
        p = self._translate("/data/")
        self.assertTrue(self._norm(p).endswith(self._norm(os.path.join("data"))))


class TestWebBridgeAuth(unittest.TestCase):
    """S3：WebBridge 必须带会话 token，无 token 一律 403"""

    def test_no_token_forbidden(self):
        """无 token → 403（不放行任何端点）"""
        from xiaoli import chat
        h = chat.WebBridge.__new__(chat.WebBridge)
        h.path = "/listen"
        h.headers = {}
        with mock.patch.object(chat, "_bridge_token", "secret123"), \
             mock.patch.object(h, "_json") as js:
            chat.WebBridge.do_GET(h)
            js.assert_called_once_with({"ok": False, "error": "forbidden"}, 403)

    def test_wrong_token_forbidden(self):
        """token 不对 → 403"""
        from xiaoli import chat
        h = chat.WebBridge.__new__(chat.WebBridge)
        h.path = "/listen?token=wrong"
        h.headers = {}
        with mock.patch.object(chat, "_bridge_token", "secret123"), \
             mock.patch.object(h, "_json") as js:
            chat.WebBridge.do_GET(h)
            js.assert_called_once_with({"ok": False, "error": "forbidden"}, 403)

    def test_empty_bridge_token_forbidden(self):
        """桥 token 尚未生成（_bridge_token 为空）→ 一律拒绝（防启动间隙）"""
        from xiaoli import chat
        h = chat.WebBridge.__new__(chat.WebBridge)
        h.path = "/listen?token=x"
        h.headers = {}
        with mock.patch.object(chat, "_bridge_token", ""), \
             mock.patch.object(h, "_json") as js:
            chat.WebBridge.do_GET(h)
            js.assert_called_once_with({"ok": False, "error": "forbidden"}, 403)

    def test_correct_token_passes(self):
        """token 正确 → 走到端点处理（/set_voice 的副作用不触发——mock 掉）"""
        from xiaoli import chat
        h = chat.WebBridge.__new__(chat.WebBridge)
        h.path = "/set_voice?on=1&token=secret123"
        h.headers = {}
        with mock.patch.object(chat, "_bridge_token", "secret123"), \
             mock.patch.object(h, "_json") as js, \
             mock.patch("xiaoli.chat.voice_on", False):
            chat.WebBridge.do_GET(h)
            # 走到端点：set_voice 会调 _json 返回开关状态（而非 403）
            args = js.call_args[0][0]
            self.assertTrue(args.get("ok"))
            self.assertIn("voice", args)

    def test_cors_whitelist(self):
        """CORS：只放行 8080 页面与 file://（null），恶意来源不回 CORS 头"""
        from xiaoli import chat
        h = chat.WebBridge.__new__(chat.WebBridge)
        h.wfile = mock.Mock()
        h.send_response = mock.Mock()
        h.send_header = mock.Mock()
        h.end_headers = mock.Mock()
        h.headers = {"Origin": "http://evil.example.com"}
        h._json({"ok": True})
        origins = [c[0][1] for c in h.send_header.call_args_list
                   if c[0][0] == "Access-Control-Allow-Origin"]
        self.assertEqual(origins, [])  # 恶意来源：不给 CORS 头（浏览器拦截读响应）

        h2 = chat.WebBridge.__new__(chat.WebBridge)
        h2.wfile = mock.Mock()
        h2.send_response = mock.Mock()
        h2.send_header = mock.Mock()
        h2.end_headers = mock.Mock()
        h2.headers = {"Origin": "http://127.0.0.1:8080"}
        h2._json({"ok": True})
        origins = [c[0][1] for c in h2.send_header.call_args_list
                   if c[0][0] == "Access-Control-Allow-Origin"]
        self.assertEqual(origins, ["http://127.0.0.1:8080"])  # 我们的页面正常放行


if __name__ == "__main__":
    unittest.main(verbosity=2)
