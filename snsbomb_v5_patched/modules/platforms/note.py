"""
modules/platforms/note.py
note.com フォロ爆 / いいね爆

アクション:
  - follow  : target ユーザーをフォロー (POST /api/v2/followings/{urlname})
  - like    : target 記事にいいね     (POST /api/v3/likes)

フロー (登録):
  1. TempMail でメアド取得
  2. Selenium で note.com/signup → メール + パスワード + ユーザー名 入力
  3. メール認証リンクを踏む
  4. ログイン後 cookie から session_id を抽出
  5. requests セッションで API を叩く
  6. アカウント情報を note_accounts.json に追記

アカウント再利用:
  - note_accounts.json に保存済み cookie があれば登録をスキップして直接 API 呼び出し
  - 401/403 が返ったらそのアカウントをスキップして次へ (BAN 扱い)

note.com API (2025/10 確認):
  フォロー : POST https://note.com/api/v2/followings/{urlname}
             Header: Cookie, X-Note-Client-Version (任意), Referer
  いいね   : POST https://note.com/api/v3/likes
             JSON body: {"key": "<note_key>"}  ← 記事URLの /n/{key} 部分
  CSRF     : note.com は CSRF トークン不要 (SameSite Cookie のみ)
"""
from __future__ import annotations

import json
import re
import time
import random
import string
from pathlib import Path

import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from modules.email_provider import TempMail
from modules.captcha_solver import CaptchaSolver

# ── 保存先 ────────────────────────────────────────────────────────────────────
ACCOUNTS_FILE = Path("note_accounts.json")

# ── セレクタ (2025/10 現行 DOM) ───────────────────────────────────────────────
_S = {
    "email": [
        "input[name='email']",
        "input[type='email']",
        "//input[@placeholder='メールアドレス']",
        "//input[@placeholder='Email']",
    ],
    "password": [
        "input[name='password']",
        "input[type='password']",
        "//input[@placeholder='パスワード']",
    ],
    "nickname": [
        "input[name='nickname']",
        "input[name='name']",
        "//input[@placeholder='ニックネーム']",
        "//input[@placeholder='名前']",
    ],
    "urlname": [
        "input[name='urlname']",
        "//input[@placeholder='URLで使う名前']",
        "//input[contains(@placeholder,'urlname')]",
    ],
    "submit": [
        "button[type='submit']",
        "//button[contains(.,'登録') or contains(.,'新規登録') or contains(.,'作成')]",
        "//button[contains(.,'Sign up') or contains(.,'Register')]",
    ],
}

_LOGGED_IN_SELS = [
    "a[href='/notes/new']",
    "[data-testid='create-note-button']",
    "a[href*='/settings']",
    ".o-headerUserInfo",
]


# ── アカウント永続化 ──────────────────────────────────────────────────────────

def _load_accounts() -> list[dict]:
    if ACCOUNTS_FILE.exists():
        try:
            return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_account(account: dict) -> None:
    """note_accounts.json に1件追記 (重複 email はスキップ)"""
    accounts = _load_accounts()
    emails = {a["email"] for a in accounts}
    if account["email"] not in emails:
        accounts.append(account)
        ACCOUNTS_FILE.write_text(
            json.dumps(accounts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[note] 💾 saved account: {account['email']} → {ACCOUNTS_FILE}")


def _mark_banned(email: str) -> None:
    accounts = _load_accounts()
    for a in accounts:
        if a["email"] == email:
            a["banned"] = True
    ACCOUNTS_FILE.write_text(
        json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[note] 🚫 marked banned: {email}")


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def _sleep(lo=0.5, hi=1.2):
    time.sleep(random.uniform(lo, hi))


def _click_fb(driver, wait, selectors: list[str], timeout=10) -> bool:
    for sel in selectors:
        try:
            by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
            WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, sel))
            ).click()
            return True
        except Exception:
            continue
    return False


def _type_fb(driver, wait, selectors: list[str], text: str, timeout=10) -> bool:
    for sel in selectors:
        try:
            by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
            el = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, sel))
            )
            el.clear()
            for ch in text:
                el.send_keys(ch)
                time.sleep(random.uniform(0.04, 0.10))
            return True
        except Exception:
            continue
    return False


def _is_logged_in(driver) -> bool:
    for sel in _LOGGED_IN_SELS:
        try:
            driver.find_element(By.CSS_SELECTOR, sel)
            return True
        except Exception:
            pass
    return "note.com" in driver.current_url and "/login" not in driver.current_url and "/signup" not in driver.current_url


def _extract_cookies(driver) -> dict[str, str]:
    """Selenium driver から cookie dict を返す"""
    return {c["name"]: c["value"] for c in driver.get_cookies()}


def _make_session(cookies: dict, proxy_dict: dict | None = None) -> requests.Session:
    s = requests.Session()
    s.cookies.update(cookies)
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":    "https://note.com/",
        "Origin":     "https://note.com",
    })
    if proxy_dict:
        s.proxies.update(proxy_dict)
    return s


# ── 登録フロー ────────────────────────────────────────────────────────────────

def _register(driver, identity: dict, proxy_dict: dict | None,
              captcha_api_key: str) -> dict | None:
    """
    note.com で新規アカウント作成。
    成功したら {email, password, username, cookies} を返す。
    失敗したら None。
    """
    mail = TempMail(proxy=proxy_dict)
    identity["email"] = mail.email
    wait = WebDriverWait(driver, 25)

    try:
        # ── Step 1: サインアップページ ──────────────────────────────────
        driver.get("https://note.com/signup")
        _sleep(1.5, 2.5)

        # ── Step 2: メール入力 ──────────────────────────────────────────
        if not _type_fb(driver, wait, _S["email"], identity["email"]):
            # note は「メールで登録」ボタンが先に出るパターンがある
            for xp in [
                "//button[contains(.,'メールで登録')]",
                "//a[contains(.,'メールで登録')]",
                "//span[contains(.,'メールで登録')]",
            ]:
                try:
                    driver.find_element(By.XPATH, xp).click()
                    _sleep(0.5, 1.0)
                    break
                except Exception:
                    pass
            if not _type_fb(driver, wait, _S["email"], identity["email"]):
                print(f"[note] ✗ email field not found")
                return None

        # ── Step 3: パスワード ──────────────────────────────────────────
        if not _type_fb(driver, wait, _S["password"], identity["password"]):
            print(f"[note] ✗ password field not found")
            return None

        # ── Step 4: ニックネーム (出る場合) ────────────────────────────
        _type_fb(driver, wait, _S["nickname"], identity["full_name"], timeout=4)

        # ── Step 5: Submit ───────────────────────────────────────────────
        if not _click_fb(driver, wait, _S["submit"]):
            print(f"[note] ✗ submit button not found")
            return None

        _sleep(1.5, 2.5)

        # ── Step 6: キャプチャ ───────────────────────────────────────────
        CaptchaSolver(driver, api_key=captcha_api_key).solve()
        _sleep(0.5, 1.0)

        # ── Step 7: メール認証 (リンク or コード) ────────────────────────
        body = None
        for kw in ["note", "confirm", "verify", "認証", "メール"]:
            body = mail.wait_for_verification(keyword=kw, timeout=90, poll=2)
            if body:
                break

        if body:
            import re as _re
            url_m = _re.search(r'https?://note\.com/[^\s"\'<>]+', body)
            if url_m:
                driver.get(url_m.group(0).rstrip(".,)\"'"))
                _sleep(2.0, 3.5)
            else:
                # 6桁コード
                digit_m = _re.search(r'\b(\d{6})\b', body)
                if digit_m:
                    for sel in ["input[name='code']", "input[inputmode='numeric']",
                                "input[maxlength='6']"]:
                        try:
                            el = driver.find_element(By.CSS_SELECTOR, sel)
                            el.send_keys(digit_m.group(1))
                            _click_fb(driver, wait, _S["submit"], timeout=5)
                            _sleep(1.5, 2.5)
                            break
                        except Exception:
                            pass
        else:
            print(f"[note] ⚠ verification email not received — proceeding anyway")

        # ── Step 8: urlname (出る場合) ──────────────────────────────────
        slug = re.sub(r'[^a-z0-9]', '', identity["username"].lower())[:20] or \
               "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        _type_fb(driver, wait, _S["urlname"], slug, timeout=5)
        _click_fb(driver, wait, _S["submit"], timeout=5)
        _sleep(1.0, 2.0)

        # ── Step 9: ログイン確認 ────────────────────────────────────────
        logged_in = False
        for _ in range(12):
            if _is_logged_in(driver):
                logged_in = True
                break
            _sleep(2.5, 3.5)

        if not logged_in:
            print(f"[note] ✗ login confirm failed for {identity['email']}")
            return None

        print(f"[note] ✓ registered: {identity['email']}")
        cookies = _extract_cookies(driver)

        return {
            "email":    identity["email"],
            "password": identity["password"],
            "username": slug,
            "cookies":  cookies,
            "banned":   False,
        }

    except Exception as e:
        print(f"[note] ✗ register error: {e}")
        return None


# ── API アクション ────────────────────────────────────────────────────────────

def _api_follow(session: requests.Session, target_urlname: str) -> bool:
    """POST /api/v2/followings/{urlname}"""
    target = target_urlname.lstrip("@")
    r = session.post(
        f"https://note.com/api/v2/followings/{target}",
        timeout=15,
    )
    if r.status_code in (200, 201):
        print(f"[note] ✓ followed @{target}")
        return True
    elif r.status_code == 409:
        print(f"[note] already following @{target}")
        return True  # 冪等とみなす
    else:
        print(f"[note] ✗ follow failed: {r.status_code} {r.text[:120]}")
        return False


def _api_like(session: requests.Session, note_key: str) -> bool:
    """
    POST /api/v3/likes  body: {"key": "<note_key>"}
    note_key: 記事URLの /n/{key} 部分 (例: "abc123def")
    """
    r = session.post(
        "https://note.com/api/v3/likes",
        json={"key": note_key},
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    if r.status_code in (200, 201):
        print(f"[note] ✓ liked note key={note_key}")
        return True
    elif r.status_code == 409:
        print(f"[note] already liked note key={note_key}")
        return True
    else:
        print(f"[note] ✗ like failed: {r.status_code} {r.text[:120]}")
        return False


def _extract_note_key(target: str) -> str:
    """
    記事URL (https://note.com/user/n/nXXXXXX) or note_key そのままを返す。
    URL の場合は /n/{key} 部分を抽出。
    """
    m = re.search(r'/n/([A-Za-z0-9]+)', target)
    return m.group(1) if m else target.strip()


# ── 公開インターフェース ──────────────────────────────────────────────────────

def bomb(driver, action: str, target: str, count: int,
         proxy_dict: dict | None, captcha_api_key: str,
         identity_fn) -> tuple[int, int]:
    """
    note フォロ爆 / いいね爆のメインループ。

    Args:
        driver          : Selenium WebDriver (ループ外で quit しないこと)
        action          : "follow" | "like"
        target          : フォロー→ @urlname / いいね→ 記事URL or note_key
        count           : 実行回数
        proxy_dict      : requests 用 proxy dict
        captcha_api_key : 2captcha 等の API key
        identity_fn     : generate_identity を返す callable

    Returns:
        (success_count, fail_count)
    """
    if action not in ("follow", "like"):
        raise ValueError(f"action must be 'follow' or 'like', got '{action}'")

    note_key = _extract_note_key(target) if action == "like" else None
    accounts = _load_accounts()
    usable   = [a for a in accounts if not a.get("banned")]

    success = 0
    fail    = 0
    account_ptr = 0  # usable リストのポインタ

    for i in range(count):
        print(f"\n── note bomb {i+1}/{count} ({action}) ──")
        account = None

        # ── A: 既存アカウント優先 ─────────────────────────────────────
        while account_ptr < len(usable):
            cand = usable[account_ptr]
            account_ptr += 1
            if not cand.get("banned"):
                account = cand
                print(f"[note] reusing {account['email']}")
                break

        # ── B: 既存アカウントなし → 新規登録 ─────────────────────────
        if account is None:
            identity = identity_fn()
            new_acc  = _register(driver, identity, proxy_dict, captcha_api_key)
            if new_acc is None:
                print(f"[note] ✗ registration failed, skipping")
                fail += 1
                _sleep(3, 6)
                continue
            _save_account(new_acc)
            # 保存済みリストにも追加してポインタを次へ
            usable.append(new_acc)
            account_ptr = len(usable)  # 今追加したやつは次回以降に使う
            account = new_acc

        # ── C: API セッション構築 → アクション実行 ────────────────────
        session = _make_session(account["cookies"], proxy_dict)

        if action == "follow":
            ok = _api_follow(session, target)
        else:
            ok = _api_like(session, note_key)

        if ok:
            success += 1
        else:
            # 401/403 → BAN 扱い
            _mark_banned(account["email"])
            account["banned"] = True
            fail += 1

        _sleep(2.0, 5.0)

    return success, fail


# ── main.py から呼ぶ register_and_follow 互換ラッパー ────────────────────────
# main.py の PLATFORM_MAP が (driver, identity, target, proxy, captcha_key) を
# 期待するため、フォローアクションに限定した薄いラッパーを提供する。
# フル機能 (いいね / カウント指定) は note_bomb.py CLI を使うこと。

def register_and_follow(driver, identity: dict, target_username: str,
                        proxy_dict: dict | None, captcha_api_key: str) -> bool:
    account = _register(driver, identity, proxy_dict, captcha_api_key)
    if account is None:
        return False
    _save_account(account)
    session = _make_session(account["cookies"], proxy_dict)
    return _api_follow(session, target_username)


def register_and_like(driver, identity: dict, target: str,
                      proxy_dict: dict | None, captcha_api_key: str) -> bool:
    """note記事にいいね。target は記事URL または note_key (/n/{key}) どちらでも可。"""
    account = _register(driver, identity, proxy_dict, captcha_api_key)
    if account is None:
        return False
    _save_account(account)
    session = _make_session(account["cookies"], proxy_dict)
    note_key = _extract_note_key(target)
    return _api_like(session, note_key)
