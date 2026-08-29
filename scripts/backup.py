# ============================================
# 一键备份聊天数据（2026-08-23）
# 把 data/（聊天记录/记忆/她的心/照片收件箱）打包成 zip 存到 backups/。
# data/ 是私密数据（git 不跟踪），电脑坏了/误删就没了——定期备份。
# 自动保留最近 5 份，更旧的删掉。
# 用法：python scripts/backup.py
# ============================================

import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
BACKUP_DIR = os.path.join(ROOT, "backups")
KEEP = 5  # 保留最近几份


def make_backup():
    """打包 data/ → backups/backup_时间戳.zip。返回备份路径或 None"""
    if not os.path.isdir(DATA):
        print("[X] data/ 不存在，没什么好备份的")
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(BACKUP_DIR, f"backup_{stamp}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirnames, filenames in os.walk(DATA):
            for name in filenames:
                p = os.path.join(dirpath, name)
                # arcname：zip 内用相对路径，解压后是 data/... 结构
                zf.write(p, os.path.relpath(p, ROOT))
    size_mb = os.path.getsize(out) / 1024 / 1024
    _prune_old()
    print(f"[OK] 备份完成：{os.path.relpath(out, ROOT)}（{size_mb:.1f} MB）")
    return out


def _prune_old():
    """只留最近 KEEP 份备份"""
    backups = sorted(
        (os.path.join(BACKUP_DIR, n) for n in os.listdir(BACKUP_DIR) if n.endswith(".zip")),
        key=os.path.getmtime)
    for old in backups[:-KEEP]:
        os.remove(old)
        print(f"  （清理旧备份：{os.path.basename(old)}）")


if __name__ == "__main__":
    # Windows 终端可能是 GBK，打印用 ASCII 防崩
    try:
        path = make_backup()
    except Exception as e:
        print(f"[X] 备份失败：{e}")
        sys.exit(1)
    if not path:
        sys.exit(1)
