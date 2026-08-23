# ============================================
# 启动备份提醒 _backup_due 单测（2026-08-23）
# 数据是无价的：超过 7 天没备份，启动自检要提醒跑 scripts/backup.py
# ============================================

import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from xiaoli import chat


class TestBackupDue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(chat.paths, "ROOT", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def _make_backup(self, age_days):
        """造一个 mtime 为 age_days 天前的备份 zip"""
        d = os.path.join(self.tmp, "backups")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "backup_old.zip")
        with open(p, "wb") as f:
            f.write(b"fake")
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
        return p

    def test_no_backup_dir_means_due(self):
        """从没备份过 → 到期（要提醒）"""
        self.assertTrue(chat._backup_due())

    def test_empty_backup_dir_means_due(self):
        os.makedirs(os.path.join(self.tmp, "backups"))
        self.assertTrue(chat._backup_due())

    def test_fresh_backup_not_due(self):
        self._make_backup(age_days=1)
        self.assertFalse(chat._backup_due())

    def test_old_backup_due(self):
        self._make_backup(age_days=8)
        self.assertTrue(chat._backup_due())

    def test_ignores_non_zip_files(self):
        """backups/ 里只有别的文件（不算备份）→ 到期"""
        d = os.path.join(self.tmp, "backups")
        os.makedirs(d)
        with open(os.path.join(d, "note.txt"), "w") as f:
            f.write("hi")
        self.assertTrue(chat._backup_due())

    def test_newest_zip_wins(self):
        """多个备份取最新的那个"""
        self._make_backup(age_days=10)
        self._make_backup(age_days=1)
        self.assertFalse(chat._backup_due())


class TestRunBackup(unittest.TestCase):
    """终端「备份」命令：调 scripts/backup.py（参数正确，不吞异常）"""

    def _call(self, side_effect=None):
        out = io.StringIO()
        with mock.patch("subprocess.run") as run_mock, redirect_stdout(out):
            run_mock.side_effect = side_effect
            chat._run_backup()
        return run_mock, out.getvalue()

    def test_calls_backup_script(self):
        run_mock, out = self._call()
        run_mock.assert_called_once()
        args, kwargs = run_mock.call_args
        cmd = args[0]
        self.assertTrue(cmd[0].endswith("python.exe") or "python" in cmd[0])
        self.assertTrue(cmd[1].endswith(os.path.join("scripts", "backup.py")))
        self.assertEqual(kwargs["timeout"], 120)

    def test_failure_printed_not_crash(self):
        run_mock, out = self._call(side_effect=RuntimeError("磁盘满"))
        self.assertIn("备份失败", out)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
