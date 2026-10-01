"""
modules/platforms/discord.py
Discord サーバーメンバー追加

フロー:
  1. mail.tm でメアド取得 (Discord通過率高い)
  2. discord.com/register でアカウント作成
  3. メール認証 (6桁コード or URL)
  4. 招待URL → 参加ボタン
  5. Imgurからアバター取得して設定
  6. 電話認証が出たらスキップ
"""
from __future__ import annotations

import re
import time
import random

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from modules.captcha_solver import CaptchaSolver
from modules.email_provider import TempMail
from modules.imgur_avatar import fetch_random_avatar, set_discord_avatar


# ── セレクター ────────────────────────────────────────────────────────────────
_S = {
    "email": [
        "input[name='email']",
        "input[type='email']",
        "//input[@placeholder='Email']",
        "//input[@name='email']",
    ],
    "username": [
        "input[name='username']",
        "//input[@placeholder='Username']",
        "//input[@aria-label='username']",
        "//input[contains(@placeholder,'call you')]",
        "//input[contains(@placeholder,'display')]",
    ],
    "password": [
        "input[name='password']",
        "input[type='password']",
        "//input[@placeholder='Password']",
    ],
    "dob_month": [
        "//input[@placeholder='MM']",
        "input[aria-label*='Month' i]",
        "input[name='month']",
    ],
    "dob_day": [
        "//input[@placeholder='DD']",
        "input[aria-label*='Day' i]",
        "input[name='day']",
    ],
    "dob_year": [
        "//input[@placeholder='YYYY']",
        "input[aria-label*='Year' i]",
        "input[name='year']",
    ],
    "submit": [
        "//button[@type='submit']",
        "//button[contains(.,'Continue')]",
        "//button[contains(.,'Register')]",
        "//button[contains(.,'Done')]",
        "button[type='submit']",
    ],
    # 2025年現行UIの参加ボタン
    "join_btn": [
        # data-testid
        "[data-testid='accept-invite-button']",
        "[data-testid='social-layer-accept-button']",
        # aria-label
        "//button[@aria-label='Accept Invite']",
        # テキストマッチ (class問わず)
        "//button[normalize-space()='Accept Invite']",
        "//button[normalize-space()='Join Server']",
        "//button[contains(@class,'button')][contains(.,'Accept')]",
        "//button[contains(@class,'button')][contains(.,'Join')]",
        # 日本語UI
        "//button[contains(.,'参加')]",
        "//button[contains(.,'招待を承認')]",
    ],
    "reg_btn": [
        "//button[contains(.,'Register')]",
        "//a[contains(.,'Register')]",
        "//button[contains(.,'Create an account')]",
        "//button[contains(.,'アカウントを作成')]",
    ],
}


# ── 座標定数 ──────────────────────────────────────────────────────────────────
# 招待ページフォーム (左寄りレイアウト、実測値)
_C = {
    "display_name": (172, 352),
    "month":        (80,  451),
    "day":          (172, 451),
    "year":         (262, 451),
    "create_btn":   (172, 547),
}


def _click_at(driver, x: int, y: int) -> None:
    from selenium.webdriver.common.action_chains import ActionChains
    ActionChains(driver).move_by_offset(x, y).click().perform()
    ActionChains(driver).move_by_offset(-x, -y).perform()  # reset


def _click_coord(driver, key: str) -> None:
    from selenium.webdriver.common.action_chains import ActionChains
    x, y = _C[key]
    # elementFromPoint().click() は Reactコンポーネントで Illegal invocation になる
    # move_by_offset でビューポート絶対座標に直接移動してクリック (origin=viewport)
    ActionChains(driver) \
        .move_by_offset(x, y).click() \
        .move_by_offset(-x, -y) \
        .perform()


def join_server(
    driver,
    invite_url: str,
    identity: dict,
    proxy_dict: dict | None,
    captcha_api_key: str,
) -> bool:
    """
    フロー:
      1. discord.com/register でアカウント作成
      2. メール認証
      3. 招待URL → Join ボタン
    """
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait as _WDW
    from selenium.webdriver.support import expected_conditions as _EC

    # Discord は mail.tm のみ通過率が安定している (guerrilla は弾かれる)
    mail = TempMail(proxy=proxy_dict, prefer="mailtm")
    identity["email"] = mail.email

    def ss(tag):
        try:
            import pathlib
            pathlib.Path("data").mkdir(exist_ok=True)
            driver.save_screenshot(f"data/dc_{tag}_{identity['username'][:8]}.png")
        except Exception:
            pass

    try:
        driver.set_window_size(1366, 768)

        # ── Step 1: 登録ページ ─────────────────────────────────────────────
        driver.get("https://discord.com/register")
        # Cloudflare / React 初期化待ち: email input が出るまで最大20秒
        _page_ready(driver, timeout=20)
        _dismiss_cookie(driver)
        ss("01_register")

        # ── Step 2: フォーム入力 ───────────────────────────────────────────
        if not _fill_register_form(driver, identity, _WDW(driver, 15)):
            ss("ERR_form")
            return False
        ss("02_form")

        # ── Step 3: キャプチャ ─────────────────────────────────────────────
        CaptchaSolver(driver, api_key=captcha_api_key).solve()
        _sleep(2.0, 3.0)
        ss("03_captcha")

        # ── Step 4: メール認証 ─────────────────────────────────────────────
        _handle_email_verify(driver, mail, identity["username"], _WDW(driver, 10))
        _sleep(2.0, 3.0)
        ss("04_verify")

        # ── Step 5: 電話認証チェック ───────────────────────────────────────
        if _phone_required(driver):
            return False

        # ── Step 6: 招待URL → 参加ボタン ──────────────────────────────────
        driver.get(invite_url)
        _sleep(3.5, 5.0)
        ss("05_invite")

        # ログインしたまま招待ページに来てるはずなので Join ボタンを押す
        _dismiss_cookie(driver)

        # セレクターで試す
        joined = False
        join_sels = [
            (By.CSS_SELECTOR, "[data-testid='accept-invite-button']"),
            (By.CSS_SELECTOR, "[data-testid='social-layer-accept-button']"),
            (By.XPATH,        "//button[normalize-space()='Accept Invite']"),
            (By.XPATH,        "//button[normalize-space()='Join Server']"),
            (By.XPATH,        "//button[contains(.,'Accept')]"),
            (By.XPATH,        "//button[contains(.,'Join')]"),
            (By.XPATH,        "//button[contains(.,'参加')]"),
            (By.XPATH,        "//button[contains(.,'招待を承認')]"),
        ]
        for by, sel in join_sels:
            try:
                btn = _WDW(driver, 5).until(_EC.element_to_be_clickable((by, sel)))
                btn.click()
                _sleep(2.0, 3.0)
                joined = True
                break
            except Exception:
                continue

        # JSフォールバック
        if not joined:
            joined = _click_js_join(driver)
            if joined:
                _sleep(2.0, 3.0)

        # ── Step 7: 参加確認 ───────────────────────────────────────────────
        if _is_in_server(driver) or joined:
            ss("06_joined")
            try:
                img_bytes = fetch_random_avatar(proxy=proxy_dict)
                if img_bytes:
                    set_discord_avatar(driver, img_bytes)
            except Exception:
                pass
            return True

        ss("ERR_join")
        return False

    except Exception as e:
        print(f"[DC] {type(e).__name__}: {e}")
        ss("ERR")
        return False

def _page_ready(driver, timeout: int = 20) -> bool:
    """
    discord.com/register のReactアプリが描画完了するまで待つ。
    email input か Cloudflare チャレンジかを判定して適切に待機する。
    """
    import time as _time
    deadline = _time.time() + timeout
    email_sels = [
        "input[name='email']",
        "input[type='email']",
        "//input[@placeholder='Email']",
    ]
    while _time.time() < deadline:
        # Cloudflare チャレンジ中は title に "Just a moment" が入る
        try:
            title = driver.title.lower()
            if "just a moment" in title or "checking your browser" in title:
                _time.sleep(1.5)
                continue
        except Exception:
            pass

        # email input が見えたら完了
        for sel in email_sels:
            try:
                by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
                from selenium.webdriver.support.ui import WebDriverWait as _W
                from selenium.webdriver.support import expected_conditions as _E
                _W(driver, 2).until(_E.presence_of_element_located((by, sel)))
                return True
            except Exception:
                continue

        _time.sleep(0.5)
    return False



# ── フォーム入力 ──────────────────────────────────────────────────────────────

def _fill_register_form(driver, identity: dict, wait: WebDriverWait) -> bool:
    # メールフィールド確認
    if not _wait_for_any(driver, _S["email"], timeout=20):
        return False

    dob = identity["dob"].split("-")

    _type_fb(driver, _S["email"],    identity["email"])
    _sleep(0.2, 0.4)
    # display_name がある場合 (register UI)
    _type_fb(driver, [
        "input[name='global_name']",
        "//input[@aria-label='Display Name']",
        "//input[contains(@placeholder,'display')]",
        "//input[contains(@placeholder,'call you')]",
    ], identity.get("full_name") or identity["username"])
    _sleep(0.2, 0.4)
    _type_fb(driver, _S["username"], identity["username"])
    _sleep(0.2, 0.4)
    _type_fb(driver, _S["password"], identity["password"])
    _sleep(0.2, 0.4)
    _fill_dob(driver, dob)

    _sleep(0.3, 0.6)

    # チェックボックス (利用規約同意 / 年齢確認 — 2025 UI ではラベルクリックが確実)
    _checkbox_sels = [
        "[data-testid='age-gate-checkbox']",
        "input[type='checkbox']",
        # React仮想DOMでinputが隠れてるケース → ラベルをクリック
        "label[for*='age']",
        "label[for*='terms']",
        "[class*='checkbox']",
    ]
    for sel in _checkbox_sels:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            # input要素なら is_selected() で状態確認、それ以外は直クリック
            if el.tag_name == "input":
                if not el.is_selected():
                    driver.execute_script("arguments[0].click();", el)
            else:
                el.click()
            _sleep(0.2, 0.4)
            break
        except Exception:
            pass

    _click_fb(driver, _S["submit"])
    _sleep(2.0, 3.0)
    return True


# ── メール認証 ────────────────────────────────────────────────────────────────

def _handle_email_verify(driver, mail, username: str, wait: WebDriverWait) -> None:
    code_sels = [
        "input[aria-label*='code' i]",
        "input[name='code']",
        "input[placeholder*='6-digit' i]",
        "input[maxlength='6']",
        "input[inputmode='numeric']",
        "//input[@aria-label='Enter Discord Auth App code']",
    ]

    # コードフィールドが出るか待つ
    code_field = None
    for sel in code_sels:
        try:
            by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
            el = WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((by, sel))
            )
            code_field = el
            break
        except TimeoutException:
            continue

    if code_field:
        # メールから認証情報を取得
        body = None
        for kw in ["Discord", "verify", "confirm", "code", "authentication"]:
            body = mail.wait_for_verification(keyword=kw, timeout=60, poll=1.5)
            if body:
                break

        if body:
            # 6桁コード
            m = re.search(r'\b(\d{6})\b', re.sub(r'<[^>]+>', ' ', body))
            if m:
                code = m.group(1)
                print(f"[DC] 確認コード: {code}")
                code_field.clear()
                for ch in code:
                    code_field.send_keys(ch)
                    time.sleep(random.uniform(0.06, 0.12))
                _click_fb(driver, _S["submit"])
                _sleep(1.5, 2.5)
                return

            # URLリンク
            url_m = re.search(r'https?://[^\s"\'<>]+', body)
            if url_m:
                driver.get(url_m.group(0).rstrip('.,)"\''))
                _sleep(2.0, 3.0)
    else:
        # コードフィールドなし → URLクリック方式
        body = mail.wait_for_verification(keyword="verify", timeout=40, poll=1.5)
        if body:
            url_m = re.search(r'https?://[^\s"\'<>]+', body)
            if url_m:
                driver.get(url_m.group(0).rstrip('.,)"\''))
                _sleep(2.0, 3.0)


# ── ユーティリティ ────────────────────────────────────────────────────────────

def _click_js_join(driver) -> bool:
    """JSで全ボタンを走査して参加ボタンを強制クリック"""
    try:
        result = driver.execute_script("""
            var btns = document.querySelectorAll('button');
            for (var b of btns) {
                var txt = b.innerText || b.textContent || '';
                if (/Accept Invite|Join Server|参加|招待を承認/i.test(txt)) {
                    b.click();
                    return true;
                }
            }
            return false;
        """)
        return bool(result)
    except Exception:
        return False


def _phone_required(driver) -> bool:
    try:
        txt = driver.find_element(By.TAG_NAME, "body").text.lower()
        return any(k in txt for k in [
            "phone verification", "verify your phone",
            "電話番号", "phone number required",
            "add a phone number",
        ])
    except Exception:
        return False


def _is_in_server(driver) -> bool:
    try:
        url = driver.current_url
        return "channels/" in url and "@me" not in url
    except Exception:
        return False


def _dismiss_cookie(driver) -> None:
    for xp in [
        "//button[contains(.,'Accept')]",
        "//button[contains(.,'OK')]",
        "//button[contains(.,'Got it')]",
        "//button[contains(.,'承認')]",
    ]:
        try:
            driver.find_element(By.XPATH, xp).click()
            time.sleep(0.3)
            return
        except Exception:
            pass


def _wait_for_any(driver, selectors: list[str], timeout=8) -> bool:
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


def _type_fb(driver, selectors: list[str], text: str) -> bool:
    for sel in selectors:
        try:
            by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
            el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel)))
            el.click()
            driver.execute_script("""
                var el=arguments[0], val=arguments[1];
                var s=Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype,'value').set;
                s.call(el,val);
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
            """, el, text)
            time.sleep(random.uniform(0.06, 0.14))
            return True
        except Exception:
            continue
    return False


def _click_fb(driver, selectors: list[str], timeout=5) -> bool:
    for sel in selectors:
        try:
            by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
            el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, sel)))
            el.click()
            return True
        except Exception:
            continue
    return False


def _fill_dob(driver, dob: list) -> None:
    """
    生年月日入力。Discord 2025 UI は select か React custom picker かで分岐。
    dob = ["2000", "05", "15"]  (YYYY, MM, DD)
    """
    from selenium.webdriver.support.ui import Select as _Select
    from selenium.webdriver.common.keys import Keys as _Keys

    month_int = int(dob[1])
    day_int   = int(dob[2])
    year_str  = dob[0]

    # ── A: select要素が3個あれば旧来フロー ───────────────────────────────
    selects = driver.find_elements(By.CSS_SELECTOR, "select")
    if len(selects) >= 3:
        try:
            _Select(selects[0]).select_by_value(str(month_int))
            _sleep(0.15)
            _Select(selects[1]).select_by_value(str(day_int))
            _sleep(0.15)
            _Select(selects[2]).select_by_value(year_str)
            _sleep(0.15)
            return
        except Exception:
            pass

    # ── B: React Custom Picker (2025現行UI) ──────────────────────────────
    # input[placeholder='MM'] / input[placeholder='DD'] / input[placeholder='YYYY']
    # または aria-label="Month" etc.
    _MONTH_SELS = [
        "input[placeholder='MM']",
        "input[aria-label*='Month' i]",
        "input[name='month']",
        "//input[@placeholder='Month']",
    ]
    _DAY_SELS = [
        "input[placeholder='DD']",
        "input[aria-label*='Day' i]",
        "input[name='day']",
        "//input[@placeholder='Day']",
    ]
    _YEAR_SELS = [
        "input[placeholder='YYYY']",
        "input[aria-label*='Year' i]",
        "input[name='year']",
        "//input[@placeholder='Year']",
    ]

    def _fill_input(sels, value: str):
        for sel in sels:
            try:
                by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
                el = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((by, sel))
                )
                el.click()
                el.send_keys(_Keys.CONTROL, "a")
                el.send_keys(_Keys.DELETE)
                for ch in value:
                    el.send_keys(ch)
                    time.sleep(random.uniform(0.04, 0.09))
                _sleep(0.1, 0.2)
                return True
            except Exception:
                continue
        return False

    _fill_input(_MONTH_SELS, str(month_int).zfill(2))
    _sleep(0.1, 0.2)
    _fill_input(_DAY_SELS,   str(day_int).zfill(2))
    _sleep(0.1, 0.2)
    _fill_input(_YEAR_SELS,  year_str)
    _sleep(0.1, 0.2)

    # ── C: それでもダメなら座標クリック → キーボード ──────────────────────
    # _C の month/day/year 座標に直打ち (move_by_offset でビューポート絶対座標)
    from selenium.webdriver.common.action_chains import ActionChains as _AC

    def _coord_type(cx, cy, text):
        try:
            _AC(driver).move_by_offset(cx, cy).click().move_by_offset(-cx, -cy).perform()
            _sleep(0.15)
            active = driver.switch_to.active_element
            active.send_keys(_Keys.CONTROL, "a")
            active.send_keys(_Keys.DELETE)
            for ch in text:
                active.send_keys(ch)
                time.sleep(random.uniform(0.04, 0.09))
        except Exception:
            pass

    _coord_type(_C["month"][0], _C["month"][1], str(month_int).zfill(2))
    _coord_type(_C["day"][0],   _C["day"][1],   str(day_int).zfill(2))
    _coord_type(_C["year"][0],  _C["year"][1],  year_str)


def _sleep(lo=0.5, hi=1.2):
    time.sleep(random.uniform(lo, hi))
