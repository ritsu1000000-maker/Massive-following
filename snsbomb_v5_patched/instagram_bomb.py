"""
instagram_bomb.py — Instagram 全アクション CLI
Usage:
    # 対話モード (アクション選択 → ターゲット → テキスト → 個数)
    python instagram_bomb.py

    # 直接指定
    python instagram_bomb.py --action follow     --target username      --count 30
    python instagram_bomb.py --action like       --target https://www.instagram.com/p/XXX/ --count 20
    python instagram_bomb.py --action comment    --target https://www.instagram.com/p/XXX/ --count 10 --text "Nice! 🔥"
    python instagram_bomb.py --action view       --target https://www.instagram.com/p/XXX/ --count 15
    python instagram_bomb.py --action live       --target username      --count 25
    python instagram_bomb.py --action impression --target https://www.instagram.com/p/XXX/ --count 20
    python instagram_bomb.py --action reels      --target https://www.instagram.com/reel/XXX/ --count 20

    python instagram_bomb.py --no-headless  # デバッグ
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from modules.proxy_manager import ProxyManager
from modules.account_gen   import generate_identity
from modules.browser       import make_driver
from modules.actions import (
    ig_follow, ig_like, ig_comment,
    ig_view_video, ig_live_view, ig_impression, ig_reels_view,
)

# ── アクション定義 ────────────────────────────────────────────────────────────
# key: CLI 引数値
# fn:  actions.py の関数
# target_hint: --target に何を渡すか (表示用)
# has_text: --text オプションを使うか
ACTIONS: dict[str, dict] = {
    "follow":     {"fn": ig_follow,      "target_hint": "@ユーザー名",          "has_text": False},
    "like":       {"fn": ig_like,        "target_hint": "投稿URL",              "has_text": False},
    "comment":    {"fn": ig_comment,     "target_hint": "投稿URL",              "has_text": True},
    "view":       {"fn": ig_view_video,  "target_hint": "動画URL",              "has_text": False},
    "live":       {"fn": ig_live_view,   "target_hint": "@ユーザー名 (配信中)", "has_text": False},
    "impression": {"fn": ig_impression,  "target_hint": "投稿/プロフURL",       "has_text": False},
    "reels":      {"fn": ig_reels_view,  "target_hint": "Reels URL",            "has_text": False},
}

ACTION_LABELS = {
    "follow":     "フォロー爆",
    "like":       "いいね爆",
    "comment":    "コメント爆",
    "view":       "動画再生爆",
    "live":       "ライブ視聴者爆",
    "impression": "インプレッション爆",
    "reels":      "Reels再生爆",
}


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Instagram 全アクション爆")
    p.add_argument("--action",  choices=list(ACTIONS.keys()))
    p.add_argument("--target",  type=str)
    p.add_argument("--count",   type=int, default=10)
    p.add_argument("--text",    type=str, default="",
                   help="コメントテキスト (--action comment 時)")
    p.add_argument("--headless",    action="store_true", default=True)
    p.add_argument("--no-headless", dest="headless", action="store_false")
    p.add_argument("--config",  type=str, default="config.json")
    return p.parse_args()


def load_cfg(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {
            "recaptcha": {"api_key": ""},
            "proxy_list_file": "data/proxies.txt",
            "headless": True,
            "delay_between_actions": [3, 8],
        }


def interactive_prompt() -> tuple[str, str, str, int]:
    """引数省略時の対話入力。(action, target, text, count) を返す。"""
    print("\n── Instagram 爆ツール ──")
    for i, (key, label) in enumerate(ACTION_LABELS.items(), 1):
        print(f"  {i}) {label}")
    choice = input("選択 [1-7]: ").strip()
    keys = list(ACTIONS.keys())
    try:
        action = keys[int(choice) - 1]
    except (ValueError, IndexError):
        print("無効な選択"); sys.exit(1)

    hint = ACTIONS[action]["target_hint"]
    target = input(f"ターゲット ({hint}): ").strip()

    text = ""
    if ACTIONS[action]["has_text"]:
        text = input("テキスト (空欄でデフォルト 'Nice! 🔥'): ").strip()

    count_raw = input("個数: ").strip()
    try:
        count = int(count_raw)
    except ValueError:
        print("数値を入力してください"); sys.exit(1)

    return action, target, text, count


# ── メインループ ──────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    cfg  = load_cfg(args.config)

    action = args.action
    target = args.target
    text   = args.text
    count  = args.count

    if not action or not target:
        action, target, text, count = interactive_prompt()

    headless    = args.headless
    captcha_key = cfg["recaptcha"]["api_key"]
    proxies     = ProxyManager(cfg["proxy_list_file"])
    delay_range = cfg.get("delay_between_actions", [3, 8])

    action_cfg = ACTIONS[action]
    fn         = action_cfg["fn"]

    print(f"\n[ig_bomb] action   : {ACTION_LABELS[action]}")
    print(f"[ig_bomb] target   : {target}")
    if text:
        print(f"[ig_bomb] text     : {text}")
    print(f"[ig_bomb] count    : {count}")
    print(f"[ig_bomb] headless : {headless}")

    success = 0
    fail    = 0

    for i in range(1, count + 1):
        proxy_str  = proxies.get_str()
        proxy_dict = proxies.get()
        identity   = generate_identity()

        print(f"\n── {i}/{count} ({ACTION_LABELS[action]}) ──")
        driver = make_driver(headless=headless, proxy=proxy_str)

        try:
            # コメントは追加引数あり
            if action == "comment":
                ok = fn(driver, target, identity, proxy_dict, captcha_key,
                        comment_text=text or "Nice! 🔥")
            else:
                ok = fn(driver, target, identity, proxy_dict, captcha_key)

            if ok:
                success += 1
                print(f"[ig_bomb] ✓ {i}/{count} done")
            else:
                fail += 1
                print(f"[ig_bomb] ✗ {i}/{count} failed")

        except Exception as e:
            print(f"[ig_bomb] unhandled: {e}")
            fail += 1
        finally:
            driver.quit()

        if i < count:
            sleep = random.uniform(*delay_range)
            print(f"[ig_bomb] sleeping {sleep:.1f}s …")
            time.sleep(sleep)

    print(f"\n── Done ── success={success}  fail={fail}  total={count}")


if __name__ == "__main__":
    main()
