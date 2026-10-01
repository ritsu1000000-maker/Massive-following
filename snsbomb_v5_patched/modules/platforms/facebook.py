"""
modules/platforms/facebook.py
Facebook ページフォロワー爆 — アカウント作成 + ページフォロー

フロー:
  1. actions._fb_login (Facebook アカウント作成) で認証
  2. ページURL / @handle にアクセス
  3. フォローボタン押下

Fix log (v1.0 — 2025/10):
  - actions._fb_login をラップして register_and_follow に統一
  - フォローボタン: data-testid / aria-label / XPATH 3段フォールバック
  - FB ページ / ユーザー / グループ の target 正規化
"""
from __future__ import annotations

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from modules.actions import _fb_login, _sleep


_FOLLOW_SELS = [
    (By.CSS_SELECTOR, "[data-testid='follow-button']"),
    (By.CSS_SELECTOR, "[aria-label='Follow']"),
    (By.CSS_SELECTOR, "[aria-label='フォロー']"),
    (By.XPATH,        "//div[@role='button'][contains(.,'Follow') and not(contains(.,'Following'))]"),
    (By.XPATH,        "//div[@role='button'][contains(.,'フォロー') and not(contains(.,'フォロー中'))]"),
    (By.XPATH,        "//span[normalize-space()='Follow']/../.."),
    (By.XPATH,        "//span[normalize-space()='フォロー']/../.."),
]

_LOGGED_IN_SELS = [
    (By.CSS_SELECTOR, "[aria-label='Facebook']"),
    (By.CSS_SELECTOR, "#facebook"),
    (By.CSS_SELECTOR, "[data-testid='royal_bar']"),
]


def _is_logged_in(driver) -> bool:
    for by, sel in _LOGGED_IN_SELS:
        try:
            driver.find_element(by, sel)
            return True
        except Exception:
            pass
    return "facebook.com" in driver.current_url and "/r.php" not in driver.current_url


def _normalize_target(target: str) -> str:
    """URL そのままか handle を facebook.com/handle に変換"""
    if target.startswith("http"):
        return target
    return f"https://www.facebook.com/{target.lstrip('@')}"


def register_and_follow(driver, identity: dict, target_username: str,
                        proxy_dict: dict | None, captcha_api_key: str) -> bool:
    """
    Facebook アカウント作成 → target ページをフォロー。
    戻り値: True=成功 False=失敗

    target_username: ページURL または @handle (例: "@nike" / "https://www.facebook.com/nike")
    """
    # ── Step 1: Facebook アカウント作成 ─────────────────────────────────
    if not _fb_login(driver, identity, proxy_dict, captcha_api_key):
        print(f"[FB] ✗ login/register failed for {identity.get('username')}")
        return False

    # ── Step 2: ログイン確認 (最大30s) ──────────────────────────────────
    logged_in = False
    for _ in range(10):
        if _is_logged_in(driver):
            logged_in = True
            break
        time.sleep(3)

    if not logged_in:
        print(f"[FB] ⚠ login confirm timeout — attempting follow anyway")

    print(f"[FB] ✓ registered as {identity.get('username')}")

    # ── Step 3: ページへ ─────────────────────────────────────────────────
    url = _normalize_target(target_username)
    driver.get(url)
    _sleep(2.0, 3.5)

    wait = WebDriverWait(driver, 15)
    btn = None
    for by, sel in _FOLLOW_SELS:
        try:
            el = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((by, sel)))
            txt = el.text or el.get_attribute("aria-label") or ""
            if "following" in txt.lower() or "フォロー中" in txt:
                print(f"[FB] already following {target_username}")
                return True
            btn = el
            break
        except Exception:
            continue

    if not btn:
        print(f"[FB] ✗ follow button not found for {target_username}")
        return False

    btn.click()
    print(f"[FB] ✓ {identity.get('username')} followed {target_username}")
    _sleep(1.0, 2.0)
    return True
