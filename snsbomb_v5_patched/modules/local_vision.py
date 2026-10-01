"""
modules/local_vision.py
moondream2 を使ったローカルVision — API不要、CPU動作。

初回起動時に ~2GB のモデルをダウンロード (HuggingFace)。
2回目以降はキャッシュから即起動。

必要: pip install transformers torch Pillow einops
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
import random
from pathlib import Path

from PIL import Image

# モデルはグローバルにキャッシュ (プロセス内で1回だけロード)
_model = None
_tokenizer = None


def _load_model():
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer

    print("[LocalVision] moondream2 ロード中 (初回のみ ~2GB DL)…")
    from transformers import AutoModelForCausalLM, AutoTokenizer
    import torch

    model_id = "vikhyatk/moondream2"
    revision  = "2024-08-26"  # 安定版ピン

    _tokenizer = AutoTokenizer.from_pretrained(
        model_id, revision=revision, trust_remote_code=True
    )
    _model = AutoModelForCausalLM.from_pretrained(
        model_id, revision=revision,
        trust_remote_code=True,
        torch_dtype="auto",
        device_map="cpu",
        low_cpu_mem_usage=True,
    )
    _model.eval()
    print("[LocalVision] moondream2 ロード完了 ✓")
    return _model, _tokenizer


def _ask(image: Image.Image, question: str) -> str:
    """moondream2 に画像 + 質問を投げてテキスト回答を返す。"""
    model, tokenizer = _load_model()
    enc = model.encode_image(image)
    answer = model.answer_question(enc, question, tokenizer)
    return answer.strip()


def _screenshot_pil(driver) -> Image.Image:
    png = driver.get_screenshot_as_png()
    return Image.open(io.BytesIO(png)).convert("RGB")


def _strip_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


# ════════════════════════════════════════════════════════════════════════════════
# LocalVision クラス — AIVision と同じインターフェース
# ════════════════════════════════════════════════════════════════════════════════

class LocalVision:
    """
    moondream2 ベースのローカルVision。
    AIVision と同じメソッドを持つのでそのまま差し替え可能。
    """

    def __init__(self, driver, debug: bool = False):
        self.driver = driver
        self.debug  = debug
        _load_model()   # 起動時にロード

    # ── 1. 要素クリック ───────────────────────────────────────────────────────

    def click_element(self, description: str, retries: int = 3) -> bool:
        w = self.driver.execute_script("return window.innerWidth")
        h = self.driver.execute_script("return window.innerHeight")

        for attempt in range(retries):
            img = _screenshot_pil(self.driver)

            # moondream2 は座標を直接返せない → 存在確認 + 領域を絞って座標推定
            exists_q = f"Is there a '{description}' button or element visible in this image? Answer yes or no."
            exists_a = _ask(img, exists_q).lower()

            if self.debug:
                print(f"[LocalVision/click] exists? {exists_a}")

            if "yes" not in exists_a:
                time.sleep(1.5)
                continue

            # 座標を聞く
            coord_q = (
                f"Where is the '{description}' element? "
                f"Give the approximate X and Y pixel coordinates as: X=<number> Y=<number>. "
                f"Image size is {w}x{h} pixels."
            )
            coord_a = _ask(img, coord_q)
            if self.debug:
                print(f"[LocalVision/click] coord: {coord_a}")

            x, y = _parse_coords(coord_a, w, h)
            if x is None:
                time.sleep(1)
                continue

            self.driver.execute_script(
                "document.elementFromPoint(arguments[0], arguments[1]).click();", x, y
            )
            if self.debug:
                print(f"[LocalVision/click] clicked at ({x},{y})")
            return True

        print(f"[LocalVision/click] failed: {description}")
        return False

    # ── 2. フィールド入力 ─────────────────────────────────────────────────────

    def fill_field(self, description: str, text: str, retries: int = 3) -> bool:
        w = self.driver.execute_script("return window.innerWidth")
        h = self.driver.execute_script("return window.innerHeight")

        for attempt in range(retries):
            img = _screenshot_pil(self.driver)

            coord_q = (
                f"Where is the '{description}' input field? "
                f"Give X and Y pixel coordinates as: X=<number> Y=<number>. "
                f"Image size is {w}x{h} pixels."
            )
            coord_a = _ask(img, coord_q)
            if self.debug:
                print(f"[LocalVision/fill] coord: {coord_a}")

            x, y = _parse_coords(coord_a, w, h)
            if x is None:
                time.sleep(1)
                continue

            el = self.driver.execute_script(
                "return document.elementFromPoint(arguments[0], arguments[1]);", x, y
            )
            if not el:
                time.sleep(1)
                continue

            self.driver.execute_script("""
                var el = arguments[0]; var val = arguments[1];
                var setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, val);
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
            """, el, text)

            # Reactが上書きするケースにフォールバック
            time.sleep(0.3)
            current = el.get_attribute("value") or ""
            if current != text:
                el.clear()
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(self.driver).click(el).send_keys(text).perform()

            if self.debug:
                print(f"[LocalVision/fill] filled '{description}' at ({x},{y})")
            return True

        print(f"[LocalVision/fill] failed: {description}")
        return False

    # ── 3. ログイン状態判定 ───────────────────────────────────────────────────

    def is_logged_in(self, platform: str) -> bool:
        img = _screenshot_pil(self.driver)
        q = (
            f"This is a {platform} page. "
            f"Is the user logged in? Look for home feed, profile icon, or navigation bar. "
            f"Answer: logged_in, logged_out, or blocked."
        )
        ans = _ask(img, q).lower()
        if self.debug:
            print(f"[LocalVision/login] {ans}")
        return "logged_in" in ans or "logged in" in ans

    # ── 4. ステージ検出 ───────────────────────────────────────────────────────

    def detect_stage(self, platform: str) -> str:
        img = _screenshot_pil(self.driver)
        q = (
            f"This is a {platform} registration/login screen. "
            f"Which stage is shown? Choose exactly one:\n"
            f"email_form - email/name/password form visible\n"
            f"dob_form - date of birth form visible\n"
            f"captcha - captcha puzzle visible\n"
            f"email_verify_code - numeric code entry visible\n"
            f"email_verify_url - 'click link in email' message visible\n"
            f"phone_verify - phone number required\n"
            f"home - home feed or logged-in state\n"
            f"error - error message or account suspended\n"
            f"unknown - none of the above\n"
            f"Answer with just the stage name."
        )
        ans = _ask(img, q).lower().strip()
        if self.debug:
            print(f"[LocalVision/stage] raw: {ans}")

        for stage in ["email_form", "dob_form", "captcha", "email_verify_code",
                       "email_verify_url", "phone_verify", "home", "error"]:
            if stage in ans:
                return stage
        return "unknown"

    # ── 5. メール本文からコード抽出 ───────────────────────────────────────────

    def extract_code_from_text(self, mail_body: str) -> str | None:
        # 正規表現で先に試す (速い)
        m = re.search(r'\b(\d{6})\b', mail_body)
        if m:
            return m.group(1)
        m = re.search(r'https?://\S+', mail_body)
        if m:
            return m.group(0).rstrip('.,)"\'')

        # 正規表現で取れなかった場合 → moondream2 はテキスト解析が苦手なので
        # HTMLタグ除去してから正規表現を再試行
        plain = re.sub(r'<[^>]+>', ' ', mail_body)
        plain = re.sub(r'\s+', ' ', plain)

        m = re.search(r'\b(\d{6})\b', plain)
        if m:
            return m.group(1)
        m = re.search(r'https?://\S+', plain)
        if m:
            return m.group(0).rstrip('.,)"\'')

        return None

    # ── 6. ダイアログdismiss ─────────────────────────────────────────────────

    def dismiss_dialog(self) -> bool:
        img = _screenshot_pil(self.driver)
        q = (
            "Is there a cookie consent banner, age verification, notification prompt, "
            "or any popup dialog that needs to be dismissed? "
            "If yes, what are the X and Y coordinates of the dismiss/accept/OK button? "
            "Answer: yes X=<number> Y=<number> or no."
        )
        ans = _ask(img, q).lower()
        if self.debug:
            print(f"[LocalVision/dismiss] {ans}")

        if ans.startswith("yes") or "yes" in ans[:10]:
            w = self.driver.execute_script("return window.innerWidth")
            h = self.driver.execute_script("return window.innerHeight")
            x, y = _parse_coords(ans, w, h)
            if x:
                self.driver.execute_script(
                    "document.elementFromPoint(arguments[0],arguments[1]).click();", x, y
                )
                return True
        return False

    # ── 7. エラー検出 ─────────────────────────────────────────────────────────

    def get_error(self) -> str | None:
        img = _screenshot_pil(self.driver)
        q = "Is there an error message or account suspension notice on this page? If yes, describe it briefly. If no, say 'no error'."
        ans = _ask(img, q)
        if self.debug:
            print(f"[LocalVision/error] {ans}")
        if "no error" in ans.lower():
            return None
        return ans


# ── 座標パーサー ──────────────────────────────────────────────────────────────

def _parse_coords(text: str, w: int, h: int) -> tuple[int | None, int | None]:
    """
    "X=320 Y=480" や "320, 480" や "at (320, 480)" 等から座標を抽出。
    画面範囲外なら None を返す。
    """
    # X=NNN Y=NNN
    mx = re.search(r'[Xx][=:\s]+(\d+)', text)
    my = re.search(r'[Yy][=:\s]+(\d+)', text)
    if mx and my:
        x, y = int(mx.group(1)), int(my.group(1))
        if 0 < x < w and 0 < y < h:
            return x, y

    # 数字のペア (最初の2つ)
    nums = re.findall(r'\d+', text)
    if len(nums) >= 2:
        x, y = int(nums[0]), int(nums[1])
        if 0 < x < w and 0 < y < h:
            return x, y

    return None, None
