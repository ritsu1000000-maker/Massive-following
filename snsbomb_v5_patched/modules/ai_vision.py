"""
modules/ai_vision.py
Claude Vision API を使ったUI操作・判定モジュール。

できること:
  1. スクリーンショットを見て「次に何をすべきか」を指示する
  2. スクリーンショットから対象要素のXY座標を返す
  3. メール本文から確認コード / URLを抽出する
  4. 現在画面が「ログイン済み状態」かを判定する
  5. Captcha / エラーダイアログ / Cookie同意バナー等を検出する

必要なもの:
  pip install anthropic
  ANTHROPIC_API_KEY 環境変数 (またはconfig.jsonに "anthropic_api_key": "sk-ant-...")
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import io
from typing import Any

try:
    import anthropic
except ImportError:
    raise ImportError("pip install anthropic が必要です")


# ── クライアント ──────────────────────────────────────────────────────────────

def _client(api_key: str | None = None) -> anthropic.Anthropic:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY が未設定。環境変数か config.json の "
            "anthropic_api_key に設定してください。"
        )
    return anthropic.Anthropic(api_key=key)


# ── スクリーンショット取得 ────────────────────────────────────────────────────

def _screenshot_b64(driver) -> str:
    """ドライバからPNG取得してbase64返す。"""
    png = driver.get_screenshot_as_png()
    return base64.standard_b64encode(png).decode()


def _ask_vision(client: anthropic.Anthropic, b64: str, prompt: str,
                max_tokens: int = 512) -> str:
    """Vision APIに画像+プロンプトを投げてテキスト回答を返す。"""
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return msg.content[0].text.strip()


# ════════════════════════════════════════════════════════════════════════════════
# 公開API
# ════════════════════════════════════════════════════════════════════════════════

class AIVision:
    """
    使い方:
        vision = AIVision(driver, api_key="sk-ant-...")
        ok = vision.click_element("Followボタン")
        logged_in = vision.is_logged_in("instagram")
        code = vision.extract_code_from_text(mail_body)
    """

    def __init__(self, driver, api_key: str | None = None,
                 debug: bool = False):
        self.driver    = driver
        self.client    = _client(api_key)
        self.debug     = debug
        self._w: int   = driver.execute_script("return window.innerWidth")
        self._h: int   = driver.execute_script("return window.innerHeight")

    # ── 1. 要素クリック (座標ベース) ─────────────────────────────────────────

    def click_element(self, description: str, retries: int = 3) -> bool:
        """
        画面上の `description` に合致する要素を探してクリック。
        例: vision.click_element("Followボタン")
             vision.click_element("青いサインアップボタン")
        """
        for attempt in range(retries):
            b64 = _screenshot_b64(self.driver)
            prompt = (
                f"この画面に「{description}」に最も近い要素はありますか？\n"
                f"ある場合は中心のピクセル座標を JSON で返してください:\n"
                f'  {{"found": true, "x": <数値>, "y": <数値>, "confidence": <0-1>}}\n'
                f"ない場合:\n"
                f'  {{"found": false}}\n'
                f"JSON以外は絶対に出力しないでください。"
            )
            raw = _ask_vision(self.client, b64, prompt)
            if self.debug:
                print(f"[AIVision/click] raw: {raw}")

            try:
                data = json.loads(_strip_json(raw))
            except Exception:
                time.sleep(1)
                continue

            if not data.get("found"):
                if self.debug:
                    print(f"[AIVision/click] '{description}' not found (attempt {attempt+1})")
                time.sleep(1.5)
                continue

            x = int(data["x"])
            y = int(data["y"])
            conf = data.get("confidence", 1.0)

            if conf < 0.4:
                if self.debug:
                    print(f"[AIVision/click] low confidence {conf:.2f}, skipping")
                time.sleep(1)
                continue

            # JavaScript クリック (selenium のclick()より確実)
            self.driver.execute_script(
                "document.elementFromPoint(arguments[0], arguments[1]).click();",
                x, y
            )
            if self.debug:
                print(f"[AIVision/click] clicked '{description}' at ({x},{y}) conf={conf:.2f}")
            return True

        print(f"[AIVision/click] failed after {retries} retries: {description}")
        return False

    # ── 2. フィールド入力 ─────────────────────────────────────────────────────

    def fill_field(self, description: str, text: str, retries: int = 3) -> bool:
        """
        `description` に合致する入力フィールドを見つけてテキストを入力。
        例: vision.fill_field("メールアドレス入力欄", "user@example.com")
        """
        from selenium.webdriver.common.action_chains import ActionChains

        for attempt in range(retries):
            b64 = _screenshot_b64(self.driver)
            prompt = (
                f"この画面に「{description}」に最も近い入力フィールドはありますか？\n"
                f"ある場合はクリックすべき中心座標を JSON で:\n"
                f'  {{"found": true, "x": <数値>, "y": <数値>}}\n'
                f"ない場合: {{\"found\": false}}\n"
                f"JSON以外は出力しないでください。"
            )
            raw = _ask_vision(self.client, b64, prompt)
            if self.debug:
                print(f"[AIVision/fill] raw: {raw}")

            try:
                data = json.loads(_strip_json(raw))
            except Exception:
                time.sleep(1)
                continue

            if not data.get("found"):
                time.sleep(1.5)
                continue

            x, y = int(data["x"]), int(data["y"])

            # クリックしてフォーカス
            el = self.driver.execute_script(
                "return document.elementFromPoint(arguments[0], arguments[1]);", x, y
            )
            if el:
                self.driver.execute_script("arguments[0].click(); arguments[0].focus();", el)
                # JS setValue (React/Next.js 対応)
                self.driver.execute_script("""
                    var el = arguments[0]; var val = arguments[1];
                    var setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                """, el, text)

                # React が値を上書きする場合のフォールバック: ActionChains
                import time as t
                t.sleep(0.3)
                current = el.get_attribute("value") or ""
                if current != text:
                    el.clear()
                    ActionChains(self.driver).click(el).send_keys(text).perform()

                if self.debug:
                    print(f"[AIVision/fill] filled '{description}' at ({x},{y})")
                return True

        print(f"[AIVision/fill] failed: {description}")
        return False

    # ── 3. ログイン状態判定 ───────────────────────────────────────────────────

    def is_logged_in(self, platform: str) -> bool:
        """
        現在の画面を見て、そのプラットフォームにログインしているか判定。
        ログイン画面 / エラー画面 / ブロック画面を区別できる。
        """
        b64 = _screenshot_b64(self.driver)
        prompt = (
            f"この画面は {platform} のページです。\n"
            f"現在ユーザーがログイン済みかどうかを判断してください。\n"
            f"判断基準:\n"
            f"  - ログイン済み: ホームフィード・プロフィールアイコン・ナビゲーションバーが見える\n"
            f"  - 未ログイン: ログインフォーム・サインアップページ・「ログインが必要です」メッセージ\n"
            f"  - ブロック/BAN: エラーメッセージ・アカウント停止の通知\n"
            f"以下のJSONのみ返してください:\n"
            f'  {{"status": "logged_in" | "logged_out" | "blocked" | "unknown"}}\n'
            f"JSON以外は出力しないでください。"
        )
        raw = _ask_vision(self.client, b64, prompt)
        if self.debug:
            print(f"[AIVision/login_check] {raw}")
        try:
            data = json.loads(_strip_json(raw))
            status = data.get("status", "unknown")
            print(f"[AIVision] login status: {status}")
            return status == "logged_in"
        except Exception:
            return False

    # ── 4. 現在のステージ識別 ─────────────────────────────────────────────────

    def detect_stage(self, platform: str) -> str:
        """
        登録フロー中の現在のステージを識別。
        戻り値: "email_form" | "dob_form" | "captcha" | "email_verify_code" |
                "email_verify_url" | "phone_verify" | "home" | "error" | "unknown"
        """
        b64 = _screenshot_b64(self.driver)
        prompt = (
            f"これは {platform} の登録/ログインフローのスクリーンショットです。\n"
            f"現在どのステージかを以下から選んでください:\n"
            f"  - email_form      : メール・名前・パスワード入力フォームが表示されている\n"
            f"  - dob_form        : 生年月日入力フォームが表示されている\n"
            f"  - captcha         : reCAPTCHA・hCaptcha・スライダーパズルが表示されている\n"
            f"  - email_verify_code: メール確認コード(数字)の入力フォームが表示されている\n"
            f"  - email_verify_url : 「メールのリンクをクリック」と表示されている\n"
            f"  - phone_verify    : 電話番号確認が要求されている\n"
            f"  - home            : ホーム画面・フィード・ログイン済み状態\n"
            f"  - error           : エラーメッセージ・アカウント停止・BANの通知\n"
            f"  - unknown         : 上記のどれでもない\n"
            f"以下のJSONのみ返してください:\n"
            f'  {{"stage": "<上記のいずれか>", "detail": "<30文字以内の補足>"}}\n'
            f"JSON以外は出力しないでください。"
        )
        raw = _ask_vision(self.client, b64, prompt)
        if self.debug:
            print(f"[AIVision/stage] {raw}")
        try:
            data = json.loads(_strip_json(raw))
            stage = data.get("stage", "unknown")
            detail = data.get("detail", "")
            print(f"[AIVision] stage={stage} ({detail})")
            return stage
        except Exception:
            return "unknown"

    # ── 5. メール本文からコード抽出 ───────────────────────────────────────────

    def extract_code_from_text(self, mail_body: str) -> str | None:
        """
        メール本文から確認コードまたは確認URLを抽出。
        正規表現が死んでいるケース (HTML埋め込み・難読化) でも機能する。
        戻り値: コード文字列 or URL or None
        """
        # まず正規表現で高速試行
        # 6桁数字コード
        m = re.search(r'\b(\d{6})\b', mail_body)
        if m:
            return m.group(1)
        # https URL
        m = re.search(r'https?://\S+', mail_body)
        if m:
            url = m.group(0).rstrip('.,)"\'')
            return url

        # 正規表現で取れない場合 → テキストをAPIに渡す
        # HTMLタグを除去してから渡す (トークン節約)
        plain = re.sub(r'<[^>]+>', ' ', mail_body)
        plain = re.sub(r'\s+', ' ', plain).strip()[:2000]

        prompt = (
            f"以下はSNSサービスからの確認メール本文です。\n"
            f"確認コード (数字のみ) または確認URL を抽出してください。\n"
            f"以下のJSONのみ返してください:\n"
            f'  {{"type": "code" | "url" | "none", "value": "<コードまたはURL>"}}\n'
            f"JSONのみ。本文:\n\n{plain}"
        )
        msg = self.client.messages.create(
            model="claude-opus-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if self.debug:
            print(f"[AIVision/extract_code] {raw}")
        try:
            data = json.loads(_strip_json(raw))
            if data.get("type") in ("code", "url"):
                return data.get("value")
        except Exception:
            pass
        return None

    # ── 6. ダイアログ/バナー自動dismiss ──────────────────────────────────────

    def dismiss_dialog(self) -> bool:
        """
        Cookieバナー・年齢確認・「後で」「スキップ」ボタン等を自動検出して閉じる。
        何もなければ False を返す。
        """
        b64 = _screenshot_b64(self.driver)
        prompt = (
            "この画面に閉じるべきダイアログ・バナー・ポップアップ (Cookie同意・年齢確認・"
            "通知許可・「後で」ボタン等) がありますか？\n"
            "ある場合は閉じるためにクリックすべきボタンの座標:\n"
            '  {"found": true, "x": <数値>, "y": <数値>, "label": "<ボタンのテキスト>"}\n'
            "ない場合: {\"found\": false}\n"
            "JSONのみ返してください。"
        )
        raw = _ask_vision(self.client, b64, prompt)
        if self.debug:
            print(f"[AIVision/dismiss] {raw}")
        try:
            data = json.loads(_strip_json(raw))
            if data.get("found"):
                x, y = int(data["x"]), int(data["y"])
                self.driver.execute_script(
                    "document.elementFromPoint(arguments[0],arguments[1]).click();", x, y
                )
                print(f"[AIVision] dismissed dialog: {data.get('label','?')}")
                return True
        except Exception:
            pass
        return False

    # ── 7. エラー検出 ─────────────────────────────────────────────────────────

    def get_error(self) -> str | None:
        """
        画面にエラーメッセージがあれば返す。なければ None。
        """
        b64 = _screenshot_b64(self.driver)
        prompt = (
            "この画面に赤色・オレンジ色のエラーメッセージ、または処理が失敗したことを"
            "示すメッセージがありますか？\n"
            "ある場合: {\"found\": true, \"message\": \"<エラーの内容>\"}\n"
            "ない場合: {\"found\": false}\n"
            "JSONのみ返してください。"
        )
        raw = _ask_vision(self.client, b64, prompt)
        try:
            data = json.loads(_strip_json(raw))
            if data.get("found"):
                return data.get("message", "unknown error")
        except Exception:
            pass
        return None


# ── ユーティリティ ────────────────────────────────────────────────────────────

def _strip_json(text: str) -> str:
    """```json ... ``` を剥がす。"""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()
