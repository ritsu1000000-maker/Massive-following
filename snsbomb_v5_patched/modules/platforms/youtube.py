"""
modules/platforms/youtube.py
YouTube チャンネル登録者爆 — アカウント作成 + チャンネル登録

フロー:
  1. actions._yt_login (Google アカウント作成) で認証
  2. チャンネルURL / @handle にアクセス
  3. チャンネル登録ボタン押下

Fix log (v1.0 — 2025/10):
  - actions._yt_login をラップして register_and_follow に統一
  - チャンネル登録ボタン: yt-button-shape / aria-label / 旧UI の3段フォールバック
  - @handle 正規化 (@ prefix 付与)
"""
from __future__ import annotations

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from modules.actions import _yt_login, _sleep


_SUBSCRIBE_SELS = [
    # 2025現行 UI — yt-button-shape ラッパー
    (By.CSS_SELECTOR, "ytd-subscribe-button-renderer yt-button-shape button"),
    (By.CSS_SELECTOR, "#subscribe-button yt-button-shape button"),
    # aria-label
    (By.CSS_SELECTOR, "button[aria-label*='Subscribe']"),
    (By.CSS_SELECTOR, "button[aria-label*='チャンネル登録']"),
    # 旧 UI
    (By.CSS_SELECTOR, "ytd-subscribe-button-renderer button"),
    (By.CSS_SELECTOR, "#subscribe-button tp-yt-paper-button"),
    # テキスト XPATH フォールバック
    (By.XPATH, "//yt-button-shape//button[contains(.,'登録') or contains(.,'Subscribe')]"),
]

_LOGGED_IN_SELS = [
    (By.CSS_SELECTOR, "button[aria-label='Search']"),  # サイドバーに出る
    (By.CSS_SELECTOR, "#avatar-btn"),
    (By.CSS_SELECTOR, "yt-img-shadow#avatar"),
]


def _is_logged_in(driver) -> bool:
    for by, sel in _LOGGED_IN_SELS:
        try:
            driver.find_element(by, sel)
            return True
        except Exception:
            pass
    return False


def _normalize_target(target: str) -> str:
    """URL そのままか @handle を youtube.com/@handle URL に変換"""
    if target.startswith("http"):
        return target
    handle = target.lstrip("@")
    return f"https://www.youtube.com/@{handle}"


def register_and_follow(driver, identity: dict, target_username: str,
                        proxy_dict: dict | None, captcha_api_key: str) -> bool:
    """
    Google アカウント作成 → target チャンネルを登録。
    戻り値: True=成功 False=失敗

    target_username: チャンネルURL または @handle (例: "@mkbhd" / "UCBcRF18a7Qf58cCRy5xuWwQ")
    """
    # ── Step 1: Google アカウント作成 / ログイン ─────────────────────────
    if not _yt_login(driver, identity, proxy_dict, captcha_api_key):
        print(f"[YT] ✗ login/register failed for {identity.get('username')}")
        return False

    # ── Step 2: ログイン確認 (最大30s) ──────────────────────────────────
    logged_in = False
    for _ in range(10):
        if _is_logged_in(driver):
            logged_in = True
            break
        time.sleep(3)

    if not logged_in:
        print(f"[YT] ⚠ login confirm timeout — attempting subscribe anyway")

    print(f"[YT] ✓ registered as {identity.get('username')}")

    # ── Step 3: チャンネルページへ ──────────────────────────────────────
    url = _normalize_target(target_username)
    driver.get(url)
    _sleep(2.0, 3.5)

    wait = WebDriverWait(driver, 15)
    btn = None
    for by, sel in _SUBSCRIBE_SELS:
        try:
            el = wait.until(EC.element_to_be_clickable((by, sel)))
            lbl = el.get_attribute("aria-label") or el.text or ""
            if "登録済み" in lbl or "subscribed" in lbl.lower():
                print(f"[YT] already subscribed to {target_username}")
                return True
            btn = el
            break
        except Exception:
            continue

    if not btn:
        print(f"[YT] ✗ subscribe button not found for {target_username}")
        return False

    btn.click()
    print(f"[YT] ✓ {identity.get('username')} subscribed to {target_username}")
    _sleep(1.0, 2.0)
    return True
