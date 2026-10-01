"""
modules/cookie_login.py
保存済みcookieでログイン状態を復元するユーティリティ。

使い方:
    from modules.cookie_login import restore_session, is_logged_in

    ok = restore_session(driver, platform, account)
    if ok:
        # ログイン済み → アクション実行
    else:
        # cookie切れ → 新規登録フローへ
"""
import time
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

# プラットフォームごとのベースURL & ログイン確認セレクタ
_PLATFORM_CFG = {
    "instagram": {
        "base_url": "https://www.instagram.com/",
        "check": [
            (By.CSS_SELECTOR, "svg[aria-label='Home']"),
            (By.CSS_SELECTOR, "[data-testid='user-avatar']"),
        ],
    },
    "twitter": {
        "base_url": "https://twitter.com/",
        "check": [
            (By.CSS_SELECTOR, "[data-testid='SideNav_AccountSwitcher_Button']"),
            (By.CSS_SELECTOR, "[data-testid='AppTabBar_Home_Link']"),
        ],
    },
    "tiktok": {
        "base_url": "https://www.tiktok.com/",
        "check": [
            (By.CSS_SELECTOR, "[data-e2e='profile-icon']"),
            (By.XPATH, "//button[contains(@class,'avatar')]"),
        ],
    },
    "youtube": {
        "base_url": "https://www.youtube.com/",
        "check": [
            (By.CSS_SELECTOR, "#avatar-btn"),
            (By.CSS_SELECTOR, "yt-img-shadow#avatar"),
        ],
    },
    "facebook": {
        "base_url": "https://www.facebook.com/",
        "check": [
            (By.CSS_SELECTOR, "[aria-label='Your profile']"),
            (By.CSS_SELECTOR, "[data-testid='blue_bar_profile_link']"),
        ],
    },
}


def restore_session(driver, platform: str, account: dict,
                    timeout: float = 12.0) -> bool:
    """
    保存済みcookieをドライバに注入してログイン状態を復元する。
    成功 (ログイン確認できた) → True
    失敗 (cookie切れ / セレクタ未検出) → False
    """
    platform = platform.lower().replace("x", "twitter")
    cfg = _PLATFORM_CFG.get(platform)
    if not cfg:
        print(f"[CookieLogin] unknown platform: {platform}")
        return False

    cookies = account.get("cookies", [])
    if not cookies:
        return False

    # cookie注入にはそのドメインに一度アクセスが必要
    driver.get(cfg["base_url"])
    time.sleep(1.5)

    driver.delete_all_cookies()
    for ck in cookies:
        # selenium が受け付けないキーを除去
        ck_clean = {k: v for k, v in ck.items()
                    if k in ("name", "value", "domain", "path",
                              "secure", "httpOnly", "expiry", "sameSite")}
        try:
            driver.add_cookie(ck_clean)
        except Exception:
            pass

    driver.refresh()
    time.sleep(2)

    # ログイン確認
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _check_logged_in(driver, cfg["check"]):
            print(f"[CookieLogin] ✓ {account.get('username')} session restored")
            return True
        time.sleep(1.5)

    print(f"[CookieLogin] ✗ session restore failed for {account.get('username')}")
    return False


def is_logged_in(driver, platform: str) -> bool:
    platform = platform.lower().replace("x", "twitter")
    cfg = _PLATFORM_CFG.get(platform, {})
    return _check_logged_in(driver, cfg.get("check", []))


def save_cookies(driver) -> list:
    """現在のドライバのcookieを返す (pool.update_cookies / add に渡す)。"""
    return driver.get_cookies()


def _check_logged_in(driver, selectors: list) -> bool:
    for by, sel in selectors:
        try:
            driver.find_element(by, sel)
            return True
        except NoSuchElementException:
            pass
    return False
