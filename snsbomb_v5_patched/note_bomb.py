"""
note_bomb.py — note.com フォロ爆 / いいね爆 CLI
Usage:
    # フォロ爆
    python note_bomb.py --action follow --target @username --count 30

    # いいね爆
    python note_bomb.py --action like --target https://note.com/user/n/nABC123 --count 20
    python note_bomb.py --action like --target nABC123 --count 20

    # 既存アカウント一覧確認
    python note_bomb.py --list-accounts

    # ヘッドレス OFF (デバッグ)
    python note_bomb.py --action follow --target @user --count 5 --no-headless
"""
import argparse
import json
import sys

from modules.proxy_manager import ProxyManager
from modules.account_gen   import generate_identity
from modules.browser        import make_driver
from modules.platforms.note import bomb, _load_accounts

import json as _json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="note.com フォロ爆 / いいね爆")
    p.add_argument("--action",  choices=["follow", "like"],
                   help="follow=フォロー爆 / like=いいね爆")
    p.add_argument("--target",  type=str,
                   help="フォロー: @urlname  /  いいね: 記事URL or note_key")
    p.add_argument("--count",   type=int, default=10,
                   help="実行回数 (デフォルト: 10)")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--no-headless", dest="headless", action="store_false")
    p.add_argument("--config",  type=str, default="config.json")
    p.add_argument("--list-accounts", action="store_true",
                   help="保存済みアカウント一覧を表示して終了")
    return p.parse_args()


def load_cfg(path: str) -> dict:
    try:
        return _json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {"recaptcha": {"api_key": ""}, "proxy_list_file": "data/proxies.txt",
                "headless": True, "delay_between_actions": [3, 8]}


def interactive_prompt() -> tuple[str, str, int]:
    """--action / --target / --count が省略された時の対話入力"""
    print("\n── note フォロ爆 / いいね爆 ──")
    print("1) フォロー爆")
    print("2) いいね爆")
    choice = input("選択 [1/2]: ").strip()
    action = "follow" if choice == "1" else "like"

    if action == "follow":
        target = input("ターゲット @urlname: ").strip()
    else:
        target = input("記事URL または note_key (例: nABC123): ").strip()

    count_raw = input("個数 (例: 20): ").strip()
    try:
        count = int(count_raw)
    except ValueError:
        print("数値を入力してください")
        sys.exit(1)

    return action, target, count


def main() -> None:
    args = parse_args()
    cfg  = load_cfg(args.config)

    # ── 一覧表示 ──────────────────────────────────────────────────────────
    if args.list_accounts:
        accounts = _load_accounts()
        if not accounts:
            print("保存済みアカウントなし")
        else:
            print(f"\n── note_accounts.json ({len(accounts)}件) ──")
            for i, a in enumerate(accounts, 1):
                status = "🚫 BAN" if a.get("banned") else "✓"
                print(f"  {i:3d}. {status}  {a['email']}  @{a.get('username','?')}")
        return

    # ── action / target / count 解決 ─────────────────────────────────────
    action = args.action
    target = args.target
    count  = args.count

    if not action or not target:
        action, target, count = interactive_prompt()

    headless    = args.headless
    captcha_key = cfg["recaptcha"]["api_key"]
    proxies     = ProxyManager(cfg["proxy_list_file"])
    proxy_dict  = proxies.get()

    print(f"\n[note_bomb] action   : {action}")
    print(f"[note_bomb] target   : {target}")
    print(f"[note_bomb] count    : {count}")
    print(f"[note_bomb] headless : {headless}")

    driver = make_driver(headless=headless, proxy=proxies.get_str())

    try:
        success, fail = bomb(
            driver          = driver,
            action          = action,
            target          = target,
            count           = count,
            proxy_dict      = proxy_dict,
            captcha_api_key = captcha_key,
            identity_fn     = generate_identity,
        )
    finally:
        driver.quit()

    print(f"\n── Done ── success={success}  fail={fail}  total={count}")
    print(f"[note_bomb] アカウントは note_accounts.json に保存済み")


if __name__ == "__main__":
    main()
