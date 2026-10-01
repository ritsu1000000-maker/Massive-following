"""
modules/platforms/tiktok.py
TikTok フォロワー爆 — アカウント作成 + フォロー

フロー:
  1. TempMail でメアド取得
  2. tiktok.com/signup でアカウント作成 (DOB → email/pass → 認証コード)
  3. フォローボタン押下

Fix log (v1.0 — 2025/10):
  - actions._tt_login をラップして register_and_follow に統一
  - フォローボタン: data-e2e / aria-label / XPATH 3段フォールバック
  - ログイン確認: URL ベース (tiktok.com/foryou or home)
"""
from __future__ import annotations

import time
import random

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from modules.actions import _tt_login, _sleep


_FOLLOW_SELS = [
    (By.CSS_SELECTOR, "[data-e2e='follow-button']"),
    (By.CSS_SELECTOR, "[data-e2e='user-follow-button']"),
    (By.CSS_SELECTOR, "button[aria-label*='Follow']"),
    (By.CSS_SELECTOR, "button[aria-label*='フォロー']"),
    (By.XPATH,        "//div[@data-e2e='user-page']//button[contains(.,'Follow') or contains(.,'フォロー')]"),
    (By.XPATH,        "//button[contains(@style,'background') and (contains(.,'Follow') or contains(.,'フォロー'))]"),
]

_LOGGED_IN_URLS = ("tiktok.com/foryou", "tiktok.com/following", "tiktok.com/?", "tiktok.com/live")


def _is_logged_in(driver) -> bool:
    url = driver.current_url
    return any(p in url for p in _LOGGED_IN_URLS)


def register_and_follow(driver, identity: dict, target_username: str,
                        proxy_dict: dict | None, captcha_api_key: str) -> bool:
    """
    TikTok 新規登録 → target_username をフォロー。
    戻り値: True=成功 False=失敗
    """
    # ── Step 1: アカウント作成 ────────────────────────────────────────────
    if not _tt_login(driver, identity, proxy_dict, captcha_api_key):
        print(f"[TT] ✗ login/register failed for {identity.get('username')}")
        return False

    # ── Step 2: ログイン確認 (最大20s) ──────────────────────────────────
    logged_in = False
    for _ in range(10):
        if _is_logged_in(driver):
            logged_in = True
            break
        time.sleep(2)

    if not logged_in:
        # TikTok はリダイレクト遅延が長いので寛容に
        print(f"[TT] ⚠ login confirm timeout — attempting follow anyway")

    print(f"[TT] ✓ registered as {identity.get('username')}")

    # ── Step 3: フォロー ─────────────────────────────────────────────────
    target = target_username.lstrip("@")
    driver.get(f"https://www.tiktok.com/@{target}")
    _sleep(2.0, 3.5)

    wait = WebDriverWait(driver, 15)
    btn = None
    for by, sel in _FOLLOW_SELS:
        try:
            el = wait.until(EC.element_to_be_clickable((by, sel)))
            lbl = el.get_attribute("aria-label") or el.text or ""
            if "following" in lbl.lower() or "フォロー中" in lbl:
                print(f"[TT] already following @{target}")
                return True
            btn = el
            break
        except Exception:
            continue

    if not btn:
        print(f"[TT] ✗ follow button not found for @{target}")
        return False

    btn.click()
    print(f"[TT] ✓ {identity.get('username')} followed @{target}")
    _sleep(1.0, 1.8)
    return True
