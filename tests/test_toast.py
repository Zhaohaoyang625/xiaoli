# ============================================
# 桌面通知单测（2026-08-24）
# 不真弹通知：mock subprocess 验证编码/截断/降级/线程
# ============================================

import base64
import time
import unittest
from unittest import mock

from xiaoli import toast


class TestToast(unittest.TestCase):
    def tearDown(self):
        toast._warned = False

    def _decode_cmd(self, calls):
        """从 mock 调用里捞出 -EncodedCommand 参数并解码"""
        for c in calls:
            a = c.args[0] if c.args else []
            if a and a[0] == "powershell":
                return base64.b64decode(a[3]).decode("utf-16-le")
        self.fail("没找到 -EncodedCommand 调用")

    def _wait_ps(self, run):
        """等后台线程跑到 powershell 那步（reg query 会先被调，别提前退出）"""
        for _ in range(200):
            if any(c.args and c.args[0] and c.args[0][0] == "powershell"
                   for c in run.call_args_list):
                return
            time.sleep(0.01)
        self.fail("powershell 调用没发生（后台线程没跑完？）")

    def test_show_passes_encoded_command(self):
        """中文不被 GBK 控制台搞乱：整个脚本走 UTF-16LE base64"""
        with mock.patch("xiaoli.toast.subprocess.run",
                        return_value=mock.Mock(returncode=0)) as run:
            toast.show("小李找你", "寶貝～記得喝水")
            self._wait_ps(run)
        script = self._decode_cmd(run.call_args_list)
        self.assertIn("寶貝～記得喝水", script)
        self.assertIn("ToastNotificationManager", script)

    def test_message_truncated_to_preview(self):
        """通知只预览前 60 字（不刷屏）"""
        with mock.patch("xiaoli.toast.subprocess.run",
                        return_value=mock.Mock(returncode=0)) as run:
            toast.show("小李找你", "长" * 100)
            self._wait_ps(run)
        script = self._decode_cmd(run.call_args_list)
        self.assertEqual(script.count("长"), toast.MAX_PREVIEW)

    def test_single_quote_escaped(self):
        """消息里的单引号在 PS 字符串里要翻倍（否则脚本断掉）"""
        with mock.patch("xiaoli.toast.subprocess.run",
                        return_value=mock.Mock(returncode=0)) as run:
            toast.show("小李", "It's fine")
            self._wait_ps(run)
        script = self._decode_cmd(run.call_args_list)
        self.assertIn("It''s fine", script)

    def test_failure_silent(self):
        """subprocess 抛异常 → 不炸、不刷屏（只警告一次）"""
        with mock.patch("xiaoli.toast.subprocess.run",
                        side_effect=OSError("no powershell")), \
             mock.patch("builtins.print") as pr:
            toast.show("小李", "hi")
            time.sleep(0.05)
            toast.show("小李", "hi again")  # 第二次不重复警告
            time.sleep(0.05)
        self.assertEqual(pr.call_count, 1)

    def test_ensure_app_id_registers_once(self):
        """AppUserModelID 注册：已存在不重复写；不存在 → 写 DisplayName/DefaultIcon"""
        with mock.patch("xiaoli.toast.subprocess.run") as run:
            # 第一次：reg query 找不到（returncode=1）→ 走 reg add 两连
            run.return_value = mock.Mock(returncode=1)
            toast._ensure_app_id()
            adds = [c for c in run.call_args_list
                    if c.args and c.args[0][0] == "reg" and c.args[0][1] == "add"]
            self.assertEqual(len(adds), 2)
            # 第二次：reg query 找到了（returncode=0）→ 不再写
            run.reset_mock()
            run.return_value = mock.Mock(returncode=0)
            toast._ensure_app_id()
            adds = [c for c in run.call_args_list
                    if c.args and c.args[0][0] == "reg" and c.args[0][1] == "add"]
            self.assertEqual(len(adds), 0)


if __name__ == "__main__":
    unittest.main()
