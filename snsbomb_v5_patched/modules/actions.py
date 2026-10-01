"""
actions.py — 全プラットフォーム・全アクション定義
各アクションは同一シグネチャ:
    fn(driver, target: str, identity: dict, proxy_dict: dict|None, captcha_api_key: str) -> bool
target: URL または ユーザー名 (アクションによる)
"""
from __future__ import annotations
import time, random
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from modules.captcha_solver import CaptchaSolver
from modules.email_provider import TempMail
from modules.platforms.note import register_and_follow as note_follow, register_and_like as note_like

# ── ヘルパー ──────────────────────────────────────────────────────────────────
def _wait(driver, timeout=12):
    return WebDriverWait(driver, timeout)

def _type(driver, wait, selector: str, text: str, by=By.CSS_SELECTOR):
    """JS setValueで一括入力 → input/changeイベント発火 → send_keysより10x速い"""
    el = wait.until(EC.element_to_be_clickable((by, selector)))
    el.click()
    driver.execute_script("""
        var el = arguments[0], val = arguments[1];
        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        nativeInputValueSetter.call(el, val);
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
    """, el, text)
    time.sleep(random.uniform(0.06, 0.15))

def _type_slow(driver, wait, selector: str, text: str, by=By.CSS_SELECTOR):
    """JS が効かないフィールド用 (認証コード等) — 1文字ずつ"""
    el = wait.until(EC.element_to_be_clickable((by, selector)))
    el.clear()
    for ch in text:
        el.send_keys(ch)
        time.sleep(random.uniform(0.03, 0.07))

def _click(wait, selector: str, by=By.CSS_SELECTOR):
    wait.until(EC.element_to_be_clickable((by, selector))).click()

def _sleep(lo=0.5, hi=1.2):
    time.sleep(random.uniform(lo, hi))

def _ig_login(driver, identity: dict, proxy_dict, captcha_api_key: str) -> bool:
    """Instagram: アカウント作成 → ログイン状態にする (2025 UI対応)"""
    import re as _re
    mail = TempMail(proxy=proxy_dict)
    identity["email"] = mail.email
    wait = _wait(driver, 20)

    def _ss(tag):
        try: driver.save_screenshot(f"data/ig_{tag}_{identity['username'][:8]}.png")
        except Exception: pass

    def _dismiss_cookie():
        for xp in ["//button[contains(.,'Allow')]","//button[contains(.,'Accept')]",
                   "//button[contains(.,'すべて許可')]","//button[contains(.,'OK')]"]:
            try: driver.find_element(By.XPATH, xp).click(); _sleep(0.4); return
            except Exception: pass

    def _type_fb(sel_list, text):
        """セレクター候補リストを順に試して入力。"""
        for sel in sel_list:
            try:
                by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
                el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel)))
                el.click()
                driver.execute_script("""
                    var el=arguments[0],val=arguments[1];
                    var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                    s.call(el,val);
                    el.dispatchEvent(new Event('input',{bubbles:true}));
                    el.dispatchEvent(new Event('change',{bubbles:true}));
                """, el, text)
                _sleep(0.1, 0.2)
                return True
            except Exception: continue
        return False

    def _click_fb(sel_list):
        for sel in sel_list:
            try:
                by = By.XPATH if sel.startswith("/") else By.CSS_SELECTOR
                WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel))).click()
                return True
            except Exception: continue
        return False

    try:
        driver.get("https://www.instagram.com/accounts/emailsignup/")
        _sleep(2.0, 3.5)
        _dismiss_cookie()
        _sleep(0.5, 1.0)
        _ss("01_signup")

        # メール
        _type_fb([
            "input[name='emailOrPhone']",
            "input[name='email']",
            "input[type='email']",
            "//input[@aria-label='Mobile Number or Email']",
            "//input[@aria-label='Email address']",
        ], identity["email"])

        # フルネーム
        _type_fb([
            "input[name='fullName']",
            "//input[@aria-label='Full Name']",
            "//input[@placeholder='Full Name']",
        ], identity["full_name"])

        # ユーザー名
        _type_fb([
            "input[name='username']",
            "//input[@aria-label='Username']",
        ], identity["username"])

        # パスワード
        _type_fb([
            "input[name='password']",
            "input[type='password']",
            "//input[@aria-label='Password']",
        ], identity["password"])

        _sleep(0.5, 1.0)
        _ss("02_form_filled")

        _click_fb(["button[type='submit']",
                   "//button[.//div[text()='Sign up']]",
                   "//button[.//div[text()='Next']]"])
        _sleep(2.0, 3.5)
        _ss("03_after_submit")

        # 生年月日
        from selenium.webdriver.support.ui import Select
        dob = identity["dob"].split("-")
        for m_sel in ["select[title='Month']","select[aria-label*='Month']","#month"]:
            try: Select(driver.find_element(By.CSS_SELECTOR, m_sel)).select_by_value(str(int(dob[1]))); break
            except Exception: pass
        for d_sel in ["select[title='Day']","select[aria-label*='Day']","#day"]:
            try: Select(driver.find_element(By.CSS_SELECTOR, d_sel)).select_by_value(str(int(dob[2]))); break
            except Exception: pass
        for y_sel in ["select[title='Year']","select[aria-label*='Year']","#year"]:
            try: Select(driver.find_element(By.CSS_SELECTOR, y_sel)).select_by_value(dob[0]); break
            except Exception: pass

        _click_fb(["button[type='submit']",
                   "//button[.//div[text()='Next']]",
                   "//button[contains(.,'Next')]"])
        _sleep(2.0, 3.5)
        _ss("04_after_dob")

        CaptchaSolver(driver, api_key=captcha_api_key).solve()
        _sleep(1.0, 1.5)

        # メール確認コード or URL
        for kw in ["Instagram","verify","confirm","code","確認"]:
            body = mail.wait_for_verification(keyword=kw, timeout=40)
            if body: break
        else:
            body = None

        if body:
            # 6桁コード
            m = _re.search(r'\b(\d{6})\b', _re.sub(r'<[^>]+>',' ',body))
            if m:
                code = m.group(1)
                print(f"[IG/login] 確認コード: {code}")
                _type_fb([
                    "input[name='email_confirmation_code']",
                    "//input[@aria-label='Confirmation Code']",
                    "input[autocomplete='one-time-code']",
                    "input[inputmode='numeric']",
                ], code)
                _click_fb(["button[type='submit']",
                           "//button[contains(.,'Next')]",
                           "//button[contains(.,'Confirm')]"])
                _sleep(1.5, 2.5)
            else:
                # URL
                url_m = _re.search(r'https?://\S+', body)
                if url_m:
                    driver.get(url_m.group(0).rstrip('.,)"\''))
                    _sleep(2.0, 3.0)

        _ss("05_done")
        return True

    except Exception as e:
        print(f"[IG/login] {type(e).__name__}: {e}")
        _ss("ERR")
        return False

def _x_login(driver, identity: dict, proxy_dict, captcha_api_key: str) -> bool:
    """X: アカウント作成 → ログイン状態"""
    mail = TempMail(proxy=proxy_dict)
    identity["email"] = mail.email
    wait = _wait(driver)
    try:
        driver.get("https://twitter.com/i/flow/signup")
        _sleep(1.0, 1.8)
        _type(driver, wait, "input[name='name']", identity["full_name"])
        _click(wait, "//span[text()='Next']/..", By.XPATH)
        _sleep(0.4, 0.8)
        try:
            driver.find_element(By.XPATH, "//span[text()='Use email instead']").click()
        except Exception: pass
        _type(driver, wait, "input[name='email']", identity["email"])
        _click(wait, "//span[text()='Next']/..", By.XPATH)
        _sleep(0.4, 0.8)
        dob = identity["dob"].split("-")
        from selenium.webdriver.support.ui import Select
        try:
            Select(driver.find_element(By.CSS_SELECTOR, "select[data-testid='date-picker-month']")).select_by_value(str(int(dob[1])))
            Select(driver.find_element(By.CSS_SELECTOR, "select[data-testid='date-picker-day']")).select_by_value(str(int(dob[2])))
            Select(driver.find_element(By.CSS_SELECTOR, "select[data-testid='date-picker-year']")).select_by_value(dob[0])
        except Exception: pass
        _click(wait, "//span[text()='Next']/..", By.XPATH)
        _sleep(0.6, 1.1)
        code_url = mail.wait_for_verification(keyword="confirm", timeout=60)
        if code_url:
            driver.get(code_url)
            _sleep(2)
        _type(driver, wait, "input[name='password']", identity["password"])
        _click(wait, "//span[text()='Next']/..", By.XPATH)
        _sleep(0.7, 1.3)
        CaptchaSolver(driver, api_key=captcha_api_key).solve()
        _sleep(0.6, 1.1)
        return True
    except Exception as e:
        print(f"[X/login] {e}")
        return False

def _yt_login(driver, identity: dict, proxy_dict, captcha_api_key: str) -> bool:
    """YouTube: Googleアカウント作成 → ログイン"""
    mail = TempMail(proxy=proxy_dict)
    identity["email"] = mail.email.split("@")[0] + "@gmail.com"
    wait = _wait(driver, 30)
    try:
        driver.get("https://accounts.google.com/signup/v2/createaccount?flowName=GlifWebSignIn")
        _sleep(0.7, 1.3)
        _type(driver, wait, "input[name='firstName']", identity["first_name"])
        _type(driver, wait, "input[name='lastName']", identity["last_name"])
        _click(wait, "#collectNameNext")
        _sleep(0.5, 1.0)
        dob = identity["dob"].split("-")
        from selenium.webdriver.support.ui import Select
        try:
            Select(driver.find_element(By.ID, "month")).select_by_value(str(int(dob[1])))
            _type(driver, wait, "input[name='day']", dob[2])
            _type(driver, wait, "input[name='year']", dob[0])
            Select(driver.find_element(By.ID, "gender")).select_by_value("1")
        except Exception: pass
        _click(wait, "#birthdaygenderNext")
        _sleep(0.5, 1.0)
        # username
        try:
            _click(wait, "//div[contains(text(),'Create your own')]/..", By.XPATH)
            _sleep(0.2)
        except Exception: pass
        _type(driver, wait, "input[name='Username']", identity["username"])
        _click(wait, "#userNameNext")
        _sleep(0.5, 1.0)
        _type(driver, wait, "input[name='Passwd']", identity["password"])
        _type(driver, wait, "input[name='PasswdAgain']", identity["password"])
        _click(wait, "#passwordNext")
        _sleep(0.7, 1.3)
        CaptchaSolver(driver, api_key=captcha_api_key).solve()
        _sleep(1.0, 1.8)
        return True
    except Exception as e:
        print(f"[YT/login] {e}")
        return False

def _tt_login(driver, identity: dict, proxy_dict, captcha_api_key: str) -> bool:
    """
    TikTok: メール登録 (2025現行フロー)
    フロー: トップ → Use phone / email → Email tab
            → DOB入力 → Next → email + password → Send code → 6桁コード入力
    """
    mail = TempMail(proxy=proxy_dict)
    identity["email"] = mail.email
    wait = _wait(driver, 30)
    from selenium.webdriver.support.ui import Select
    from selenium.webdriver.common.keys import Keys

    try:
        # ── Step 1: サインアップページ ──────────────────────────────────────
        driver.get("https://www.tiktok.com/signup")
        _sleep(1.0, 1.8)

        # "Use phone or email" ボタンを探してクリック
        try:
            btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//*[contains(text(),'Use phone') or contains(text(),'電話番号') or contains(text(),'phone or email')]")
            ))
            btn.click()
            _sleep(0.4, 0.8)
        except Exception:
            # 既にフォームが出ている場合は pass
            pass

        # Email タブへ切替 (Phone がデフォルトの場合)
        try:
            email_tab = driver.find_element(By.XPATH,
                "//*[contains(text(),'Email') or contains(text(),'メール')][@role='tab' or self::a or self::button]"
            )
            email_tab.click()
            _sleep(0.3)
        except Exception:
            pass

        # ── Step 2: 生年月日 (DOB) ──────────────────────────────────────────
        dob = identity["dob"].split("-")  # YYYY-MM-DD

        # パターンA: <select> ドロップダウン
        dob_done = False
        try:
            month_sel = driver.find_element(By.CSS_SELECTOR,
                "select[name='month'], select[placeholder*='Month'], select[aria-label*='Month'], select[aria-label*='月']"
            )
            Select(month_sel).select_by_value(str(int(dob[1])))
            day_sel = driver.find_element(By.CSS_SELECTOR,
                "select[name='day'], select[placeholder*='Day'], select[aria-label*='Day'], select[aria-label*='日']"
            )
            Select(day_sel).select_by_value(str(int(dob[2])))
            year_sel = driver.find_element(By.CSS_SELECTOR,
                "select[name='year'], select[placeholder*='Year'], select[aria-label*='Year'], select[aria-label*='年']"
            )
            Select(year_sel).select_by_value(dob[0])
            dob_done = True
        except Exception:
            pass

        # パターンB: テキスト入力 (data-e2e属性)
        if not dob_done:
            try:
                for attr, val in [("month", dob[1].lstrip("0")), ("day", dob[2].lstrip("0")), ("year", dob[0])]:
                    f = driver.find_element(By.CSS_SELECTOR,
                        f"input[data-e2e*='{attr}'], input[placeholder*='{attr.capitalize()}'], input[name='{attr}']"
                    )
                    f.clear()
                    f.send_keys(val)
                    _sleep(0.3)
                dob_done = True
            except Exception:
                pass

        # DOB Next ボタン
        try:
            next_btn = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR,
                 "button[data-e2e='birthday-proceed'], button[data-e2e='channel-next-button'], "
                 "button[type='submit']")
            ))
            next_btn.click()
            _sleep(0.6, 1.0)
        except Exception:
            pass

        # ── Step 3: Email + Password ─────────────────────────────────────────
        email_field = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR,
             "input[name='email'], input[type='email'], input[placeholder*='Email'], input[placeholder*='メール']")
        ))
        email_field.clear()
        for ch in identity["email"]:
            email_field.send_keys(ch)
            time.sleep(random.uniform(0.04, 0.11))
        _sleep(0.2)

        pw_candidates = driver.find_elements(By.CSS_SELECTOR,
            "input[name='password'], input[type='password']"
        )
        if pw_candidates:
            pw = pw_candidates[0]
            pw.clear()
            for ch in identity["password"]:
                pw.send_keys(ch)
                time.sleep(random.uniform(0.04, 0.11))
            _sleep(0.2)

        # Send code / Submit
        try:
            send_btn = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR,
                 "button[data-e2e='send-code-button'], button[data-e2e='register-button'], button[type='submit']")
            ))
            send_btn.click()
            _sleep(0.6, 1.1)
        except Exception:
            pass

        # ── Step 4: キャプチャ ────────────────────────────────────────────────
        CaptchaSolver(driver, api_key=captcha_api_key).solve()
        _sleep(0.4, 0.8)

        # ── Step 5: メール認証コード (6桁) ────────────────────────────────────
        # Guerrilla Mail から件名 "TikTok" or "verification" のメールを待つ
        code_body = None
        for keyword in ["TikTok", "verification", "verify", "code"]:
            code_body = mail.wait_for_verification(keyword=keyword, timeout=45)
            if code_body:
                break

        if code_body:
            # URLではなく6桁コードを探す
            import re
            # まずURLを試す
            url_match = re.search(r'https?://[^\s"\'<>]+', code_body)
            digit_match = re.search(r'\b(\d{6})\b', code_body)

            if url_match:
                driver.get(url_match.group(0))
                _sleep(1.0, 1.8)
            elif digit_match:
                code_str = digit_match.group(1)
                print(f"[TT/login] 認証コード: {code_str}")
                try:
                    code_inputs = wait.until(lambda d: d.find_elements(
                        By.CSS_SELECTOR,
                        "input[data-e2e='verification-code-input'], input[name='code'], "
                        "input[placeholder*='code'], input[placeholder*='コード'], input[maxlength='1']"
                    ))
                    if len(code_inputs) == 1:
                        code_inputs[0].send_keys(code_str)
                    elif len(code_inputs) >= 6:
                        for i, ch in enumerate(code_str[:6]):
                            code_inputs[i].send_keys(ch)
                            _sleep(0.1, 0.2)
                    _sleep(0.4, 0.8)
                    try:
                        confirm = wait.until(EC.element_to_be_clickable(
                            (By.CSS_SELECTOR, "button[type='submit'], button[data-e2e='confirm-button']")
                        ))
                        confirm.click()
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[TT/login] コード入力失敗: {e}")
        else:
            print("[TT/login] 認証メール未着 — 登録自体は通ってる可能性あり")

        _sleep(1.0, 1.8)
        return True

    except Exception as e:
        print(f"[TT/login] {e}")
        # デバッグ用: 現在のURL出力
        try:
            print(f"[TT/login] 現在URL: {driver.current_url}")
        except Exception:
            pass
        return False

def _fb_login(driver, identity: dict, proxy_dict, captcha_api_key: str) -> bool:
    """Facebook: アカウント作成"""
    mail = TempMail(proxy=proxy_dict)
    identity["email"] = mail.email
    wait = _wait(driver, 25)
    try:
        driver.get("https://www.facebook.com/r.php")
        _sleep(0.7, 1.3)
        _type(driver, wait, "input[name='firstname']", identity["first_name"])
        _type(driver, wait, "input[name='lastname']", identity["last_name"])
        _type(driver, wait, "input[name='reg_email__']", identity["email"])
        _type(driver, wait, "input[name='reg_passwd__']", identity["password"])
        dob = identity["dob"].split("-")
        from selenium.webdriver.support.ui import Select
        try:
            Select(driver.find_element(By.ID, "day")).select_by_value(str(int(dob[2])))
            Select(driver.find_element(By.ID, "month")).select_by_value(str(int(dob[1])))
            Select(driver.find_element(By.ID, "year")).select_by_value(dob[0])
        except Exception: pass
        try:
            driver.find_element(By.CSS_SELECTOR, "input[value='2']").click()  # gender
        except Exception: pass
        _click(wait, "button[name='websubmit']")
        _sleep(1.0, 1.8)
        CaptchaSolver(driver, api_key=captcha_api_key).solve()
        verify_url = mail.wait_for_verification(keyword="confirm", timeout=120)
        if verify_url:
            driver.get(verify_url)
            _sleep(1.0, 1.8)
        return True
    except Exception as e:
        print(f"[FB/login] {e}")
        return False


# ════════════════════════════════════════════════════════════════════════════════
# INSTAGRAM アクション
# ════════════════════════════════════════════════════════════════════════════════

def ig_follow(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    if not _ig_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    _FOLLOW_SELS = [
        (By.CSS_SELECTOR, "button[aria-label='Follow']"),
        (By.XPATH,        "//button[.//div[text()='Follow']]"),
        (By.XPATH,        "//header//button[contains(.,'Follow') and not(contains(.,'Following'))]"),
    ]
    try:
        driver.get(f"https://www.instagram.com/{target}/")
        _sleep(1.0, 1.8)
        btn = None
        for by, sel in _FOLLOW_SELS:
            try:
                btn = wait.until(EC.element_to_be_clickable((by, sel)))
                lbl = (btn.get_attribute("aria-label") or btn.text or "").lower()
                if "following" not in lbl and "requested" not in lbl:
                    break
            except Exception:
                btn = None
        if not btn:
            print(f"[IG/follow] follow button not found for @{target}"); return False
        btn.click()
        return True
    except Exception as e:
        print(f"[IG/follow] {e}"); return False

def ig_like(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: 投稿URL"""
    if not _ig_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        like_btn = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "svg[aria-label='Like'], svg[aria-label='いいね！']")
        ))
        like_btn.find_element(By.XPATH, "..").click()
        return True
    except Exception as e:
        print(f"[IG/like] {e}"); return False

def ig_comment(driver, target, identity, proxy_dict, captcha_api_key, comment_text="") -> bool:
    """target: 投稿URL"""
    if not _ig_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        box = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "textarea[placeholder*='comment'], textarea[placeholder*='コメント']")))
        box.click()
        _sleep(0.2)
        for ch in (comment_text or "Nice! 🔥"):
            box.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.15))
        post_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='Post' or text()='投稿']")))
        post_btn.click()
        return True
    except Exception as e:
        print(f"[IG/comment] {e}"); return False

def ig_view_video(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: 動画投稿URL — 30s再生"""
    if not _ig_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        # autoplay — just stay on page
        time.sleep(20)
        return True
    except Exception as e:
        print(f"[IG/view_video] {e}"); return False

def ig_live_view(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: ユーザー名 — ライブ配信を60s視聴"""
    if not _ig_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(f"https://www.instagram.com/{target}/live/")
        _sleep(1.0, 1.8)
        time.sleep(30)
        return True
    except Exception as e:
        print(f"[IG/live_view] {e}"); return False

def ig_impression(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: 投稿URL — プロフ/投稿を閲覧してインプレッション+"""
    if not _ig_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(target)
        _sleep(0.6, 1.0)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2)")
        _sleep(1.0, 2.0)
        return True
    except Exception as e:
        print(f"[IG/impression] {e}"); return False

def ig_reels_view(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: Reels URL"""
    if not _ig_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        time.sleep(20)
        return True
    except Exception as e:
        print(f"[IG/reels_view] {e}"); return False


# ════════════════════════════════════════════════════════════════════════════════
# X (TWITTER) アクション
# ════════════════════════════════════════════════════════════════════════════════

def x_follow(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    if not _x_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(f"https://twitter.com/{target}")
        _sleep(1.0, 1.8)
        # クッキーバナー dismiss
        for sel in ["//span[text()='Accept all cookies']/..", "[data-testid='xMigrationBottomBar'] button"]:
            try:
                by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
                driver.find_element(by, sel).click(); _sleep(0.3)
            except Exception: pass
        # X: フォローボタン (2025現行UI — プロフページ右上の黒ボタン)
        btn = None
        for sel in [
            "[data-testid='followButton']",
            # aria-labelにユーザー名が入るパターン
            f"[aria-label*='Follow @{target}']",
            f"[aria-label*='フォローする @{target}']",
            # フォールバック: header内のボタン
            "//div[@data-testid='primaryColumn']//button[not(@aria-label) or not(contains(@aria-label,'Following'))]",
        ]:
            try:
                by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
                el = wait.until(EC.element_to_be_clickable((by, sel)))
                lbl = el.get_attribute("aria-label") or el.text or ""
                if "following" in lbl.lower() or "フォロー中" in lbl:
                    continue  # 既にフォロー済み
                btn = el
                break
            except Exception:
                continue
        lbl = (btn.get_attribute("aria-label") or "").lower()
        if "following" in lbl:
            print(f"[X/follow] already following {target}"); return True
        btn.click()
        return True
    except Exception as e:
        print(f"[X/follow] {e}"); return False

def x_like(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: ツイートURL"""
    if not _x_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='like']")))
        btn.click()
        return True
    except Exception as e:
        print(f"[X/like] {e}"); return False

def x_repost(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: ツイートURL"""
    if not _x_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='retweet']")))
        btn.click()
        _sleep(0.2)
        confirm = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='retweetConfirm']")))
        confirm.click()
        return True
    except Exception as e:
        print(f"[X/repost] {e}"); return False

def x_reply(driver, target, identity, proxy_dict, captcha_api_key, comment_text="") -> bool:
    """target: ツイートURL"""
    if not _x_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        reply_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='reply']")))
        reply_btn.click()
        _sleep(1)
        box = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='tweetTextarea_0']")))
        for ch in (comment_text or "🔥"):
            box.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.13))
        send = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='tweetButtonInline']")))
        send.click()
        return True
    except Exception as e:
        print(f"[X/reply] {e}"); return False

def x_impression(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: ツイートURL or ユーザー名"""
    if not _x_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        url = target if target.startswith("http") else f"https://twitter.com/{target}"
        driver.get(url)
        _sleep(0.6, 1.0)
        driver.execute_script("window.scrollTo(0, 500)")
        _sleep(1.0, 2.0)
        return True
    except Exception as e:
        print(f"[X/impression] {e}"); return False

def x_view_video(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: 動画ツイートURL"""
    if not _x_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        time.sleep(20)
        return True
    except Exception as e:
        print(f"[X/view_video] {e}"); return False

def x_poll_vote(driver, target, identity, proxy_dict, captcha_api_key, poll_option: int = 0) -> bool:
    """target: 投票ツイートURL, poll_option: 0始まりの選択肢インデックス"""
    if not _x_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        options = wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "[data-testid='pollOption']"))
        idx = min(poll_option, len(options) - 1)
        options[idx].click()
        return True
    except Exception as e:
        print(f"[X/poll_vote] {e}"); return False


# ════════════════════════════════════════════════════════════════════════════════
# YOUTUBE アクション
# ════════════════════════════════════════════════════════════════════════════════

def yt_subscribe(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: チャンネルURL or @handle"""
    if not _yt_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        url = target if target.startswith("http") else f"https://www.youtube.com/@{target}"
        driver.get(url)
        _sleep(1.0, 1.8)
        # YouTube: 黒い「チャンネル登録」ボタン (2025現行UI)
        btn = None
        for sel in [
            # 新UI: yt-button-shape内
            "ytd-subscribe-button-renderer yt-button-shape button",
            "#subscribe-button yt-button-shape button",
            # aria-label
            "button[aria-label*='Subscribe']",
            "button[aria-label*='チャンネル登録']",
            # 旧UI
            "ytd-subscribe-button-renderer button",
            "#subscribe-button tp-yt-paper-button",
            # テキストフォールバック
            "//yt-button-shape//button[contains(.,'登録') or contains(.,'Subscribe')]",
        ]:
            try:
                by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
                el = wait.until(EC.element_to_be_clickable((by, sel)))
                lbl = el.get_attribute("aria-label") or el.text or ""
                if "登録済み" in lbl or "subscribed" in lbl.lower():
                    continue
                btn = el
                break
            except Exception:
                continue
        btn.click()
        return True
    except Exception as e:
        print(f"[YT/subscribe] {e}"); return False

def yt_view(driver, target, identity, proxy_dict, captcha_api_key, watch_time: int = 60) -> bool:
    """target: 動画URL, watch_time: 秒"""
    if not _yt_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        # unmute & play
        try:
            driver.execute_script("""
                var v = document.querySelector('video');
                if(v){ v.muted=false; v.play(); }
            """)
        except Exception: pass
        time.sleep(watch_time)
        return True
    except Exception as e:
        print(f"[YT/view] {e}"); return False

def yt_like(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: 動画URL"""
    if not _yt_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(4, 6)
        btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "ytd-toggle-button-renderer.ytd-menu-renderer:first-child button, #top-level-buttons-computed yt-button-shape:first-child button")))
        btn.click()
        return True
    except Exception as e:
        print(f"[YT/like] {e}"); return False

def yt_live_view(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: ライブ配信URL"""
    if not _yt_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        time.sleep(30)
        return True
    except Exception as e:
        print(f"[YT/live_view] {e}"); return False

def yt_shorts_view(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: Shorts URL"""
    if not _yt_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        try:
            driver.execute_script("document.querySelector('video').play()")
        except Exception: pass
        time.sleep(20)
        return True
    except Exception as e:
        print(f"[YT/shorts_view] {e}"); return False

def yt_comment(driver, target, identity, proxy_dict, captcha_api_key, comment_text="") -> bool:
    """target: 動画URL"""
    if not _yt_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(4, 6)
        driver.execute_script("window.scrollTo(0, 500)")
        _sleep(0.4, 0.8)
        box = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#simplebox-placeholder, #contenteditable-root")))
        box.click()
        _sleep(0.2)
        box = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#contenteditable-root")))
        for ch in (comment_text or "Great video! 🔥"):
            box.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.13))
        submit = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#submit-button")))
        submit.click()
        return True
    except Exception as e:
        print(f"[YT/comment] {e}"); return False


# ════════════════════════════════════════════════════════════════════════════════
# TIKTOK アクション
# ════════════════════════════════════════════════════════════════════════════════

def tt_follow(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    if not _tt_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(f"https://www.tiktok.com/@{target}")
        _sleep(1.0, 1.8)
        # TikTok: 赤いフォローボタン (2025現行UI)
        btn = None
        for sel in [
            "[data-e2e='follow-button']",
            "[data-e2e='user-follow-button']",
            # aria-label
            "button[aria-label*='Follow']",
            # 赤背景ボタン (style属性でrgb確認)
            "//button[contains(@style,'background') and (contains(.,'Follow') or contains(.,'フォロー'))]",
            # フォールバック
            "//div[@data-e2e='user-page']//button[contains(.,'Follow') or contains(.,'フォロー')]",
        ]:
            try:
                by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
                el = wait.until(EC.element_to_be_clickable((by, sel)))
                lbl = el.get_attribute("aria-label") or el.text or ""
                if "following" in lbl.lower() or "フォロー中" in lbl:
                    continue
                btn = el
                break
            except Exception:
                continue
        btn.click()
        return True
    except Exception as e:
        print(f"[TT/follow] {e}"); return False

def tt_like(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: 動画URL"""
    if not _tt_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-e2e='like-icon']")))
        btn.click()
        return True
    except Exception as e:
        print(f"[TT/like] {e}"); return False

def tt_view(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: 動画URL"""
    if not _tt_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        time.sleep(20)
        return True
    except Exception as e:
        print(f"[TT/view] {e}"); return False

def tt_comment(driver, target, identity, proxy_dict, captcha_api_key, comment_text="") -> bool:
    """target: 動画URL"""
    if not _tt_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        box = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-e2e='comment-input']")))
        box.click()
        for ch in (comment_text or "🔥"):
            box.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.13))
        send = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-e2e='comment-post']")))
        send.click()
        return True
    except Exception as e:
        print(f"[TT/comment] {e}"); return False

def tt_share(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: 動画URL — シェアパネルを開く"""
    if not _tt_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-e2e='share-icon']")))
        btn.click()
        _sleep(0.4, 0.8)
        return True
    except Exception as e:
        print(f"[TT/share] {e}"); return False

def tt_live_view(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: ユーザー名"""
    if not _tt_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(f"https://www.tiktok.com/@{target}/live")
        _sleep(1.0, 1.8)
        time.sleep(30)
        return True
    except Exception as e:
        print(f"[TT/live_view] {e}"); return False


# ════════════════════════════════════════════════════════════════════════════════
# FACEBOOK アクション
# ════════════════════════════════════════════════════════════════════════════════

def fb_page_follow(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: ページURL or @handle"""
    if not _fb_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        url = target if target.startswith("http") else f"https://www.facebook.com/{target}"
        driver.get(url)
        _sleep(1.0, 1.8)
        # Facebook: ページフォローボタン (2025現行UI)
        btn = None
        for sel in [
            # data-testid
            "[data-testid='follow-button']",
            # aria-label
            "[aria-label='Follow']",
            "[aria-label='フォロー']",
            # role=button のspan
            "//div[@role='button'][contains(.,'Follow') and not(contains(.,'Following'))]",
            "//div[@role='button'][contains(.,'フォロー') and not(contains(.,'フォロー中'))]",
            # span内テキスト
            "//span[normalize-space()='Follow']/../..",
            "//span[normalize-space()='フォロー']/../..",
        ]:
            try:
                by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
                el = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((by, sel)))
                txt = el.text or el.get_attribute("aria-label") or ""
                if "following" in txt.lower() or "フォロー中" in txt:
                    continue
                btn = el
                break
            except Exception:
                continue
        btn.click()
        return True
    except Exception as e:
        print(f"[FB/page_follow] {e}"); return False

def fb_post_like(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: 投稿URL"""
    if not _fb_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[aria-label='Like'], [aria-label='いいね！']")))
        btn.click()
        return True
    except Exception as e:
        print(f"[FB/post_like] {e}"); return False

def fb_video_view(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: 動画URL"""
    if not _fb_login(driver, identity, proxy_dict, captcha_api_key): return False
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        time.sleep(20)
        return True
    except Exception as e:
        print(f"[FB/video_view] {e}"); return False

def fb_story_view(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: ユーザー/ページURL"""
    if not _fb_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target if target.startswith("http") else f"https://www.facebook.com/{target}")
        _sleep(1.0, 1.8)
        story = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[aria-label*='story'], [data-testid='story-ring']")))
        story.click()
        _sleep(0.6, 1.0)
        return True
    except Exception as e:
        print(f"[FB/story_view] {e}"); return False

def fb_group_join(driver, target, identity, proxy_dict, captcha_api_key) -> bool:
    """target: グループURL"""
    if not _fb_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        url = target if target.startswith("http") else f"https://www.facebook.com/groups/{target}"
        driver.get(url)
        _sleep(1.0, 1.8)
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[text()='Join group' or text()='グループに参加']/..")))
        btn.click()
        return True
    except Exception as e:
        print(f"[FB/group_join] {e}"); return False

def fb_comment(driver, target, identity, proxy_dict, captcha_api_key, comment_text="") -> bool:
    """target: 投稿URL"""
    if not _fb_login(driver, identity, proxy_dict, captcha_api_key): return False
    wait = _wait(driver)
    try:
        driver.get(target)
        _sleep(1.0, 1.8)
        box = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[aria-label*='comment'], [data-testid='UFI2CommentInputContainer'] [contenteditable]")))
        box.click()
        for ch in (comment_text or "🔥"):
            box.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.13))
        from selenium.webdriver.common.keys import Keys
        box.send_keys(Keys.RETURN)
        return True
    except Exception as e:
        print(f"[FB/comment] {e}"); return False


# ════════════════════════════════════════════════════════════════════════════════
# アクションマップ
# ════════════════════════════════════════════════════════════════════════════════

ACTION_MAP: dict[str, dict[str, dict]] = {
    "instagram": {
        "フォロワー":       {"fn": ig_follow,       "target": "username",  "label": "ターゲット @ユーザー名"},
        "いいね":           {"fn": ig_like,         "target": "url",       "label": "投稿URL"},
        "コメント":         {"fn": ig_comment,      "target": "url",       "label": "投稿URL", "has_text": True},
        "動画再生":         {"fn": ig_view_video,   "target": "url",       "label": "動画URL"},
        "ライブ視聴者":     {"fn": ig_live_view,    "target": "username",  "label": "ライブ配信中の @ユーザー名"},
        "インプレッション": {"fn": ig_impression,   "target": "url",       "label": "投稿/プロフURL"},
        "Reels再生":        {"fn": ig_reels_view,   "target": "url",       "label": "Reels URL"},
    },
    "x": {
        "フォロワー":       {"fn": x_follow,        "target": "username",  "label": "ターゲット @ユーザー名"},
        "いいね":           {"fn": x_like,          "target": "url",       "label": "ツイートURL"},
        "リポスト":         {"fn": x_repost,        "target": "url",       "label": "ツイートURL"},
        "リプライ":         {"fn": x_reply,         "target": "url",       "label": "ツイートURL", "has_text": True},
        "インプレッション": {"fn": x_impression,    "target": "url",       "label": "ツイートURL or @ユーザー名"},
        "動画再生":         {"fn": x_view_video,    "target": "url",       "label": "動画ツイートURL"},
        "投票":             {"fn": x_poll_vote,     "target": "url",       "label": "投票ツイートURL"},
    },
    "youtube": {
        "チャンネル登録者": {"fn": yt_subscribe,    "target": "url",       "label": "チャンネルURL or @handle"},
        "動画再生":         {"fn": yt_view,         "target": "url",       "label": "動画URL"},
        "いいね":           {"fn": yt_like,         "target": "url",       "label": "動画URL"},
        "ライブ視聴者":     {"fn": yt_live_view,    "target": "url",       "label": "ライブURL"},
        "Shorts再生":       {"fn": yt_shorts_view,  "target": "url",       "label": "Shorts URL"},
        "コメント":         {"fn": yt_comment,      "target": "url",       "label": "動画URL", "has_text": True},
    },
    "tiktok": {
        "フォロワー":       {"fn": tt_follow,       "target": "username",  "label": "ターゲット @ユーザー名"},
        "いいね":           {"fn": tt_like,         "target": "url",       "label": "動画URL"},
        "再生":             {"fn": tt_view,         "target": "url",       "label": "動画URL"},
        "コメント":         {"fn": tt_comment,      "target": "url",       "label": "動画URL", "has_text": True},
        "シェア":           {"fn": tt_share,        "target": "url",       "label": "動画URL"},
        "ライブ視聴者":     {"fn": tt_live_view,    "target": "username",  "label": "ライブ配信中の @ユーザー名"},
    },
    "facebook": {
        "ページフォロワー": {"fn": fb_page_follow,  "target": "url",       "label": "ページURL"},
        "投稿いいね":       {"fn": fb_post_like,    "target": "url",       "label": "投稿URL"},
        "動画再生":         {"fn": fb_video_view,   "target": "url",       "label": "動画URL"},
        "ストーリー閲覧":   {"fn": fb_story_view,   "target": "url",       "label": "ユーザー/ページURL"},
        "グループ参加":     {"fn": fb_group_join,   "target": "url",       "label": "グループURL"},
        "コメント":         {"fn": fb_comment,      "target": "url",       "label": "投稿URL", "has_text": True},
    },
    "note": {
        "フォロワー": {"fn": note_follow, "target": "username", "label": "ターゲット @urlname"},
        "いいね":     {"fn": note_like,   "target": "url",      "label": "記事URL または note_key"},
    },
}
