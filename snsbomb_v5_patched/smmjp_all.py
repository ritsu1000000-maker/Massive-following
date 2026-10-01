"""
smmjp_all.py
smmjp.com 全サービス自動注文ツール

Usage:
    # 全サービスをInstagramアカウントに注文 (マスオーダー)
    python smmjp_all.py --email you@example.com --password PASS \
        --url https://www.instagram.com/TARGET_USERNAME/

    # プラットフォーム絞り込み
    python smmjp_all.py --email E --password P \
        --url https://x.com/TARGET \
        --platforms twitter

    # 1件ずつ注文 (マスオーダーが動かない場合)
    python smmjp_all.py --email E --password P \
        --url https://www.instagram.com/TARGET/ \
        --no-mass

    # ドライラン (実際には注文しない)
    python smmjp_all.py --email E --password P \
        --url https://... --dry-run

    # 特定IDのみスキップ
    python smmjp_all.py ... --skip-ids 3215,1731

    # 最小注文数の2倍で注文
    python smmjp_all.py ... --qty-ratio 2.0
"""
import argparse
import json
import sys
import time
import random
from pathlib import Path

from modules.browser     import make_driver
from modules.smmjp_order import SmmjpOrderEngine


SERVICES_FILE = Path("data/services_clean.json")


def load_services() -> list[dict]:
    if not SERVICES_FILE.exists():
        print(f"[main] {SERVICES_FILE} が見つかりません")
        print("       Services.html からパースして生成してください:")
        print("       python parse_services.py Services.html")
        sys.exit(1)
    return json.loads(SERVICES_FILE.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="smmjp.com 全サービス自動注文")
    p.add_argument("--email",       required=True,  help="smmjp.com ログインメール")
    p.add_argument("--password",    required=True,  help="smmjp.com パスワード")
    p.add_argument("--url",         required=True,  help="注文対象URL (例: https://instagram.com/xxx/)")
    p.add_argument("--platforms",   nargs="+",      default=None,
                   help="絞り込むプラットフォーム (例: instagram twitter tiktok)")
    p.add_argument("--categories",  nargs="+",      default=None,
                   help="カテゴリ名の部分一致で絞り込み (例: フォロワー いいね)")
    p.add_argument("--skip-ids",    default="",
                   help="スキップするサービスIDをカンマ区切りで (例: 3215,1731)")
    p.add_argument("--qty-ratio",   type=float, default=1.0,
                   help="最小注文数に対する倍率 (デフォルト: 1.0 = min)")
    p.add_argument("--mass",        action="store_true",  default=True,
                   help="マスオーダー使用 (デフォルト: True)")
    p.add_argument("--no-mass",     dest="mass", action="store_false",
                   help="1件ずつ注文 (マスオーダーが効かない場合)")
    p.add_argument("--mass-chunk",  type=int, default=100,
                   help="マスオーダーの1回あたり件数 (デフォルト: 100)")
    p.add_argument("--delay",       type=float, nargs=2, default=[2.0, 5.0],
                   metavar=("MIN", "MAX"), help="注文間スリープ秒 (デフォルト: 2 5)")
    p.add_argument("--headless",    action="store_true",  default=True)
    p.add_argument("--no-headless", dest="headless", action="store_false")
    p.add_argument("--cookie-file", default="data/smmjp_cookies.json")
    p.add_argument("--dry-run",     action="store_true",
                   help="ブラウザ操作なし — 注文予定リストを表示するだけ")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # スキップID解析
    skip_ids: set[int] = set()
    if args.skip_ids:
        for part in args.skip_ids.split(","):
            part = part.strip()
            if part.isdigit():
                skip_ids.add(int(part))

    services = load_services()

    print(f"\n[main] smmjp 全サービス注文ツール")
    print(f"[main] Target URL : {args.url}")
    print(f"[main] Services   : {len(services)}件")
    print(f"[main] Platforms  : {args.platforms or '全部'}")
    print(f"[main] Categories : {args.categories or '全部'}")
    print(f"[main] qty_ratio  : {args.qty_ratio}")
    print(f"[main] mass_order : {args.mass} (chunk={args.mass_chunk})")
    print(f"[main] dry_run    : {args.dry_run}")
    print(f"[main] skip_ids   : {skip_ids or 'なし'}")
    print()

    if args.dry_run:
        # ドライランはブラウザ不要
        dummy_engine = SmmjpOrderEngine.__new__(SmmjpOrderEngine)
        dummy_engine._logged_in = True
        dummy_engine.driver     = None
        result = dummy_engine.order_all(
            services        = services,
            target_url      = args.url,
            qty_ratio       = args.qty_ratio,
            use_mass        = args.mass,
            mass_chunk      = args.mass_chunk,
            delay_range     = tuple(args.delay),
            skip_ids        = skip_ids,
            platform_filter = args.platforms,
            category_filter = args.categories,
            dry_run         = True,
        )
        # JSON出力
        out = Path("data/order_plan.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(result.get("orders", []), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n[main] 注文計画を {out} に保存しました")
        return

    # ── 実行 ─────────────────────────────────────────────────────────────────
    driver = make_driver(headless=args.headless)
    try:
        engine = SmmjpOrderEngine(
            driver      = driver,
            email       = args.email,
            password    = args.password,
            cookie_file = args.cookie_file,
            headless    = args.headless,
        )

        if not engine.login():
            print("[main] ✗ ログイン失敗 — 終了")
            return

        result = engine.order_all(
            services        = services,
            target_url      = args.url,
            qty_ratio       = args.qty_ratio,
            use_mass        = args.mass,
            mass_chunk      = args.mass_chunk,
            delay_range     = tuple(args.delay),
            skip_ids        = skip_ids,
            platform_filter = args.platforms,
            category_filter = args.categories,
            dry_run         = False,
        )

        print(f"\n── 完了 ──")
        print(f"  成功: {result['success']}")
        print(f"  失敗: {result['fail']}")
        print(f"  合計: {result['total']}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
