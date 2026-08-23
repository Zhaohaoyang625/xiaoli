# ============================================
# 网页 JS 语法兜底检查（2026-08-23）
# 前端没有自动化测试——低级语法错误（少括号/引号没闭合）只能靠这个提前抓。
# 用法：python scripts/check_web_js.py   （需要装了 node）
# 注意：运行时错误（比如 status 未定义那种）node --check 抓不到，
#       那是浏览器行为，靠人工实测 + 小心 review。
# ============================================

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "XiaoLi.html")


def main():
    try:
        with open(HTML, encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        print(f"[X] 读不到 {HTML}：{e}")
        return 1
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not scripts:
        print("[X] 没找到内联 script 块")
        return 1
    tmp = os.path.join(tempfile.gettempdir(), "xiaoli_check.js")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n;\n".join(scripts))
    r = subprocess.run(["node", "--check", tmp],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"[OK] 网页 JS 语法正确（{len(scripts)} 个内联块）")
        return 0
    print("[X] 网页 JS 语法错误：")
    print(r.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
