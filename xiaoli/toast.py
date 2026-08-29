# ============================================
# Windows 桌面通知（toast）—— 她主动找你时弹窗（2026-08-24）
# 用途：你在别的窗口/全屏工作看不到终端时，她主动说话（节奏/闲聊/出门跟进/提醒）
#       弹系统通知，一抬头就知道"小李找你了"。
# 实现：零依赖——PowerShell 调 Windows 原生 toast（Win10/11 自带），不装任何 pip 包。
#   - 中文用 -EncodedCommand（UTF-16LE base64）传参：绕开 GBK 控制台编码坑
#   - CREATE_NO_WINDOW：不闪黑窗
#   - 后台线程跑：弹通知不阻塞说话/聊天
#   - 首次调用注册 AppUserModelID（注册表）：未注册的 appid 有时不弹
# 失败策略：静默降级（弹不出来不影响任何功能，只打印一次提示）
# ============================================

import base64
import os
import subprocess
import threading

APP_ID = "xiaoli.xiaoli"
_APP_ID_KEY = r"HKCU\SOFTWARE\Classes\AppUserModelId\xiaoli"
# 0x08000000 = CREATE_NO_WINDOW（不弹黑框）；subprocess 也吞掉控制台
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

MAX_PREVIEW = 60          # 通知里只预览前 60 字（说啥一目了然，不刷屏）
_warned = False           # 只提示一次"通知不可用"（免得每次主动说话都刷一行）


def _ensure_app_id():
    """首次弹通知前注册 AppUserModelID（Win10 未注册 appid 的 toast 可能不显示）。
    注册表写入是幂等的：已存在就跳过（reg query 判断）。"""
    try:
        r = subprocess.run(["reg", "query", _APP_ID_KEY], capture_output=True,
                           timeout=5, creationflags=_CREATE_NO_WINDOW)
        if r.returncode == 0:
            return
        subprocess.run(
            ["reg", "add", _APP_ID_KEY,
             "/v", "DisplayName", "/t", "REG_SZ", "/d", "小李", "/f"],
            capture_output=True, timeout=5, creationflags=_CREATE_NO_WINDOW)
        subprocess.run(
            ["reg", "add", _APP_ID_KEY,
             "/v", "DefaultIcon", "/t", "REG_SZ", "/d", "shell32.dll,0", "/f"],
            capture_output=True, timeout=5, creationflags=_CREATE_NO_WINDOW)
    except Exception:
        pass  # 注册不上就算了——未注册只是"可能"不弹，别的照常


def _ps_esc(s):
    """PS 单引号字符串转义：单引号翻倍"""
    return s.replace("'", "''")


def _build_script(title, msg):
    """构造 PowerShell toast 脚本（WinRT 原生通知）"""
    return (
        "$ErrorActionPreference='SilentlyContinue'\n"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]|Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]|Out-Null\n"
        f"$t='{_ps_esc(title)}'\n"
        f"$m='{_ps_esc(msg)}'\n"
        "$x=\"<toast duration='short'><visual><binding template='ToastGeneric'>"
        "<text>$t</text><text>$m</text></binding></visual></toast>\"\n"
        "$d=New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        "$d.LoadXml($x)\n"
        "$n=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('"
        + APP_ID + "')\n"
        # 方法参数里嵌 New-Object 必须套括号：$n.Show((New-Object ...))
        # ——裸写是 ParserError，整个脚本解析失败静默退出（2026-08-24 真机验收抓到）。
        # 这是普通字符串（非 f-string），$ 无需转义（转义了反而输出反斜杠）
        "$n.Show((New-Object Windows.UI.Notifications.ToastNotification -ArgumentList $d))\n"
    )


def show(title="小李找你", message=""):
    """弹桌面通知（后台线程，不阻塞）。失败静默——弹不出来不耽误说话。"""
    def _run():
        global _warned
        try:
            _ensure_app_id()
            preview = (message or "")[:MAX_PREVIEW]
            script = _build_script(title, preview)
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded],
                           capture_output=True, timeout=10,
                           creationflags=_CREATE_NO_WINDOW)
        except Exception as e:
            if not _warned:
                _warned = True
                print(f"  [桌面通知不可用：{e}（不影响聊天）]")

    threading.Thread(target=_run, daemon=True).start()


if __name__ == "__main__":
    # 自测：弹一条通知试试（1 秒后消失）
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    show("小李找你 💌", "寶貝～你忙完記得喝水喔，人家在這裡等你")
    print("[OK] 通知已发送（右下角应该弹了一条）")
