# ============================================
# 密钥管理脚本（2026-08-22 S1 密钥轮换后新增）
# 密钥不再明文写在 config.py 里（那是安全泄漏面——路径穿越漏洞实测
# 证明过本地程序能读出明文密钥），改存进 Windows 凭据管理器
# （系统级加密，只有本机和你的账号能读）。
#
# 用法（在项目根目录打开终端）：
#   1. 换新密钥：   python scripts/setup_keys.py --set deepseek
#                   回车后会提示你粘贴密钥（输入时不显示，安全）
#   2. 命令行直接给（脚本/自动化用）：python scripts/setup_keys.py --set deepseek sk-xxxxx
#   3. 查看状态：   python scripts/setup_keys.py --show   （只显示打码，不显示完整密钥）
#   4. 删除密钥：   python scripts/setup_keys.py --delete deepseek
#   支持的账户：deepseek（大脑）、volc（火山语音）
# ============================================

import argparse
import getpass
import sys
import keyring

SERVICE = "xiaoli"
ACCOUNTS = {
    "deepseek": "DeepSeek API Key（大脑，在 platform.deepseek.com 创建）",
    "volc": "火山引擎 API Key（语音，控制台「API Key」页面）",
}


def get_key(account):
    return keyring.get_password(SERVICE, account)


def set_key(account, value):
    keyring.set_password(SERVICE, account, value)


def delete_key(account):
    try:
        keyring.delete_password(SERVICE, account)
        print(f"[OK] 已删除 {account} 的密钥")
    except keyring.errors.PasswordDeleteError:
        print(f"[i]  {account} 本来就没有密钥")


def masked(value):
    if not value:
        return "(未设置)"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def main():
    parser = argparse.ArgumentParser(description="小李密钥管理（存 Windows 凭据管理器，输出用 GBK 安全字符）")
    parser.add_argument("--set", metavar="ACCOUNT", help="设置密钥，如 deepseek / volc")
    parser.add_argument("--value", metavar="KEY", help="密钥值（不填则交互式粘贴，输入不显示）")
    parser.add_argument("--delete", metavar="ACCOUNT", help="删除密钥")
    parser.add_argument("--show", action="store_true", help="查看各密钥是否已设置（打码显示）")
    args = parser.parse_args()

    if args.set:
        account = args.set.lower()
        if account not in ACCOUNTS:
            print(f"[X] 未知账户：{account}，支持：{'、'.join(ACCOUNTS)}")
            sys.exit(1)
        value = args.value
        if not value:
            print(f"粘贴你的 {ACCOUNTS[account]}（输入不显示，回车确认）：")
            value = getpass.getpass("> ").strip()
        if not value:
            print("[X] 不能为空")
            sys.exit(1)
        set_key(account, value)
        print(f"[OK] {account} 密钥已存入 Windows 凭据管理器（{masked(value)}）")
        return

    if args.delete:
        delete_key(args.delete.lower())
        return

    if args.show:
        for account in ACCOUNTS:
            print(f"  {account:8s} {masked(get_key(account))}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
