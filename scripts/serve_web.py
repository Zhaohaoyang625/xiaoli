# ============================================
# 小李网页本地服务器（2026-08-22）
# 为什么需要它：Live2D 模型必须通过 http:// 访问，
#   直接双击 XiaoLi.html 是 file:// 协议，浏览器禁止模型加载（Network error）
# 用法：双击项目根的「打开小李网页.bat」即可（自动起服务器 + 打开浏览器）
# 本脚本只服务 127.0.0.1（仅本机可访问），安全无外网暴露
# ============================================

import os
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(PROJECT_DIR, "web")
DATA_DIR = os.path.join(PROJECT_DIR, "data")
LOG_FILE = os.path.join(PROJECT_DIR, "server.log")


class LogHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 每个请求都写进 server.log（排查"模型加载失败"用；看请求到哪一步断了）
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}\n")
        except OSError:
            pass

    def translate_path(self, path):
        # 2026-08-22 关键修复：/data/* 映射到项目根 data/（主程序写的 face_state.js 在
        # 那里）。否则网页轮询 face_state.js 永远 404 → 情绪/字幕/好感全不联动
        # （实测：网页只显示模型 idle 微笑，微表情/字幕/好感条全静默）
        # ⚠️ 二修（12:19 实测）：self.path 带查询串（网页用 ?t=时间戳 防缓存），
        #   必须先剥掉，否则 "/data/face_state.js?t=..." 整个当文件名 → 404
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/data/"):
            # ⚠️ 路径穿越防护（2026-08-22 安全审计实测复现）：
            #   旧实现把 /data/ 后的内容直接拼进项目根——请求
            #   /data/../xiaoli/config.py 能读出项目根任意文件（含 API 密钥）
            #   和全部私密对话（data/ 下聊天记忆）。现在 realpath 解析后
            #   必须仍落在 DATA_DIR 内才放行，否则给一个不存在的路径（404）。
            rel = path[len("/data/"):]
            target = os.path.realpath(os.path.join(DATA_DIR, rel))
            if os.path.commonpath([target, DATA_DIR]) == DATA_DIR:
                return target
            return os.path.join(WEB_DIR, "__forbidden__")
        return super().translate_path(path)

    def end_headers(self):
        # 2026-08-22 关键修复：禁用缓存！
        # PIXI 的 XHR 加载器只认 200；第二次打开时浏览器缓存命中返回 304，
        #   被加载器误判成加载失败 → "Network error"（用户实测复现）
        # 每次都回全新 200，彻底绕开
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def main():
    os.chdir(WEB_DIR)  # 以 web/ 为根目录，XiaoLi.html 里的 ./vendor/ ./live2d/ 相对路径直接生效

    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), LogHandler)
    except OSError:
        # 端口被占：多半是服务器已经在跑，直接开浏览器即可（?v= 防旧缓存）
        webbrowser.open(f"http://127.0.0.1:{PORT}/XiaoLi.html?v=12")
        return

    # 服务器起来后自动打开网页（浏览器里的请求会排队等服务器就绪，给 0.5 秒缓冲）
    threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/XiaoLi.html?v=12")).start()

    print(f"小李网页已启动：http://127.0.0.1:{PORT}/XiaoLi.html")
    print("（这个窗口别关，关掉网页就访问不到了；想关就关闭本窗口）")
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
