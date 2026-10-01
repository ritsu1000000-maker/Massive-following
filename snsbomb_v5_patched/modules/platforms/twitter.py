"""
modules/platforms/twitter.py
X (Twitter) account creation + follow.

Fix log (v2.2 — 2025/09):
  - x.com への正式移行 (twitter.com はリダイレクトのみ)
  - Nextボタン: span/div どちらのラップでも取れる多段セレクタに変更
  - name入力: JS setValue → send_keys に変更 (React onKeyDown 検出対策)
  - _page_ready() 追加: React初期化完了まで待ってからフォーム操作
  - email確認コード入力: wait_for_verification の keyword を "confirm","verify" 両方試行
  - フォローURL: x.com ベースに統一
  - ログイン確認セレクタ追加
  - スクリーンショット保存 (data/ ディレクトリ、デバッグ用)
"""
import re
import time
import random
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from modules.captcha_solver import CaptchaSolver, CastleioSolver, FunCaptchaSolver
from modules.email_provider import TempMail


# ── ログイン確認セレクタ ──────────────────────────────────────────────────────
_LOGGED_IN_SELECTORS = [
    (By.CSS_SELECTOR, "[data-testid='SideNav_AccountSwitcher_Button']"),
    (By.CSS_SELECTOR, "[data-testid='AppTabBar_Home_Link']"),
    (By.CSS_SELECTOR, "[aria-label='Home']"),
    (By.CSS_SELECTOR, "[data-testid='primaryColumn']"),
    (By.CSS_SELECTOR, "nav[aria-label='Primary']"),
]

# ── Nextボタン候補 (span / div どちらでもヒット) ──────────────────────────────
_NEXT_SELS = [
    (By.XPATH,        "//span[normalize-space()='Next']/.."),
    (By.XPATH,        "//div[normalize-space()='Next']"),
    (By.XPATH,        "//span[normalize-space()='Next']"),
    (By.XPATH,        "//button[@role='button'][.//span[normalize-space()='Next']]"),
    (By.CSS_SELECTOR, "[data-testid='ocfEnterTextNextButton']"),
    (By.CSS_SELECTOR, "button[type='button']:last-of-type"),  # フォールバック
]

# ── 名前フィールド候補 ────────────────────────────────────────────────────────
_NAME_SELS = [
    "input[name='name']",
    "input[autocomplete='name']",
    "//input[@name='name']",
    "//input[@placeholder='Name']",
    "//input[@placeholder='名前']",
]

# ── メールフィールド候補 ──────────────────────────────────────────────────────
_EMAIL_SELS = [
    "input[name='email']",
    "input[type='email']",
    "//input[@name='email']",
    "//input[@placeholder='Email']",
    "//input[@placeholder='メール']",
]

# ── パスワードフィールド候補 ──────────────────────────────────────────────────
_PASS_SELS = [
    "input[name='password']",
    "input[type='password']",
    "//input[@name='password']",
]

# ── 確認コードフィールド候補 ──────────────────────────────────────────────────
_CODE_SELS = [
    "input[data-testid='ocfEnterTextTextInput']",
    "input[name='verfication_code']",
    "input[name='code']",
    "input[inputmode='numeric']",
    "input[maxlength='6']",
    "//input[@autocomplete='one-time-code']",
]


def _is_logged_in(driver) -> bool:
    for by, sel in _LOGGED_IN_SELECTORS:
        try:
            driver.find_element(by, sel)
            return True
        except NoSuchElementException:
            pass
    return False


def _dismiss_layers(driver) -> None:
    """クッキー同意 / マイグレーションバナーを消す"""
    for sel in [
        "//span[normalize-space()='Accept all cookies']/..",
        "//span[normalize-space()='Refuse non-essential cookies']/..",
        "[data-testid='xMigrationBottomBar'] button",
        "//button[normalize-space()='Accept all cookies']",
        "//button[normalize-space()='すべてのCookieを許可する']",
    ]:
        try:
            by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
            driver.find_element(by, sel).click()
            time.sleep(0.5)
        except Exception:
            pass


def _ss(driver, tag: str, username: str) -> None:
    """デバッグ用スクリーンショット保存"""
    try:
        Path("data").mkdir(exist_ok=True)
        driver.save_screenshot(f"data/x_{tag}_{username[:8]}.png")
    except Exception:
        pass


def _page_ready(driver, timeout: int = 20) -> bool:
    """React初期化完了まで待つ (name input が出るまで)"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in _NAME_SELS:
            try:
                by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
                WebDriverWait(driver, 2).until(
                    EC.presence_of_element_located((by, sel))
                )
                return True
            except Exception:
                continue
        time.sleep(0.5)
    return False


def _click_next(driver, timeout: int = 10) -> bool:
    """Nextボタンを多段セレクタでクリック"""
    for by, sel in _NEXT_SELS:
        try:
            btn = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, sel))
            )
            btn.click()
            return True
        except Exception:
            continue
    return False


def _type_fb(driver, selectors, text: str, slow: bool = False) -> bool:
    """セレクタ候補リストを順に試してsend_keys入力 (JS setValue は X では認識しないことがある)"""
    for sel in selectors:
        try:
            by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
            el = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((by, sel)))
            el.click()
            # フィールドをクリア
            el.send_keys(Keys.CONTROL, "a")
            el.send_keys(Keys.DELETE)
            time.sleep(0.1)
            if slow:
                for ch in text:
                    el.send_keys(ch)
                    time.sleep(random.uniform(0.05, 0.12))
            else:
                for ch in text:
                    el.send_keys(ch)
                    time.sleep(random.uniform(0.03, 0.07))
            time.sleep(random.uniform(0.15, 0.3))
            return True
        except Exception:
            continue
    return False


def _wait_for_any(driver, selectors, timeout: int = 10) -> bool:
    for sel in selectors:
        try:
            by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, sel))
            )
            return True
        except Exception:
            continue
    return False


def register_and_follow(driver, identity: dict, target_username: str,
                        proxy_dict: dict | None, captcha_api_key: str) -> bool:
    mail = TempMail(proxy=proxy_dict)
    identity["email"] = mail.email
    uname = identity["username"]

    castle = CastleioSolver(driver, proxy=proxy_dict)

    try:
        # ── Step 1: サインアップページ (x.com に直接) ─────────────────────
        driver.get("https://x.com/i/flow/signup")

        # Castle.io を最速で偽装 (ページ読み込み直後 = 最も効果的なタイミング)
        castle_token = castle.wait_and_inject(timeout=6)

        _page_ready(driver, timeout=25)
        _dismiss_layers(driver)
        _ss(driver, "01_signup", uname)

        # ── Step 2: 名前入力 ───────────────────────────────────────────────
        if not _type_fb(driver, _NAME_SELS, identity["full_name"]):
            print(f"[X] ✗ name field not found")
            _ss(driver, "ERR_name", uname)
            return False

        if not _click_next(driver):
            print(f"[X] ✗ Next button (after name) not found")
            _ss(driver, "ERR_next1", uname)
            return False
        time.sleep(random.uniform(1.0, 2.0))
        _ss(driver, "02_name_done", uname)

        # ── Step 3: メール/電話選択 ────────────────────────────────────────
        # 電話タブが出てたらメールに切り替え
        for xp in [
            "//span[normalize-space()='Use email instead']",
            "//span[normalize-space()='代わりにメールアドレスを使用']",
            "//a[contains(.,'email')]",
        ]:
            try:
                driver.find_element(By.XPATH, xp).click()
                time.sleep(0.5)
                break
            except Exception:
                pass

        if not _type_fb(driver, _EMAIL_SELS, identity["email"]):
            print(f"[X] ✗ email field not found")
            _ss(driver, "ERR_email", uname)
            return False

        if not _click_next(driver):
            print(f"[X] ✗ Next button (after email) not found")
            _ss(driver, "ERR_next2", uname)
            return False
        time.sleep(random.uniform(1.0, 2.0))
        _ss(driver, "03_email_done", uname)

        # ── Step 4: 生年月日 ───────────────────────────────────────────────
        # Castle.io は DOB送信前に再スコアリングするため再注入
        castle.inject_fake_castle()

        dob = identity["dob"].split("-")   # ["YYYY", "MM", "DD"]
        _fill_dob_x(driver, dob[1], dob[2], dob[0])
        if not _click_next(driver):
            print(f"[X] ✗ Next button (after DOB) not found")
            _ss(driver, "ERR_next3", uname)
            return False
        time.sleep(random.uniform(1.5, 2.5))
        _ss(driver, "04_dob_done", uname)

        # ── Step 5: 確認コード or メールリンク ────────────────────────────
        _handle_verification(driver, mail, uname)
        _ss(driver, "05_verify", uname)

        # ── Step 6: パスワード入力 ─────────────────────────────────────────
        if not _wait_for_any(driver, _PASS_SELS, timeout=15):
            print(f"[X] ✗ password field not found")
            _ss(driver, "ERR_pass", uname)
            return False

        _type_fb(driver, _PASS_SELS, identity["password"])
        if not _click_next(driver):
            print(f"[X] ✗ Next button (after password) not found")
            _ss(driver, "ERR_next4", uname)
            return False
        time.sleep(random.uniform(2.0, 3.5))
        _ss(driver, "06_pass_done", uname)

        # ── Step 7: ユーザー名確認画面 (出ることがある) ─────────────────
        _handle_username_screen(driver, identity, uname)
        _ss(driver, "07_uname", uname)

        # ── Step 8: キャプチャ (reCAPTCHA → FunCaptcha → Castle.io 順) ───
        # Castle.io をパスワード送信前に再注入
        castle.inject_fake_castle()

        # reCAPTCHA / hCaptcha
        CaptchaSolver(driver, api_key=captcha_api_key).solve()

        # FunCaptcha (Arkose Labs) — 外部APIキーがあれば使う
        _captcha_cfg = _load_captcha_cfg()
        funcap = FunCaptchaSolver(
            driver,
            api_key = _captcha_cfg.get("captcha_key", captcha_api_key),
            service = _captcha_cfg.get("captcha_service", "2captcha"),
            proxy   = proxy_dict,
        )
        funcap.solve()

        time.sleep(random.uniform(2.0, 3.0))
        _ss(driver, "08_captcha", uname)

        # ── Step 9: ログイン確認 (最大36秒) ──────────────────────────────
        logged_in = False
        for _ in range(12):
            if _is_logged_in(driver):
                logged_in = True
                break
            # 残ってるフローがあれば Next を押し続ける
            _drain_remaining_flow(driver)
            time.sleep(3)

        if not logged_in:
            print(f"[X] ✗ Login state not confirmed for {uname}")
            _ss(driver, "ERR_login", uname)
            return False

        print(f"[X] ✓ Logged in as {uname}")
        _ss(driver, "09_loggedin", uname)

        # ── Step 10: フォロー ──────────────────────────────────────────────
        target = target_username.lstrip("@")
        driver.get(f"https://x.com/{target}")
        time.sleep(random.uniform(3, 5))
        _dismiss_layers(driver)
        _ss(driver, "10_profile", uname)

        follow_btn = None
        for by, sel in [
            (By.CSS_SELECTOR, "[data-testid='followButton']"),
            (By.XPATH,        "//div[@data-testid='followButton']"),
            (By.XPATH,        "//span[normalize-space()='Follow']/ancestor::div[@role='button']"),
        ]:
            try:
                follow_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((by, sel))
                )
                break
            except Exception:
                continue

        if not follow_btn:
            print(f"[X] ✗ Follow button not found for @{target}")
            _ss(driver, "ERR_follow", uname)
            return False

        label = follow_btn.get_attribute("aria-label") or ""
        if "following" in label.lower():
            print(f"[X] Already following {target}")
            return True

        follow_btn.click()
        print(f"[X] ✓ {uname} followed {target}")
        _ss(driver, "11_followed", uname)
        return True

    except TimeoutException as e:
        print(f"[X] Timeout: {e}")
        _ss(driver, "ERR_timeout", identity.get("username", "unknown"))
        return False
    except Exception as e:
        print(f"[X] Error: {type(e).__name__}: {e}")
        _ss(driver, "ERR", identity.get("username", "unknown"))
        return False


# ── 確認コード / メールリンク処理 ────────────────────────────────────────────

def _handle_verification(driver, mail, uname: str) -> None:
    """確認コードフィールドがあればメールから取得して入力"""
    code_field = None
    for sel in _CODE_SELS:
        try:
            by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
            code_field = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((by, sel))
            )
            break
        except Exception:
            continue

    if not code_field:
        return  # コード画面なし → スキップ

    # メールから確認情報を取得 ("confirm" → "verify" フォールバック)
    body = None
    for kw in ["confirm", "verify", "code", "認証"]:
        body = mail.wait_for_verification(keyword=kw, timeout=60, poll=1.5)
        if body:
            break

    if not body:
        print(f"[X] ✗ verification email not received for {uname}")
        return

    # 6桁コード優先
    text = re.sub(r'<[^>]+>', ' ', body)
    m = re.search(r'\b(\d{6})\b', text)
    if m:
        code = m.group(1)
        print(f"[X] 確認コード: {code}")
        code_field.click()
        code_field.send_keys(Keys.CONTROL, "a")
        code_field.send_keys(Keys.DELETE)
        for ch in code:
            code_field.send_keys(ch)
            time.sleep(random.uniform(0.06, 0.12))
        time.sleep(0.3)
        _click_next(driver, timeout=5)
        time.sleep(random.uniform(1.5, 2.5))
        return

    # URLリンク方式
    url_m = re.search(r'https?://[^\s"\'<>]+', body)
    if url_m:
        driver.get(url_m.group(0).rstrip('.,)"\''))
        time.sleep(random.uniform(2.0, 3.5))


# ── ユーザー名確認画面 ────────────────────────────────────────────────────────

def _handle_username_screen(driver, identity: dict, uname: str) -> None:
    """パスワード後にユーザー名確認画面が出るケース"""
    try:
        field = driver.find_element(By.CSS_SELECTOR, "input[name='username']")
        field.send_keys(Keys.CONTROL, "a")
        field.send_keys(Keys.DELETE)
        for ch in identity["username"]:
            field.send_keys(ch)
            time.sleep(random.uniform(0.04, 0.10))
        _click_next(driver, timeout=5)
        time.sleep(random.uniform(1.0, 2.0))
    except NoSuchElementException:
        pass


# ── 残りフロー処理 (誕生日再確認 / スキップ画面など) ─────────────────────────

def _drain_remaining_flow(driver) -> None:
    """ログイン後のオンボーディング画面を Next / Skip で消化"""
    for sel in [
        (By.CSS_SELECTOR, "[data-testid='ocfEnterTextNextButton']"),
        (By.XPATH,        "//span[normalize-space()='Next']/.."),
        (By.XPATH,        "//span[normalize-space()='Skip for now']/.."),
        (By.XPATH,        "//span[normalize-space()='今はスキップ']/.."),
        (By.XPATH,        "//span[normalize-space()='Done']/.."),
    ]:
        try:
            btn = driver.find_element(*sel)
            btn.click()
            time.sleep(1.0)
        except Exception:
            pass


# ── config.json からキャプチャ設定読み込み ───────────────────────────────────

def _load_captcha_cfg() -> dict:
    import json, pathlib
    for p in ["config.json", "../config.json"]:
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ── DOB入力 ──────────────────────────────────────────────────────────────────

def _fill_dob_x(driver, month: str, day: str, year: str) -> None:
    from selenium.webdriver.support.ui import Select
    # x.com は data-testid="date-picker-*" が現行
    _MONTH_SELS = [
        "select[data-testid='date-picker-month']",
        "select[name='month']",
        "select:first-of-type",
    ]
    _DAY_SELS = [
        "select[data-testid='date-picker-day']",
        "select[name='day']",
    ]
    _YEAR_SELS = [
        "select[data-testid='date-picker-year']",
        "select[name='year']",
    ]

    def _sel_pick(sels, value):
        for sel in sels:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                Select(el).select_by_value(str(int(value)) if value.isdigit() else value)
                time.sleep(0.15)
                return True
            except Exception:
                continue
        return False

    _sel_pick(_MONTH_SELS, month)
    _sel_pick(_DAY_SELS,   day)
    _sel_pick(_YEAR_SELS,  year)
