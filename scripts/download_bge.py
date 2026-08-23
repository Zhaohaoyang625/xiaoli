# ============================================
# bge-small-zh-v1.5 ONNX 模型下载器（O3 可选增强，2026-08-22）
# 背景：hf-mirror.com 是唯一有 ONNX 版 bge 的源（ModelScope 只有 PyTorch 版，
# 装 torch 转格式违背项目轻原则）。它 2026-08-22 间歇性故障（000 连不上）。
# 本脚本：每 5 分钟探测一次，恢复后断点续传下载 model.onnx + vocab.txt，
# 下载完校验大小，成功即退出。跑在后台，不阻塞主流程。
# 用法：python download_bge.py
# ============================================

import os
import subprocess
import sys
from xiaoli import paths  # 统一路径
import time

BASE = "https://hf-mirror.com/BAAI/bge-small-zh-v1.5/resolve/main"
OUT_DIR = os.path.join(paths.MODELS_DIR, "bge-small-zh")
FILES = [("model.onnx", 95_000_000), ("vocab.txt", 100_000)]  # (文件名, 最小字节数)


def fetch(url, out_path, min_size):
    """断点续传 + 长超时下载；成功返回 True"""
    if os.path.exists(out_path) and os.path.getsize(out_path) >= min_size:
        print(f"  {os.path.basename(out_path)} 已就绪（{os.path.getsize(out_path)} 字节）")
        return True
    cmd = ["curl", "-C", "-", "-L", "--connect-timeout", "20",
           "--max-time", "580", "--retry", "3", "--retry-delay", "5",
           "-o", out_path, url]
    r = subprocess.run(cmd, capture_output=True)
    if os.path.exists(out_path) and os.path.getsize(out_path) >= min_size:
        print(f"  ✓ {os.path.basename(out_path)} 下载完成（{os.path.getsize(out_path)} 字节）")
        return True
    if r.returncode != 0:
        print(f"  … {os.path.basename(out_path)} 下载中断（rc={r.returncode}，已下载 "
              f"{os.path.getsize(out_path) if os.path.exists(out_path) else 0} 字节），等镜像恢复后继续")
    return False


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    os.makedirs(OUT_DIR, exist_ok=True)
    attempts = 0
    while attempts < 72:  # 最多等 6 小时
        attempts += 1
        print(f"[{time.strftime('%H:%M:%S')}] 探测 hf-mirror 连通性（第 {attempts} 次）…", flush=True)
        probe = subprocess.run(
            ["curl", "-s", "--connect-timeout", "10", "--max-time", "20",
             "-o", os.devnull, "-w", "%{http_code}",
             BASE + "/vocab.txt"], capture_output=True, text=True)
        ok = probe.stdout.strip() == "200"
        if ok:
            done = all(fetch(f"{BASE}/{name}", os.path.join(OUT_DIR, name), min_b)
                       for name, min_b in FILES)
            if done:
                print("🎉 bge 模型全部就绪，embed.py 会自动启用真向量！")
                return
        else:
            print(f"  hf-mirror 未恢复（{probe.stdout.strip() or probe.stderr.strip()[:60]}）")
        print("  5 分钟后重试…", flush=True)
        time.sleep(300)
    print("（6 小时没等到镜像恢复；不影响使用——字符向量版语义检索照常工作）")


if __name__ == "__main__":
    main()
