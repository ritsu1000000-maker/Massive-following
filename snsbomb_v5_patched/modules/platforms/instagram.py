"""
modules/platforms/instagram.py
Instagram account creation + follow flow.

Fix log (v2.1):
  - フォローボタンセレクタを3種フォールバックに変更 (2024+ DOM対応)
  - 登録完了確認ループ追加 (ログイン状態チェック後にフォロー)
  - クッキーバナーの自動dismiss
  - ホームページリダイレクト後の待機を延長
"""
import time
import random
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from modules.captcha_solver import CaptchaSolver
from modules.email_provider import TempMail


# ── フォローボタン候補セレクタ (優先順) ──────────────────────────────────────
_FOLLOW_SELECTORS = [
    # 2025現行UI: 青いフォローボタン
    (By.CSS_SELECTOR, "button[aria-label='Follow']"),
    (By.CSS_SELECTOR, "button[aria-label='フォローする']"),
    (By.CSS_SELECTOR, "button[data-testid='follow-button']"),
    # ヘッダー内テキストマッチ
    (By.XPATH, "//header//button[normalize-space()='Follow']"),
    (By.XPATH, "//header//button[normalize-space()='フォロー']"),
    # section内 (スマホレイアウト)
    (By.XPATH, "//section//button[not(contains(.,'Following')) and not(contains(.,'フォロー中'))][contains(.,'Follow') or contains(.,'フォロー')]"),
    # 旧パターン
    (By.XPATH, "//button[.//div[text()='Follow']]"),
    (By.XPATH, "//header//button[contains(.,'Follow') and not(contains(.,'Following'))]"),
]

# ── ログイン確認セレクタ ──────────────────────────────────────────────────────
_LOGGED_IN_SELECTORS = [
    (By.CSS_SELECTOR, "svg[aria-label='Home']"),
    (By.CSS_SELECTOR, "[data-testid='user-avatar']"),
    (By.CSS_SELECTOR, "nav a[href='/']"),
]


def _is_logged_in(driver) -> bool:
    for by, sel in _LOGGED_IN_SELECTORS:
        try:
            driver.find_element(by, sel)
            return True
        except NoSuchElementException:
            pass
    # フォールバック: URLがホームかどうか
    return "instagram.com" in driver.current_url and "/accounts/" not in driver.current_url


def _dismiss_cookie_banner(driver) -> None:
    """クッキーバナーがあれば 'Allow all cookies' を押す"""
    try:
        btn = driver.find_element(By.XPATH, "//button[contains(text(),'Allow') or contains(text(),'Accept')]")
        btn.click()
        time.sleep(0.5)
    except Exception:
        pass


def _find_follow_button(driver, wait: WebDriverWait):
    """複数セレクタを順番に試してフォローボタンを返す。見つからなければ None。"""
    for by, sel in _FOLLOW_SELECTORS:
        try:
            btn = wait.until(EC.element_to_be_clickable((by, sel)))
            # "Following" / "Requested" ボタンを誤クリックしない
            label = btn.get_attribute("aria-label") or btn.text or ""
            if "following" in label.lower() or "requested" in label.lower():
                continue
            return btn
        except (TimeoutException, NoSuchElementException):
            continue
    return None


def register_and_follow(driver, identity: dict, target_username: str,
                        proxy_dict: dict | None, captcha_api_key: str) -> bool:
    """
    Full flow: open signup → fill form → solve captcha → verify email → follow target.
    Returns True on success.
    """
    mail = TempMail(proxy=proxy_dict)
    identity["email"] = mail.email
    wait = WebDriverWait(driver, 25)

    try:
        # ── Step 1: Open signup ───────────────────────────────────────────────
        driver.get("https://www.instagram.com/accounts/emailsignup/")
        time.sleep(random.uniform(2, 4))
        _dismiss_cookie_banner(driver)

        # ── Step 2: Fill form ─────────────────────────────────────────────────
        _type(driver, wait, "input[name='emailOrPhone']", identity["email"])
        _type(driver, wait, "input[name='fullName']",    identity["full_name"])
        _type(driver, wait, "input[name='username']",    identity["username"])
        _type(driver, wait, "input[name='password']",    identity["password"])

        wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "button[type='submit']")
        )).click()
        time.sleep(random.uniform(1.5, 3))

        # ── Step 3: Birthday ──────────────────────────────────────────────────
        dob = identity["dob"].split("-")  # YYYY-MM-DD
        _select_dob(driver, wait, dob[1], dob[2], dob[0])

        wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "button[type='submit']")
        )).click()
        time.sleep(random.uniform(2, 4))

        # ── Step 4: Captcha if present ────────────────────────────────────────
        CaptchaSolver(driver, api_key=captcha_api_key).solve()
        time.sleep(random.uniform(1, 2))

        # ── Step 5: Email verification ────────────────────────────────────────
        verify_url = mail.wait_for_verification(keyword="verify", timeout=120)
        if verify_url:
            driver.get(verify_url)
            time.sleep(random.uniform(3, 6))
        else:
            print(f"[IG] No verify URL found for {identity['email']} — trying code field")

        # ── Step 6: ログイン確認 (最大30s) ──────────────────────────────────
        logged_in = False
        for _ in range(10):
            if _is_logged_in(driver):
                logged_in = True
                break
            time.sleep(3)

        if not logged_in:
            print(f"[IG] ✗ Login state not confirmed for {identity['username']}")
            return False

        print(f"[IG] ✓ Logged in as {identity['username']}")

        # ── Step 7: Follow target ─────────────────────────────────────────────
        driver.get(f"https://www.instagram.com/{target_username}/")
        time.sleep(random.uniform(3, 5))
        _dismiss_cookie_banner(driver)

        follow_btn = _find_follow_button(driver, WebDriverWait(driver, 15))
        if not follow_btn:
            print(f"[IG] ✗ Follow button not found for @{target_username}")
            return False

        follow_btn.click()
        print(f"[IG] ✓ {identity['username']} followed {target_username}")
        time.sleep(random.uniform(2, 4))
        return True

    except TimeoutException as e:
        print(f"[IG] Timeout: {e}")
        return False
    except Exception as e:
        print(f"[IG] Error: {e}")
        return False


def _type(driver, wait, selector: str, text: str) -> None:
    el = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
    el.clear()
    for char in text:
        el.send_keys(char)
        time.sleep(random.uniform(0.04, 0.12))


def _select_dob(driver, wait, month: str, day: str, year: str) -> None:
    from selenium.webdriver.support.ui import Select
    try:
        Select(driver.find_element(By.CSS_SELECTOR, "select[title='Month']")).select_by_value(str(int(month)))
        Select(driver.find_element(By.CSS_SELECTOR, "select[title='Day']")).select_by_value(str(int(day)))
        Select(driver.find_element(By.CSS_SELECTOR, "select[title='Year']")).select_by_value(year)
    except Exception as e:
        print(f"[IG/DOB] {e}")
