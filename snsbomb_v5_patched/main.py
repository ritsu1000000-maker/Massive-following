"""
main.py
SNS フォロ爆ツール v2.3 — プラットフォーム拡張 (YouTube / TikTok / Facebook 追加)
Usage:
    python main.py                                       # config.json から読む
    python main.py --platform tiktok  --target user123 --count 20
    python main.py --platform youtube --target @mkbhd   --count 10
    python main.py --platform facebook --target nike    --count 15
    python main.py --platform twitter  --target elonmusk --count 20
    python main.py --stats                               # プール統計だけ表示
"""
import argparse
import json
import time
import random
import sys
from pathlib import Path

from modules.proxy_manager   import ProxyManager
from modules.account_gen     import generate_identity
from modules.browser         import make_driver
from modules.account_pool    import AccountPool
from modules.cookie_login    import restore_session, save_cookies

# ── Platform handlers ─────────────────────────────────────────────────────────
from modules.platforms import instagram, twitter
from modules.platforms import discord  as discord_mod
from modules.platforms import tiktok   as tiktok_mod
from modules.platforms import youtube  as youtube_mod
from modules.platforms import facebook as facebook_mod

# PLATFORM_MAP: platform key → register_and_follow(driver, identity, target, proxy, key)
PLATFORM_MAP = {
    "instagram": instagram.register_and_follow,
    "twitter":   twitter.register_and_follow,
    "x":         twitter.register_and_follow,
    "discord":   discord_mod.join_server,
    "tiktok":    tiktok_mod.register_and_follow,
    "youtube":   youtube_mod.register_and_follow,
    "yt":        youtube_mod.register_and_follow,
    "facebook":  facebook_mod.register_and_follow,
    "fb":        facebook_mod.register_and_follow,
}

# _do_follow 対応プラットフォーム (cookie再利用フロー)
_DO_FOLLOW_SUPPORTED = {"instagram", "twitter"}


def load_config(path: str = "config.json") -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SNS フォロ爆ツール v2.3")
    p.add_argument("--platform",    type=str)
    p.add_argument("--target",      type=str)
    p.add_argument("--invite-url",  type=str, dest="invite_url",
                   help="Discord 招待URL (--platform discord 時必須)")
    p.add_argument("--count",       type=int)
    p.add_argument("--headless",    action="store_true", default=None)
    p.add_argument("--no-headless", dest="headless", action="store_false")
    p.add_argument("--config",      type=str, default="config.json")
    p.add_argument("--stats",       action="store_true",
                   help="プール統計を表示して終了")
    return p.parse_args()


def _plat_key(platform: str) -> str:
    """PLATFORM_MAP 正規化キー (pool用)"""
    return {"x": "twitter", "yt": "youtube", "fb": "facebook"}.get(platform, platform)


# ── _do_follow: cookie再利用フロー (IG / Twitter のみ) ─────────────────────
def _do_follow(driver, platform: str, target: str, account: dict,
               captcha_key: str, proxy_dict: dict | None) -> str:  # 'ok'|'already'|'fail'
    import time, random
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    wait = WebDriverWait(driver, 15)

    try:
        if platform == "instagram":
            driver.get(f"https://www.instagram.com/{target}/")
            time.sleep(random.uniform(2, 4))
            _FOLLOW_SELS = [
                (By.CSS_SELECTOR, "button[aria-label='Follow']"),
                (By.XPATH,        "//button[.//div[text()='Follow']]"),
                (By.XPATH,        "//header//button[contains(.,'Follow') and not(contains(.,'Following'))]"),
            ]
            for by, sel in _FOLLOW_SELS:
                try:
                    btn = wait.until(EC.element_to_be_clickable((by, sel)))
                    lbl = (btn.get_attribute("aria-label") or btn.text or "").lower()
                    if "following" in lbl or "requested" in lbl:
                        print(f"[IG] already following {target}"); return "already"
                    btn.click()
                    print(f"[IG] ✓ {account['username']} followed {target}")
                    return "ok"
                except Exception:
                    continue
            print(f"[IG] follow button not found"); return "fail"

        elif platform == "twitter":
            driver.get(f"https://x.com/{target}")
            time.sleep(random.uniform(2, 4))
            btn = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "[data-testid='followButton']")
            ))
            lbl = (btn.get_attribute("aria-label") or "").lower()
            if "following" in lbl:
                print(f"[X] already following {target}"); return "already"
            btn.click()
            print(f"[X] ✓ {account['username']} followed {target}")
            return "ok"

        else:
            print(f"[follow] platform {platform} not supported in pool flow")
            return "fail"

    except TimeoutException:
        print(f"[follow] timeout"); return "fail"
    except Exception as e:
        print(f"[follow] error: {e}"); return "fail"


def _follow_with_pool(driver, pool: AccountPool, platform: str,
                      target: str, captcha_key: str,
                      proxy_str: str | None, proxy_dict: dict | None,
                      _depth: int = 0) -> bool:
    """
    pool対応プラットフォーム (IG/Twitter): cookie再利用 → フォロー。
    非対応プラットフォーム (TikTok/YouTube/Facebook): 毎回新規登録。
    """
    pk = _plat_key(platform)

    # ── 非対応プラットフォームは直接 handler 呼び出し ─────────────────────
    if pk not in _DO_FOLLOW_SUPPORTED:
        identity = generate_identity()
        handler  = PLATFORM_MAP.get(platform)
        if not handler:
            print(f"[main] unknown platform: {platform}"); return False
        ok = handler(
            driver=driver, identity=identity, target_username=target,
            proxy_dict=proxy_dict, captcha_api_key=captcha_key,
        )
        if ok:
            ck = save_cookies(driver)
            pool.add(pk, identity, cookies=ck)
        return ok

    # ── Pool 対応フロー (IG / Twitter) ────────────────────────────────────
    if _depth >= 5:
        identity = generate_identity()
        handler  = PLATFORM_MAP.get(platform)
        if not handler:
            print(f"[main] unknown platform: {platform}"); return False
        ok = handler(driver=driver, identity=identity, target_username=target,
                     proxy_dict=proxy_dict, captcha_api_key=captcha_key)
        if ok:
            ck = save_cookies(driver)
            pool.add(pk, identity, cookies=ck)
        return ok

    account = pool.get(pk)
    if account:
        print(f"[Pool] reusing {account['username']} (uses={account['uses']})")
        if restore_session(driver, pk, account):
            result = _do_follow(driver, pk, target, account, captcha_key, proxy_dict)
            if result == "ok":
                new_ck = save_cookies(driver)
                pool.mark_used(pk, account, new_cookies=new_ck)
                return True
            elif result == "already":
                pool.mark_used(pk, account, skip_increment=True)
                return _follow_with_pool(driver, pool, platform, target,
                                         captcha_key, proxy_str, proxy_dict,
                                         _depth=_depth + 1)
            else:
                pool.mark_banned(pk, account)
        else:
            print(f"[Pool] cookie expired for {account['username']}, re-registering")

    identity = generate_identity()
    handler  = PLATFORM_MAP.get(platform)
    if not handler:
        print(f"[main] unknown platform: {platform}"); return False
    ok = handler(driver=driver, identity=identity, target_username=target,
                 proxy_dict=proxy_dict, captcha_api_key=captcha_key)
    if ok:
        ck = save_cookies(driver)
        pool.add(pk, identity, cookies=ck)
    return ok


def main() -> None:
    args    = parse_args()
    cfg     = load_config(args.config)
    pool    = AccountPool()

    if args.stats:
        pool.print_stats()
        return

    platform    = (args.platform or cfg["target_platform"]).lower()
    count       = args.count    or cfg["accounts_to_create"]
    headless    = args.headless if args.headless is not None else cfg["headless"]
    captcha_key = cfg["recaptcha"]["api_key"]
    proxies     = ProxyManager(cfg["proxy_list_file"])
    delay_range = cfg.get("delay_between_actions", [3, 8])

    # ── Discord ────────────────────────────────────────────────────────────
    if platform == "discord":
        invite_url = (
            args.invite_url
            or cfg.get("platforms", {}).get("discord", {}).get("invite_url", "")
        )
        if not invite_url:
            print("[main] --invite-url が必要です (例: --invite-url https://discord.gg/xxxx)")
            sys.exit(1)

        print(f"\n[main] Platform  : {platform}")
        print(f"[main] Invite    : {invite_url}")
        print(f"[main] Count     : {count}")
        print(f"[main] Headless  : {headless}")

        success = fail = 0
        for i in range(1, count + 1):
            proxy_str  = proxies.get_str()
            proxy_dict = proxies.get()
            identity   = generate_identity()
            print(f"── Action {i}/{count} ──")
            driver = make_driver(headless=headless, proxy=proxy_str)
            try:
                ok = discord_mod.join_server(
                    driver=driver, invite_url=invite_url,
                    identity=identity, proxy_dict=proxy_dict,
                    captcha_api_key=captcha_key,
                )
                if ok:
                    success += 1
                    ck = save_cookies(driver)
                    pool.add("discord", identity, cookies=ck)
                else:
                    fail += 1
            except Exception as e:
                print(f"[main] unhandled: {e}"); fail += 1
            finally:
                driver.quit()
            time.sleep(random.uniform(*delay_range))

        print(f"\n── Done ── success={success}  fail={fail}  total={count}")
        pool.print_stats()
        return

    # ── フォロー系 (IG / X / TikTok / YouTube / Facebook) ─────────────────
    target = args.target or cfg.get("target_username", "")
    if not target:
        print("[main] --target が必要です")
        sys.exit(1)

    if platform not in PLATFORM_MAP:
        print(f"[main] 未対応プラットフォーム: {platform}")
        print(f"[main] 対応: {', '.join(PLATFORM_MAP.keys())}")
        sys.exit(1)

    pool.print_stats()

    print(f"\n[main] Platform  : {platform}")
    print(f"[main] Target    : {target}")
    print(f"[main] Count     : {count}")
    print(f"[main] Headless  : {headless}")

    success = fail = 0

    for i in range(1, count + 1):
        proxy_str  = proxies.get_str()
        proxy_dict = proxies.get()

        print(f"── Action {i}/{count} ──")
        driver = make_driver(headless=headless, proxy=proxy_str)
        try:
            ok = _follow_with_pool(
                driver=driver, pool=pool, platform=platform, target=target,
                captcha_key=captcha_key, proxy_str=proxy_str, proxy_dict=proxy_dict,
            )
            if ok: success += 1
            else:  fail += 1
        except Exception as e:
            print(f"[main] unhandled: {e}"); fail += 1
        finally:
            driver.quit()

        sleep = random.uniform(*delay_range)
        print(f"[main] sleeping {sleep:.1f}s …\n")
        time.sleep(sleep)

    print(f"\n── Done ── success={success}  fail={fail}  total={count}")
    pool.print_stats()


if __name__ == "__main__":
    main()
