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


class TestStaticServing(unittest.TestCase):
    """S4：Live2D 白名单静态服务（2026-08-23 修复 file:// 模型加载 Network error）。
    只服务 web/live2d/ + web/vendor/；穿越/白名单外一律拒绝；文件无敏感 → CORS 全放行"""

    def _h(self, path):
        from xiaoli import chat
        h = chat.WebBridge.__new__(chat.WebBridge)
        h.path = path
        h.wfile = mock.Mock()
        h.send_response = mock.Mock()
        h.send_header = mock.Mock()
        h.end_headers = mock.Mock()
        h.headers = {}
        return h

    def test_serve_live2d_model(self):
        """/live2d/* → 返回真实文件内容 + CORS 全放行 + 正确 Content-Type"""
        from xiaoli import chat
        h = self._h("/live2d/shizuku/shizuku.model.json")
        with mock.patch("xiaoli.chat.os.path.isfile", return_value=True), \
             mock.patch("xiaoli.chat.open", mock.mock_open(read_data=b'{"x":1}')):
            self.assertTrue(chat.WebBridge._serve_static(h, "/live2d/shizuku/shizuku.model.json"))
        h.send_response.assert_called_once_with(200)
        types = {c[0][1] for c in h.send_header.call_args_list}
        self.assertIn("*", types)  # CORS 全放行
        self.assertIn("application/json", types)
        h.wfile.write.assert_called_once_with(b'{"x":1}')

    def test_serve_vendor_js(self):
        """/vendor/*（live2d.min.js 等引擎库）同样放行"""
        from xiaoli import chat
        h = self._h("/vendor/live2d.min.js")
        with mock.patch("xiaoli.chat.os.path.isfile", return_value=True), \
             mock.patch("xiaoli.chat.open", mock.mock_open(read_data=b"js")):
            self.assertTrue(chat.WebBridge._serve_static(h, "/vendor/live2d.min.js"))

    def test_traversal_blocked(self):
        """路径穿越 /live2d/../../xiaoli/config.py → 拒绝（S2 教训回归）"""
        from xiaoli import chat
        h = self._h("/live2d/../../xiaoli/config.py")
        with mock.patch("xiaoli.chat.os.path.isfile") as m:
            self.assertFalse(chat.WebBridge._serve_static(h, "/live2d/../../xiaoli/config.py"))
            m.assert_not_called()  # 穿越路径根本不碰文件系统

    def test_whitelist_only(self):
        """白名单外（/data/face_state.js 含 token、/ 首页）→ 拒绝，绝不静态输出"""
        from xiaoli import chat
        h = self._h("/data/face_state.js")
        self.assertFalse(chat.WebBridge._serve_static(h, "/data/face_state.js"))
        h2 = self._h("/")
        self.assertFalse(chat.WebBridge._serve_static(h2, "/"))
        h3 = self._h("/XiaoLi.html")
        self.assertFalse(chat.WebBridge._serve_static(h3, "/XiaoLi.html"))

    def test_do_get_static_before_auth(self):
        """do_GET：静态分支在鉴权之前——Live2D fetch 无 token 也必须能读到（文件无敏感）"""
        from xiaoli import chat
        h = self._h("/live2d/x.model.json")
        with mock.patch.object(chat, "_bridge_token", "secret123"), \
             mock.patch.object(chat.WebBridge, "_serve_static", return_value=True) as ss, \
             mock.patch.object(h, "_json") as js:
            chat.WebBridge.do_GET(h)
            ss.assert_called_once_with("/live2d/x.model.json")
            js.assert_not_called()  # 没走 403


if __name__ == "__main__":
    unittest.main(verbosity=2)
