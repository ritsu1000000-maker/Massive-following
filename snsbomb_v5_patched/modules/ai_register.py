"""
modules/ai_register.py
AIVision を使った汎用登録フロー。
セレクターに頼らず画面を「見て」操作するため、UI変更に強い。

使い方:
    from modules.ai_register import AIRegister
    ok = AIRegister(driver, platform="instagram",
                    identity=identity, mail=mail,
                    api_key="sk-ant-...").run()
"""
from __future__ import annotations

import time
import random
from datetime import datetime

from modules.ai_vision import AIVision
from modules.captcha_solver import CaptchaSolver


MAX_STEPS   = 20   # 無限ループ防止
STEP_DELAY  = (1.2, 2.5)


class AIRegister:
    """
    登録フローをステートマシンとして実行。
    各ステップで AIVision.detect_stage() を呼んで現在地を確認し、
    対応するハンドラを実行して次のステップへ進む。
    """

    def __init__(self, driver, platform: str, identity: dict,
                 mail,  # TempMail インスタンス
                 api_key: str | None = None,
                 captcha_api_key: str = "",
                 debug: bool = False):
        self.driver          = driver
        self.platform        = platform
        self.identity        = identity
        self.mail            = mail
        self.captcha_api_key = captcha_api_key
        self.debug           = debug
        self.vision          = AIVision(driver, api_key=api_key, debug=debug)

    # ── メインループ ──────────────────────────────────────────────────────────

    def run(self) -> bool:
        """
        登録フローを最大 MAX_STEPS ステップ実行。
        home ステージ (ログイン済み) に到達したら True を返す。
        """
        print(f"[AIRegister] {self.platform} 登録開始 → {self.identity['username']}")
        self.vision.dismiss_dialog()

        for step in range(1, MAX_STEPS + 1):
            stage = self.vision.detect_stage(self.platform)
            print(f"[AIRegister] step={step} stage={stage}")

            if stage == "home":
                print(f"[AIRegister] ✓ 登録完了 / ログイン済み確認")
                return True

            if stage == "error":
                err = self.vision.get_error()
                print(f"[AIRegister] ✗ エラー検出: {err}")
                return False

            handler = {
                "email_form":         self._handle_email_form,
                "dob_form":           self._handle_dob,
                "captcha":            self._handle_captcha,
                "email_verify_code":  self._handle_verify_code,
                "email_verify_url":   self._handle_verify_url,
                "phone_verify":       self._handle_phone_verify,
                "unknown":            self._handle_unknown,
            }.get(stage)

            if handler:
                ok = handler()
                if not ok:
                    print(f"[AIRegister] ✗ ステップ失敗 (stage={stage})")
                    return False
            else:
                print(f"[AIRegister] 未知ステージ: {stage}")
                return False

            # ダイアログが出たら消す
            self.vision.dismiss_dialog()
            time.sleep(random.uniform(*STEP_DELAY))

        print(f"[AIRegister] MAX_STEPS ({MAX_STEPS}) 到達 — 登録未完了")
        return False

    # ── ステージハンドラ ──────────────────────────────────────────────────────

    def _handle_email_form(self) -> bool:
        """メール・名前・パスワードフォームを入力して送信。"""
        v = self.vision

        # プラットフォームによってフィールドの組み合わせが違う
        fields = _form_fields(self.platform, self.identity)
        for description, text in fields:
            ok = v.fill_field(description, text)
            if not ok:
                print(f"[AIRegister/email_form] フィールド未検出: {description}")
                # 致命的ではない — 次のフィールドへ
            time.sleep(random.uniform(0.3, 0.7))

        time.sleep(random.uniform(0.5, 1.0))
        return v.click_element("登録/次へ/Sign up/Next ボタン")

    def _handle_dob(self) -> bool:
        """生年月日を入力して次へ。"""
        v = self.vision
        dob = self.identity["dob"].split("-")  # YYYY-MM-DD
        month, day, year = dob[1], dob[2], dob[0]

        # まず select 要素を JS で直接設定 (Vision より速い)
        filled = _js_fill_dob(self.driver, month, day, year)
        if not filled:
            # フォールバック: Vision でクリック
            v.fill_field("月 (Month) ドロップダウン", month)
            v.fill_field("日 (Day) ドロップダウン", day)
            v.fill_field("年 (Year) ドロップダウン", year)

        time.sleep(0.5)
        return v.click_element("次へ/Next/進む ボタン")

    def _handle_captcha(self) -> bool:
        """既存の CaptchaSolver で解く。Vision は captcha 内部は見ない。"""
        solver = CaptchaSolver(
            self.driver, api_key=self.captcha_api_key
        )
        solved = solver.solve()
        if not solved:
            # 解けなかったが、次のステップに進んでいる可能性もある
            print("[AIRegister/captcha] 解決失敗 — 継続試行")
        return True  # captcha 失敗でも続ける (detect_stage で判断)

    def _handle_verify_code(self) -> bool:
        """メールから確認コードを取得して入力。"""
        print("[AIRegister/verify_code] メールを待機中 …")
        for keyword in ["verify", "confirm", "code", "TikTok", "Instagram",
                         "Twitter", "Facebook", "YouTube"]:
            body = self.mail.wait_for_verification(keyword=keyword, timeout=60)
            if body:
                break
        else:
            body = None

        if not body:
            print("[AIRegister/verify_code] メール未着")
            return False

        value = self.vision.extract_code_from_text(body)
        if not value:
            print("[AIRegister/verify_code] コード抽出失敗")
            return False

        print(f"[AIRegister/verify_code] 取得: {value[:6]}…")

        # URL形式なら遷移
        if value.startswith("http"):
            self.driver.get(value)
            return True

        # 数字コードなら入力フィールドへ
        ok = self.vision.fill_field("確認コード (数字) 入力欄", value)
        if ok:
            time.sleep(0.5)
            self.vision.click_element("確認/送信/Confirm/Next ボタン")
        return ok

    def _handle_verify_url(self) -> bool:
        """「メールのリンクをクリックして」画面 — メールからURLを取得して遷移。"""
        return self._handle_verify_code()  # 同じ処理で対応できる

    def _handle_phone_verify(self) -> bool:
        """電話番号認証要求 — 現時点では回避不可 → False を返してスキップ。"""
        print("[AIRegister/phone_verify] 電話番号認証が要求されました")
        print("  → SMS API (sms-activate.org 等) の統合が必要です")
        print("  → config.json に sms_api_key を設定してください")
        return False

    def _handle_unknown(self) -> bool:
        """未知ステージ — スクロールして再判定を試みる。"""
        self.driver.execute_script("window.scrollTo(0, 300)")
        time.sleep(1)
        return True  # ループに戻して再判定


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def _form_fields(platform: str, identity: dict) -> list[tuple[str, str]]:
    """プラットフォームごとのフォームフィールド定義。"""
    base = [
        ("メールアドレス入力欄", identity.get("email", "")),
        ("フルネーム・氏名入力欄", identity.get("full_name", "")),
        ("ユーザー名入力欄", identity.get("username", "")),
        ("パスワード入力欄", identity.get("password", "")),
    ]
    overrides = {
        "youtube": [
            ("名前 (First name) 入力欄", identity.get("first_name", "")),
            ("苗字 (Last name) 入力欄", identity.get("last_name", "")),
            ("ユーザー名入力欄", identity.get("username", "")),
            ("パスワード入力欄", identity.get("password", "")),
            ("パスワード確認入力欄", identity.get("password", "")),
        ],
        "facebook": [
            ("名前 (First name) 入力欄", identity.get("first_name", "")),
            ("苗字 (Last name) 入力欄", identity.get("last_name", "")),
            ("メールアドレス入力欄", identity.get("email", "")),
            ("パスワード入力欄", identity.get("password", "")),
        ],
    }
    return overrides.get(platform, base)


def _js_fill_dob(driver, month: str, day: str, year: str) -> bool:
    """JS で select 要素を直接埋める。成功したら True。"""
    script = """
    var selectors = [
        {month: "select[title='Month']",  day: "select[title='Day']",  year: "select[title='Year']"},
        {month: "select[aria-label*='Month' i]", day: "select[aria-label*='Day' i]", year: "select[aria-label*='Year' i]"},
        {month: "#month", day: "#day", year: "#year"},
        {month: "select[name='month']", day: "select[name='day']", year: "select[name='year']"},
        {month: "select[data-testid*='month' i]", day: "select[data-testid*='day' i]", year: "select[data-testid*='year' i]"},
    ];
    for (var s of selectors) {
        var m = document.querySelector(s.month);
        var d = document.querySelector(s.day);
        var y = document.querySelector(s.year);
        if (m && d && y) {
            m.value = arguments[0];
            d.value = arguments[1];
            y.value = arguments[2];
            [m, d, y].forEach(function(el) {
                el.dispatchEvent(new Event('change', {bubbles:true}));
            });
            return true;
        }
    }
    return false;
    """
    try:
        return bool(driver.execute_script(script, str(int(month)), str(int(day)), year))
    except Exception:
        return False
