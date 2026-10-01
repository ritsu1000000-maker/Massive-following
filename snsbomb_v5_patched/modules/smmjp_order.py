"""
modules/smmjp_order.py
smmjp.com Selenium注文エンジン — APIなし完全ブラウザ操作版

フロー:
  1. ログイン (Cookie再利用 or ID/PW入力)
  2. https://smmjp.com/ (新規注文フォーム)
     - サービスID選択 → リンク(URL)入力 → 数量入力 → 注文ボタン
  3. 結果確認 → 成功/失敗を返す

外部依存: selenium, undetected_chromedriver (browser.py と共通)

使い方:
    engine = SmmjpOrderEngine(driver, email, password)
    engine.login()
    results = engine.order_all(services_list, target_url, qty_ratio=1.0)
"""
from __future__ import annotations

import re
import time
import json
import random
import pathlib
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException


# ── URL定数 ──────────────────────────────────────────────────────────────────
_BASE          = "https://smmjp.com"
_LOGIN_URL     = f"{_BASE}/login"
_ORDER_URL     = f"{_BASE}/"           # 新規注文フォームはトップ
_ORDERS_URL    = f"{_BASE}/orders"
_MASSORDER_URL = f"{_BASE}/massorder"

# ── ログイン確認セレクタ ──────────────────────────────────────────────────────
_LOGGED_IN_SELS = [
    (By.CSS_SELECTOR, "a[href='/orders']"),
    (By.CSS_SELECTOR, "a[href='/addfunds']"),
    (By.CSS_SELECTOR, ".component-navbar-private-nav-item"),
    (By.CSS_SELECTOR, "a[href='/logout']"),
]

# ── 注文フォームセレクタ群 ────────────────────────────────────────────────────
# smmjp.com の新規注文フォームは Select2 / select + input の組み合わせ
_SERVICE_SELECT_SELS = [
    "select[name='service']",
    "select#service",
    "#order-service",
    "select.service-select",
    "[name='service_id']",
]
_LINK_INPUT_SELS = [
    "input[name='link']",
    "input[name='url']",
    "#order-link",
    "input[placeholder*='URL']",
    "input[placeholder*='リンク']",
    "input[placeholder*='link']",
]
_QTY_INPUT_SELS = [
    "input[name='quantity']",
    "input[name='qty']",
    "#order-quantity",
    "input[type='number'][min]",
    "input[placeholder*='数量']",
    "input[placeholder*='Quantity']",
]
_SUBMIT_SELS = [
    "button[type='submit']",
    "input[type='submit']",
    "//button[contains(.,'注文')]",
    "//button[contains(.,'Order')]",
    "//button[contains(.,'Submit')]",
    "//input[@type='submit']",
]

# ── マスオーダーフォームセレクタ ──────────────────────────────────────────────
_MASS_TEXTAREA_SELS = [
    "textarea[name='orders']",
    "textarea#orders",
    "textarea.mass-order",
    "textarea",
]

# スクリーンショット保存先
_SS_DIR = pathlib.Path("data/smmjp_ss")


def _ss(driver, tag: str) -> None:
    try:
        _SS_DIR.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(_SS_DIR / f"{tag}_{int(time.time())}.png"))
    except Exception:
        pass


def _wait_click(driver, selectors: list[str], timeout: int = 10) -> bool:
    for sel in selectors:
        try:
            by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
            el = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, sel))
            )
            el.click()
            return True
        except Exception:
            continue
    return False


def _find_el(driver, selectors: list[str], timeout: int = 10):
    for sel in selectors:
        try:
            by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
            return WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, sel))
            )
        except Exception:
            continue
    return None


def _type_clear(el, text: str, slow: bool = False) -> None:
    el.click()
    el.send_keys(Keys.CONTROL, "a")
    el.send_keys(Keys.DELETE)
    if slow:
        for ch in str(text):
            el.send_keys(ch)
            time.sleep(random.uniform(0.03, 0.08))
    else:
        el.send_keys(str(text))
    time.sleep(random.uniform(0.1, 0.3))


# ════════════════════════════════════════════════════════════════════════════════
class SmmjpOrderEngine:
    """
    smmjp.com ブラウザ注文エンジン。

    Parameters
    ----------
    driver      : selenium WebDriver (browser.py の make_driver() 推奨)
    email       : ログインメールアドレス
    password    : ログインパスワード
    cookie_file : Cookie永続化ファイルパス (省略可)
    headless    : デバッグ用
    """

    def __init__(
        self,
        driver,
        email: str,
        password: str,
        cookie_file: str = "data/smmjp_cookies.json",
        headless: bool = True,
    ):
        self.driver      = driver
        self.email       = email
        self.password    = password
        self.cookie_file = pathlib.Path(cookie_file)
        self.headless    = headless
        self._logged_in  = False

    # ── ログイン ─────────────────────────────────────────────────────────────

    def login(self) -> bool:
        """Cookie再利用 → 失敗時はID/PW入力でログイン。成功でTrue。"""
        # Cookie試行
        if self._restore_cookies():
            print("[smmjp] ✓ Cookie ログイン成功")
            self._logged_in = True
            return True

        print("[smmjp] Cookie 切れ / 初回 → ID/PW でログイン")
        return self._login_form()

    def _restore_cookies(self) -> bool:
        if not self.cookie_file.exists():
            return False
        try:
            cookies = json.loads(self.cookie_file.read_text(encoding="utf-8"))
            self.driver.get(_BASE)
            time.sleep(1.5)
            self.driver.delete_all_cookies()
            for ck in cookies:
                ck_clean = {k: v for k, v in ck.items()
                            if k in ("name","value","domain","path","secure","httpOnly","expiry","sameSite")}
                try:
                    self.driver.add_cookie(ck_clean)
                except Exception:
                    pass
            self.driver.refresh()
            time.sleep(2)
            return self._is_logged_in()
        except Exception as e:
            print(f"[smmjp] cookie restore error: {e}")
            return False

    def _login_form(self) -> bool:
        try:
            self.driver.get(_LOGIN_URL)
            time.sleep(random.uniform(1.5, 2.5))
            _ss(self.driver, "login_01")

            # メール入力
            email_sels = [
                "input[name='email']",
                "input[type='email']",
                "input[name='username']",
                "//input[@placeholder='Email']",
                "//input[@placeholder='メールアドレス']",
            ]
            el_email = _find_el(self.driver, email_sels, timeout=15)
            if not el_email:
                print("[smmjp] ✗ email フィールドが見つからない")
                _ss(self.driver, "login_ERR_email")
                return False
            _type_clear(el_email, self.email)

            # パスワード入力
            pw_sels = [
                "input[name='password']",
                "input[type='password']",
                "//input[@placeholder='Password']",
                "//input[@placeholder='パスワード']",
            ]
            el_pw = _find_el(self.driver, pw_sels, timeout=8)
            if not el_pw:
                print("[smmjp] ✗ password フィールドが見つからない")
                _ss(self.driver, "login_ERR_pw")
                return False
            _type_clear(el_pw, self.password)

            # サブミット
            submit_sels = [
                "button[type='submit']",
                "input[type='submit']",
                "//button[contains(.,'ログイン')]",
                "//button[contains(.,'Login')]",
                "//button[contains(.,'Sign in')]",
            ]
            _wait_click(self.driver, submit_sels, timeout=8)
            time.sleep(random.uniform(2.5, 4.0))
            _ss(self.driver, "login_02_after_submit")

            if self._is_logged_in():
                # Cookie保存
                self._save_cookies()
                self._logged_in = True
                print("[smmjp] ✓ ID/PW ログイン成功")
                return True

            print("[smmjp] ✗ ログイン失敗 (セレクタ未検出)")
            _ss(self.driver, "login_ERR_check")
            return False

        except Exception as e:
            print(f"[smmjp] login error: {e}")
            _ss(self.driver, "login_ERR")
            return False

    def _is_logged_in(self) -> bool:
        for by, sel in _LOGGED_IN_SELS:
            try:
                self.driver.find_element(by, sel)
                return True
            except NoSuchElementException:
                pass
        return False

    def _save_cookies(self) -> None:
        try:
            self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
            self.cookie_file.write_text(
                json.dumps(self.driver.get_cookies(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[smmjp] cookie save error: {e}")

    # ── 単体注文 ─────────────────────────────────────────────────────────────

    def order_single(
        self,
        service_id: int,
        target_url: str,
        quantity: int,
    ) -> bool:
        """
        新規注文フォームで1件注文。
        成功: True / 失敗: False
        """
        if not self._logged_in:
            print("[smmjp] 未ログイン — login() を先に呼んでください")
            return False

        tag = f"order_{service_id}"
        try:
            self.driver.get(_ORDER_URL)
            time.sleep(random.uniform(2.0, 3.5))
            _ss(self.driver, f"{tag}_01_form")

            # ── サービス選択 ─────────────────────────────────────────────
            if not self._select_service(service_id):
                print(f"[smmjp] ✗ サービス選択失敗 id={service_id}")
                _ss(self.driver, f"{tag}_ERR_service")
                return False
            time.sleep(random.uniform(0.8, 1.5))
            _ss(self.driver, f"{tag}_02_service_selected")

            # ── リンク入力 ───────────────────────────────────────────────
            el_link = _find_el(self.driver, _LINK_INPUT_SELS, timeout=10)
            if not el_link:
                print(f"[smmjp] ✗ リンク入力欄が見つからない id={service_id}")
                _ss(self.driver, f"{tag}_ERR_link")
                return False
            _type_clear(el_link, target_url)
            _ss(self.driver, f"{tag}_03_link")

            # ── 数量入力 ─────────────────────────────────────────────────
            el_qty = _find_el(self.driver, _QTY_INPUT_SELS, timeout=10)
            if not el_qty:
                print(f"[smmjp] ✗ 数量入力欄が見つからない id={service_id}")
                _ss(self.driver, f"{tag}_ERR_qty")
                return False
            _type_clear(el_qty, str(quantity))
            _ss(self.driver, f"{tag}_04_qty")

            # ── 注文ボタン ───────────────────────────────────────────────
            if not _wait_click(self.driver, _SUBMIT_SELS, timeout=8):
                print(f"[smmjp] ✗ 注文ボタンが見つからない id={service_id}")
                _ss(self.driver, f"{tag}_ERR_submit")
                return False

            time.sleep(random.uniform(2.0, 4.0))
            _ss(self.driver, f"{tag}_05_after_submit")

            # ── 成功確認 ─────────────────────────────────────────────────
            success = self._check_order_success()
            if success:
                print(f"[smmjp] ✓ 注文成功 id={service_id} qty={quantity}")
            else:
                print(f"[smmjp] ✗ 注文失敗 / 要確認 id={service_id}")
                _ss(self.driver, f"{tag}_ERR_result")
            return success

        except Exception as e:
            print(f"[smmjp] order_single error id={service_id}: {e}")
            _ss(self.driver, f"{tag}_ERR_exception")
            return False

    def _select_service(self, service_id: int) -> bool:
        """
        サービスIDを選択。
        Select要素 → Select2 JS → 直接input の順に試みる。
        """
        # A: 通常の <select> 要素
        for sel in _SERVICE_SELECT_SELS:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, sel)
                Select(el).select_by_value(str(service_id))
                return True
            except Exception:
                continue

        # B: Select2 スタイル (hidden select + .select2-container)
        # まず hidden select に JS で値を設定してイベントを発火させる
        try:
            result = self.driver.execute_script(f"""
                var selects = document.querySelectorAll('select');
                for (var s of selects) {{
                    var opt = s.querySelector('option[value="{service_id}"]');
                    if (opt) {{
                        s.value = "{service_id}";
                        s.dispatchEvent(new Event('change', {{bubbles: true}}));
                        // Select2 refresh
                        if (typeof $ !== 'undefined' && $(s).data('select2')) {{
                            $(s).trigger('change');
                        }}
                        return true;
                    }}
                }}
                return false;
            """)
            if result:
                return True
        except Exception:
            pass

        # C: Select2 — 検索ボックスに ID を打ち込む
        try:
            s2_box = self.driver.find_element(
                By.CSS_SELECTOR, ".select2-selection, .select2-container"
            )
            s2_box.click()
            time.sleep(0.5)
            search_input = self.driver.find_element(
                By.CSS_SELECTOR, ".select2-search__field, .select2-search input"
            )
            _type_clear(search_input, str(service_id))
            time.sleep(0.8)
            # 最初の候補を選択
            opt = self.driver.find_element(
                By.CSS_SELECTOR, ".select2-results__option--highlighted, .select2-results__option"
            )
            opt.click()
            return True
        except Exception:
            pass

        return False

    def _check_order_success(self) -> bool:
        """注文後のページで成功メッセージを確認。"""
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            success_kw = [
                "注文が完了", "order placed", "success", "正常に", "受け付け",
                "ご注文", "submitted", "completed",
            ]
            fail_kw = ["error", "エラー", "failed", "失敗", "不足", "invalid"]

            for kw in fail_kw:
                if kw in body_text:
                    return False
            for kw in success_kw:
                if kw in body_text:
                    return True

            # 注文履歴ページにリダイレクトされていれば成功とみなす
            if "/orders" in self.driver.current_url:
                return True

            # アラート確認
            try:
                alert = self.driver.switch_to.alert
                text = alert.text.lower()
                alert.accept()
                return any(kw in text for kw in ["success","完了","注文"])
            except Exception:
                pass

            return False
        except Exception:
            return False

    # ── マスオーダー ─────────────────────────────────────────────────────────

    def mass_order(
        self,
        orders: list[dict],  # [{"service_id": int, "link": str, "quantity": int}, ...]
    ) -> dict:
        """
        /massorder エンドポイントで一括注文。
        フォーマット: service_id|link|quantity を1行ずつ textarea に貼り付け。

        Returns: {"submitted": N, "success": bool}
        """
        if not self._logged_in:
            return {"submitted": 0, "success": False}

        lines = [f"{o['service_id']}|{o['link']}|{o['quantity']}" for o in orders]
        payload = "\n".join(lines)

        try:
            self.driver.get(_MASSORDER_URL)
            time.sleep(random.uniform(2.0, 3.5))
            _ss(self.driver, "massorder_01")

            ta = _find_el(self.driver, _MASS_TEXTAREA_SELS, timeout=12)
            if not ta:
                print("[smmjp/mass] ✗ textareaが見つからない")
                _ss(self.driver, "massorder_ERR_ta")
                return {"submitted": 0, "success": False}

            _type_clear(ta, payload)
            _ss(self.driver, "massorder_02_payload")

            if not _wait_click(self.driver, _SUBMIT_SELS, timeout=8):
                print("[smmjp/mass] ✗ submit ボタンが見つからない")
                _ss(self.driver, "massorder_ERR_submit")
                return {"submitted": 0, "success": False}

            time.sleep(random.uniform(3.0, 5.0))
            _ss(self.driver, "massorder_03_result")

            success = self._check_order_success()
            print(f"[smmjp/mass] {'✓' if success else '✗'} {len(orders)}件一括注文")
            return {"submitted": len(orders), "success": success}

        except Exception as e:
            print(f"[smmjp/mass] error: {e}")
            _ss(self.driver, "massorder_ERR")
            return {"submitted": 0, "success": False}

    # ── 全サービス一括注文 ───────────────────────────────────────────────────

    def order_all(
        self,
        services: list[dict],
        target_url: str,
        qty_ratio: float = 1.0,
        use_mass: bool = True,
        mass_chunk: int = 100,
        delay_range: tuple[float, float] = (2.0, 5.0),
        skip_ids: set[int] | None = None,
        platform_filter: list[str] | None = None,
        category_filter: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """
        services リストの全サービスを target_url に対して注文する。

        Parameters
        ----------
        services         : services_clean.json から読んだリスト
        target_url       : 注文先URL (例: https://instagram.com/username)
        qty_ratio        : 最小注文数に対する倍率 (1.0 = min, 2.0 = min*2)
        use_mass         : True = /massorder 一括、False = 1件ずつ
        mass_chunk       : 一括注文の1回あたり最大件数
        delay_range      : 注文間のスリープ秒レンジ
        skip_ids         : スキップするサービスIDセット
        platform_filter  : ["instagram","twitter"] など。Noneで全プラットフォーム
        category_filter  : カテゴリ名の部分一致リスト。Noneで全カテゴリ
        dry_run          : Trueでブラウザ操作なし (注文リストを返すだけ)
        """
        if not self._logged_in and not dry_run:
            print("[smmjp] 未ログイン")
            return {"success": 0, "fail": 0, "skipped": 0, "total": 0}

        skip_ids = skip_ids or set()

        # フィルタリング
        filtered = []
        for s in services:
            if s["id"] in skip_ids:
                continue
            if platform_filter and s["platform"] not in platform_filter:
                continue
            if category_filter:
                if not any(cf in s["category"] for cf in category_filter):
                    continue
            try:
                min_q = int(s["min"])
            except Exception:
                min_q = 10
            qty = max(min_q, int(min_q * qty_ratio))
            filtered.append({
                "service_id": s["id"],
                "name":       s["name"],
                "link":       target_url,
                "quantity":   qty,
                "platform":   s["platform"],
            })

        print(f"[smmjp] 対象サービス: {len(filtered)}件 (全{len(services)}件中)")

        if dry_run:
            print("[smmjp] DRY RUN — 以下を注文予定:")
            for o in filtered[:10]:
                print(f"  id={o['service_id']} qty={o['quantity']} {o['name'][:50]}")
            if len(filtered) > 10:
                print(f"  ... 他{len(filtered)-10}件")
            return {"success": 0, "fail": 0, "skipped": 0, "total": len(filtered),
                    "orders": filtered}

        success_count = 0
        fail_count    = 0

        if use_mass:
            # ── マスオーダー (chunk単位) ──────────────────────────────────
            chunks = [filtered[i:i+mass_chunk]
                      for i in range(0, len(filtered), mass_chunk)]
            for i, chunk in enumerate(chunks, 1):
                print(f"[smmjp/mass] chunk {i}/{len(chunks)} ({len(chunk)}件)")
                result = self.mass_order(chunk)
                if result["success"]:
                    success_count += len(chunk)
                else:
                    fail_count += len(chunk)
                # 次のchunkまで待機
                if i < len(chunks):
                    sleep = random.uniform(*delay_range)
                    print(f"[smmjp] sleeping {sleep:.1f}s")
                    time.sleep(sleep)
        else:
            # ── 1件ずつ ───────────────────────────────────────────────────
            for i, order in enumerate(filtered, 1):
                print(f"[smmjp] {i}/{len(filtered)} id={order['service_id']} qty={order['quantity']}")
                ok = self.order_single(
                    service_id=order["service_id"],
                    target_url=order["link"],
                    quantity=order["quantity"],
                )
                if ok:
                    success_count += 1
                else:
                    fail_count += 1

                if i < len(filtered):
                    sleep = random.uniform(*delay_range)
                    print(f"[smmjp] sleeping {sleep:.1f}s")
                    time.sleep(sleep)

        print(f"\n[smmjp] ── Done ── success={success_count} fail={fail_count} total={len(filtered)}")
        return {
            "success":  success_count,
            "fail":     fail_count,
            "skipped":  len(services) - len(filtered),
            "total":    len(filtered),
        }
