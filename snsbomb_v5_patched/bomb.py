"""
bomb.py — SNS 全プラットフォーム統合 CLI
対話メニューでプラットフォーム → アクション → ターゲット → 個数を選ぶだけ。

Usage:
    python bomb.py              # 対話モード
    python bomb.py --no-headless
    python bomb.py --config path/to/config.json
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
    ig_follow, ig_like, ig_comment, ig_view_video,
    ig_live_view, ig_impression, ig_reels_view,
    x_follow, x_like, x_repost, x_reply, x_impression, x_view_video, x_poll_vote,
    yt_subscribe, yt_view, yt_like, yt_live_view, yt_shorts_view, yt_comment,
    tt_follow, tt_like, tt_view, tt_comment, tt_share, tt_live_view,
    fb_page_follow, fb_post_like, fb_video_view, fb_story_view, fb_group_join, fb_comment,
)
from modules.platforms.note import bomb as note_bomb

# ── アクションテーブル ────────────────────────────────────────────────────────
# fmt: {label, fn, target_hint, has_text, extra_args}
# extra_args: fn に渡す追加 kwargs を作る callable(text) | None

MENU: dict[str, list[dict]] = {
    "Instagram": [
        {"label": "フォロー",           "fn": ig_follow,      "hint": "@ユーザー名",           "has_text": False},
        {"label": "いいね",             "fn": ig_like,        "hint": "投稿URL",               "has_text": False},
        {"label": "コメント",           "fn": ig_comment,     "hint": "投稿URL",               "has_text": True,  "text_key": "comment_text"},
        {"label": "動画再生",           "fn": ig_view_video,  "hint": "動画URL",               "has_text": False},
        {"label": "ライブ視聴者",       "fn": ig_live_view,   "hint": "@ユーザー名(配信中)",   "has_text": False},
        {"label": "インプレッション",   "fn": ig_impression,  "hint": "投稿/プロフURL",         "has_text": False},
        {"label": "Reels再生",          "fn": ig_reels_view,  "hint": "Reels URL",             "has_text": False},
    ],
    "X (Twitter)": [
        {"label": "フォロー",       "fn": x_follow,      "hint": "@ユーザー名",       "has_text": False},
        {"label": "いいね",         "fn": x_like,        "hint": "ツイートURL",       "has_text": False},
        {"label": "リポスト",       "fn": x_repost,      "hint": "ツイートURL",       "has_text": False},
        {"label": "リプライ",       "fn": x_reply,       "hint": "ツイートURL",       "has_text": True,  "text_key": "comment_text"},
        {"label": "インプレッション","fn": x_impression,  "hint": "ツイートURL",       "has_text": False},
        {"label": "動画再生",       "fn": x_view_video,  "hint": "動画ツイートURL",   "has_text": False},
        {"label": "投票",           "fn": x_poll_vote,   "hint": "投票ツイートURL",   "has_text": False},
    ],
    "YouTube": [
        {"label": "チャンネル登録", "fn": yt_subscribe,   "hint": "チャンネルURL/@handle", "has_text": False},
        {"label": "動画再生",       "fn": yt_view,        "hint": "動画URL",               "has_text": False},
        {"label": "いいね",         "fn": yt_like,        "hint": "動画URL",               "has_text": False},
        {"label": "ライブ視聴者",   "fn": yt_live_view,   "hint": "ライブURL",             "has_text": False},
        {"label": "Shorts再生",     "fn": yt_shorts_view, "hint": "Shorts URL",            "has_text": False},
        {"label": "コメント",       "fn": yt_comment,     "hint": "動画URL",               "has_text": True,  "text_key": "comment_text"},
    ],
    "TikTok": [
        {"label": "フォロー",       "fn": tt_follow,    "hint": "@ユーザー名",       "has_text": False},
        {"label": "いいね",         "fn": tt_like,      "hint": "動画URL",           "has_text": False},
        {"label": "動画再生",       "fn": tt_view,      "hint": "動画URL",           "has_text": False},
        {"label": "コメント",       "fn": tt_comment,   "hint": "動画URL",           "has_text": True,  "text_key": "comment_text"},
        {"label": "シェア",         "fn": tt_share,     "hint": "動画URL",           "has_text": False},
        {"label": "ライブ視聴者",   "fn": tt_live_view, "hint": "@ユーザー名(配信中)","has_text": False},
    ],
    "Facebook": [
        {"label": "ページフォロー", "fn": fb_page_follow, "hint": "ページURL/@handle", "has_text": False},
        {"label": "投稿いいね",     "fn": fb_post_like,   "hint": "投稿URL",           "has_text": False},
        {"label": "動画再生",       "fn": fb_video_view,  "hint": "動画URL",           "has_text": False},
        {"label": "ストーリー閲覧", "fn": fb_story_view,  "hint": "ユーザー/ページURL","has_text": False},
        {"label": "グループ参加",   "fn": fb_group_join,  "hint": "グループURL",       "has_text": False},
        {"label": "コメント",       "fn": fb_comment,     "hint": "投稿URL",           "has_text": True,  "text_key": "comment_text"},
    ],
    "note": [
        {"label": "フォロー",   "hint": "@urlname",                          "note_action": "follow"},
        {"label": "いいね",     "hint": "記事URL または note_key (nXXXXXX)", "note_action": "like"},
    ],
}

PLATFORMS = list(MENU.keys())


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def _pick(prompt: str, items: list[str]) -> int:
    """番号選択。選んだ 0-indexed を返す。"""
    for i, item in enumerate(items, 1):
        print(f"  {i:2d}) {item}")
    while True:
        raw = input(f"{prompt}: ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(items):
                return idx
        except ValueError:
            pass
        print("     もう一度入力してください")


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


# ── メイン ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SNS 全プラットフォーム爆ツール")
    p.add_argument("--headless",    action="store_true", default=True)
    p.add_argument("--no-headless", dest="headless", action="store_false")
    p.add_argument("--config",      type=str, default="config.json")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg  = load_cfg(args.config)

    captcha_key = cfg["recaptcha"]["api_key"]
    proxies     = ProxyManager(cfg["proxy_list_file"])
    delay_range = cfg.get("delay_between_actions", [3, 8])
    headless    = args.headless

    print("\n" + "═" * 40)
    print("  SNS 爆ツール — プラットフォーム選択")
    print("═" * 40)
    plat_idx = _pick("番号", PLATFORMS)
    platform = PLATFORMS[plat_idx]

    actions = MENU[platform]
    print(f"\n── {platform} アクション ──")
    act_idx = _pick("番号", [a["label"] for a in actions])
    action  = actions[act_idx]

    hint   = action["hint"]
    target = input(f"ターゲット ({hint}): ").strip()
    if not target:
        print("ターゲットを入力してください"); sys.exit(1)

    text = ""
    if action.get("has_text"):
        text = input("テキスト (空欄でデフォルト): ").strip()

    count_raw = input("個数: ").strip()
    try:
        count = int(count_raw)
        if count <= 0:
            raise ValueError
    except ValueError:
        print("正の整数を入力してください"); sys.exit(1)

    print(f"\n{'═'*40}")
    print(f"  プラットフォーム : {platform}")
    print(f"  アクション       : {action['label']}")
    print(f"  ターゲット       : {target}")
    if text:
        print(f"  テキスト         : {text}")
    print(f"  個数             : {count}")
    print(f"  ヘッドレス       : {headless}")
    print(f"{'═'*40}\n")

    # ── note は専用ループ ──────────────────────────────────────────────────
    if platform == "note":
        note_action = action["note_action"]
        driver = make_driver(headless=headless, proxy=proxies.get_str())
        try:
            success, fail = note_bomb(
                driver          = driver,
                action          = note_action,
                target          = target,
                count           = count,
                proxy_dict      = proxies.get(),
                captcha_api_key = captcha_key,
                identity_fn     = generate_identity,
            )
        finally:
            driver.quit()
        print(f"\n── Done ── success={success}  fail={fail}  total={count}")
        return

    # ── その他プラットフォーム ─────────────────────────────────────────────
    fn       = action["fn"]
    text_key = action.get("text_key")  # コメント系の追加引数名

    success = 0
    fail    = 0

    for i in range(1, count + 1):
        proxy_str  = proxies.get_str()
        proxy_dict = proxies.get()
        identity   = generate_identity()

        print(f"── {i}/{count} ({platform} / {action['label']}) ──")
        driver = make_driver(headless=headless, proxy=proxy_str)

        try:
            kwargs = {}
            if text_key:
                kwargs[text_key] = text or "🔥"

            ok = fn(driver, target, identity, proxy_dict, captcha_key, **kwargs)

            if ok:
                success += 1
                print(f"  ✓ {i}/{count}")
            else:
                fail += 1
                print(f"  ✗ {i}/{count}")

        except Exception as e:
            print(f"  unhandled: {e}")
            fail += 1
        finally:
            driver.quit()

        if i < count:
            sleep = random.uniform(*delay_range)
            print(f"  sleeping {sleep:.1f}s …")
            time.sleep(sleep)

    print(f"\n── Done ── success={success}  fail={fail}  total={count}")


if __name__ == "__main__":
    main()
