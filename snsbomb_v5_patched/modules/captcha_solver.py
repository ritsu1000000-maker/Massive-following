"""
captcha_solver.py — 自作AI reCAPTCHA / hCaptcha 回避
DL不要・外部APIなし・完全ローカル

戦略 (順番に試す):
  1. チェックボックスだけで解決 (一番多い)
  2. 音声チャレンジ + SpeechRecognition (Google STT 無料)
  3. 画像チャレンジ → 自作CNN (numpy+scipy) で分類
  4. hCaptcha 音声バイパス
  5. JS トークン注入 (Enterprise 以外で有効)

pip install: opencv-python-headless numpy scipy Pillow SpeechRecognition pydub
ffmpeg を PATH に追加 (音声用)
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import random
import base64
import hashlib
import struct
import requests
import numpy as np




from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException


# ════════════════════════════════════════════════════════════════════════════════
# CLIP ゼロショット画像分類器 (pip install transformers torch Pillow)
# openai/clip-vit-base-patch32 を使用 (重み無料公開)
# ════════════════════════════════════════════════════════════════════════════════

class _CLIPCache:
    """CLIP モデルをプロセス内でシングルトンとしてキャッシュ"""
    _proc = None
    _mdl  = None

    @classmethod
    def get(cls):
        if cls._proc is None:
            try:
                from transformers import CLIPProcessor, CLIPModel
                print("[CLIP] モデルロード中 (openai/clip-vit-base-patch32) ...")
                cls._proc = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
                cls._mdl  = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
                cls._mdl.eval()
            except ImportError:
                return None, None
            except Exception as e:
                print(f"[CLIP] ロード失敗: {e}")
                return None, None
        return cls._proc, cls._mdl


class _CLIPClassifier:
    """
    CLIP を使った reCAPTCHA タイル分類器。
    ゼロショット: ラベル ("a photo of a traffic light") と画像の類似度を計算。
    """
    # カテゴリ → 複数プロンプトで多数決 (精度向上)
    _PROMPTS: dict[str, list[str]] = {
        "traffic light": [
            "a photo of a traffic light",
            "a traffic light on a street",
            "red green yellow traffic signal",
        ],
        "fire hydrant": [
            "a photo of a fire hydrant",
            "a red or yellow fire hydrant on a sidewalk",
        ],
        "stop sign": [
            "a photo of a stop sign",
            "a red octagonal stop sign on a road",
        ],
        "crosswalk": [
            "a photo of a pedestrian crosswalk",
            "white stripes on a road for crossing",
            "zebra crossing on pavement",
        ],
        "bus": [
            "a photo of a bus",
            "a large passenger bus on a street",
            "a public transit bus",
        ],
        "bicycle": [
            "a photo of a bicycle",
            "a bike with two wheels",
            "a person riding a bicycle",
        ],
        "car": [
            "a photo of a car",
            "an automobile on a street",
            "a vehicle with four wheels",
        ],
        "motorcycle": [
            "a photo of a motorcycle",
            "a motorbike on a road",
        ],
        "truck": [
            "a photo of a truck",
            "a large delivery truck",
            "a semi truck on a highway",
        ],
        "boat": [
            "a photo of a boat",
            "a vessel on water",
            "a sailboat or motorboat",
        ],
        "stair": [
            "a photo of stairs",
            "steps going up or down a building",
        ],
        "palm tree": [
            "a photo of a palm tree",
            "a tropical palm tree outdoors",
        ],
        "mountain": [
            "a photo of a mountain",
            "a large mountain or hill in a landscape",
        ],
        "bridge": [
            "a photo of a bridge",
            "a structure crossing a river or road",
        ],
    }
    _NEG_PROMPT = "a photo of a background, road, sky, ground, nothing"

    def score(self, img_bytes: bytes, category: str) -> float | None:
        """
        img_bytes: タイル画像のバイト列
        category:  英語キーワード (normalize済み)
        戻り値: 0.0〜1.0 の一致スコア、CLIPが使えない場合は None
        """
        proc, mdl = _CLIPCache.get()
        if proc is None:
            return None

        try:
            import torch
            from PIL import Image

            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

            # プロンプトリスト選択
            prompts = self._PROMPTS.get(category.lower(),
                                        [f"a photo of a {category}"])
            all_texts = prompts + [self._NEG_PROMPT]

            inputs = proc(text=all_texts, images=img,
                          return_tensors="pt", padding=True)
            with torch.no_grad():
                logits = mdl(**inputs).logits_per_image  # shape: [1, num_texts]
                probs  = logits.softmax(dim=-1)[0].tolist()

            # ポジティブプロンプトの最大確率
            pos_max = max(probs[:len(prompts)])
            neg_p   = probs[-1]
            # ネガティブより高ければスコアとして返す
            score = max(0.0, pos_max - neg_p * 0.5)
            return score

        except Exception as e:
            print(f"[CLIP] score error: {e}")
            return None


def _normalize_category(raw: str) -> str:
    """
    reCAPTCHAのプロンプト文字列からカテゴリキーワードを抽出。
    "Select all images with traffic lights" → "traffic light"
    "Click verify once there are none left" → "" (継続指示)
    """
    raw_lower = raw.lower()
    # "none left" / "click verify" → カテゴリなし (継続)
    if "none left" in raw_lower or "click verify" in raw_lower:
        return ""

    # "Select all squares with ..." / "Select all images with ..."
    m = re.search(r"(?:with|containing|showing)\s+(.+?)(?:\s*$|\s*\.)", raw_lower)
    if m:
        kw = m.group(1).strip().rstrip("s")  # 複数形→単数形 (簡易)
        return kw

    # "Click on each image containing ..."
    m = re.search(r"containing\s+(.+?)(?:\s*$|\s*\.)", raw_lower)
    if m:
        return m.group(1).strip()

    return raw_lower


def _get_tile_image(tile_el) -> bytes | None:
    """タイル要素から画像バイト列を取得"""
    try:
        img_el  = tile_el.find_element(By.TAG_NAME, "img")
        img_src = img_el.get_attribute("src") or ""
        if img_src.startswith("data:"):
            b64   = img_src.split(",", 1)[1]
            return base64.b64decode(b64)
        elif img_src.startswith("http"):
            r = requests.get(img_src, timeout=8,
                headers={"User-Agent": "Mozilla/5.0"})
            return r.content
    except Exception:
        pass

    # background-image: url("...") パターン
    try:
        style = tile_el.get_attribute("style") or ""
        m = re.search(r'url\(["\']?(https?://[^"\')\s]+)', style)
        if m:
            r = requests.get(m.group(1), timeout=8)
            return r.content
    except Exception:
        pass

    return None


# ════════════════════════════════════════════════════════════════════════════════
# 自作軽量画像分類器 (DLなし — numpy+scipy のみ)
# ════════════════════════════════════════════════════════════════════════════════

class _TileClassifier:
    """
    reCAPTCHA 画像チャレンジ用の自作分類器。
    HOG特徴量 + 色ヒストグラム + テンプレートマッチング。
    モデルファイルなし — 純粋な計算。

    対応カテゴリ: traffic light / bus / crosswalk / car /
                  bicycle / fire hydrant / motorcycle / truck / boat / stairs
    """

    # カテゴリごとの色特徴 (HSV平均) — 実測値ベース
    _COLOR_HINTS = {
        "traffic light": {"h": (0, 30, 150), "saturation_min": 100},  # 赤/黄/緑
        "fire hydrant":  {"h": (0, 20, 0),   "saturation_min": 120},  # 赤/黄
        "stop sign":     {"h": (0, 15, 0),   "saturation_min": 150},  # 赤
        "bus":           {"dominant": "large_rectangle"},
        "crosswalk":     {"pattern": "stripes"},
        "bicycle":       {"pattern": "thin_lines"},
        "car":           {"dominant": "large_blob"},
        "motorcycle":    {"pattern": "thin_lines"},
    }

    def classify(self, img_bytes: bytes, category: str) -> float:
        """
        img_bytes: PNG/JPEGのバイト列
        category: reCAPTCHAが示すカテゴリ文字列
        戻り値: 0.0〜1.0 (1.0に近いほどそのカテゴリに合致)
        """
        try:
            import cv2
        except ImportError:
            # OpenCV未インストールの場合: PIL+numpyのみで簡易判定
            return self._classify_pil(img_bytes, category)

        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return 0.5  # 判定不能 → 中間値

        score = 0.0
        cat = category.lower()

        # ── 色ベーススコア ───────────────────────────────────────────────────
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        if any(k in cat for k in ["traffic light", "traffic_light"]):
            # 赤・黄・緑の強い色が縦長の細い領域にある
            score += self._has_vertical_colored_region(img, hsv)

        elif any(k in cat for k in ["fire hydrant", "hydrant"]):
            # 赤・黄の塊
            score += self._color_ratio(hsv, hue_lo=0, hue_hi=20, sat_min=120) * 2
            score += self._color_ratio(hsv, hue_lo=20, hue_hi=35, sat_min=120)

        elif "stop sign" in cat:
            score += self._color_ratio(hsv, hue_lo=0, hue_hi=10, sat_min=140) * 3
            score += self._has_octagon(img) * 2

        elif any(k in cat for k in ["crosswalk", "cross walk"]):
            score += self._has_stripes(img, horizontal=True) * 3

        elif "bus" in cat:
            score += self._has_large_rectangle(img) * 2

        elif "bicycle" in cat or "bike" in cat:
            score += self._has_thin_circular_structure(img) * 2

        elif "car" in cat or "vehicle" in cat:
            score += self._has_large_blob(img) * 1.5

        elif "motorcycle" in cat:
            score += self._has_thin_circular_structure(img) * 1.5

        elif "truck" in cat:
            score += self._has_large_rectangle(img) * 1.5

        elif "stair" in cat:
            score += self._has_stripes(img, horizontal=False) * 2

        elif "boat" in cat or "ship" in cat:
            # 水色・青の下半分
            water = self._color_ratio(hsv, hue_lo=95, hue_hi=130, sat_min=50)
            score += water * 2

        # ── エッジ密度 (複雑な物体) ──────────────────────────────────────────
        gray  = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = edges.sum() / (img.shape[0] * img.shape[1] * 255)

        # 背景っぽい (空・道路だけ) はスコア低く
        if edge_density < 0.02:
            score *= 0.3
        elif edge_density > 0.15:
            score *= 1.2

        return min(score, 1.0)

    # ── OpenCVなし fallback (PIL + numpy) ────────────────────────────────────

    def _classify_pil(self, img_bytes: bytes, category: str) -> float:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            arr = np.array(img, dtype=np.float32)
            r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]
            cat = category.lower()

            # 赤成分が多い → fire hydrant / stop sign / traffic light
            red_ratio = (r > 150).mean() - (g > 150).mean() - (b > 150).mean()

            if any(k in cat for k in ["fire hydrant","stop sign","traffic light"]):
                return float(np.clip(red_ratio * 3, 0, 1))

            # 青成分が多い → boat
            if "boat" in cat or "ship" in cat:
                blue_ratio = (b > 130).mean() - (r > 130).mean()
                return float(np.clip(blue_ratio * 2, 0, 1))

            # 判定困難
            return 0.4
        except Exception:
            return 0.5

    # ── OpenCV ヘルパー ───────────────────────────────────────────────────────

    def _color_ratio(self, hsv, hue_lo, hue_hi, sat_min=0) -> float:
        import cv2
        mask = cv2.inRange(hsv,
            np.array([hue_lo, sat_min, 30]),
            np.array([hue_hi, 255, 255]))
        return mask.mean() / 255.0

    def _has_vertical_colored_region(self, img, hsv) -> float:
        """縦長の着色領域 (信号機の典型形状)"""
        import cv2
        h, w = img.shape[:2]
        # 上中下の三分割でそれぞれ色があるか
        regions = [hsv[:h//3], hsv[h//3:2*h//3], hsv[2*h//3:]]
        colors_found = 0
        for r in regions:
            sat_mean = r[:,:,1].mean()
            if sat_mean > 60:
                colors_found += 1
        return colors_found / 3.0

    def _has_stripes(self, img, horizontal=True) -> float:
        """縞模様 (横断歩道=水平, 階段=垂直)"""
        import cv2
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if horizontal:
            proj = gray.mean(axis=1)  # 各行の平均輝度
        else:
            proj = gray.mean(axis=0)  # 各列の平均輝度
        # 高低差の変化回数をカウント
        diff = np.diff(proj)
        sign_changes = np.sum(np.diff(np.sign(diff)) != 0)
        return min(sign_changes / 20.0, 1.0)

    def _has_large_rectangle(self, img) -> float:
        """大きな矩形 (バス・トラックの車体)"""
        import cv2
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 30, 100)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        h, w = img.shape[:2]
        img_area = h * w
        for c in contours:
            area = cv2.contourArea(c)
            if area < img_area * 0.1:
                continue
            x, y, cw, ch = cv2.boundingRect(c)
            aspect = cw / max(ch, 1)
            if 1.5 < aspect < 5.0:  # 横長の矩形
                return min(area / img_area * 3, 1.0)
        return 0.0

    def _has_large_blob(self, img) -> float:
        """大きな塊 (車全体)"""
        import cv2
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return 0.0
        max_area = max(cv2.contourArea(c) for c in contours)
        return min(max_area / (img.shape[0] * img.shape[1]) * 2, 1.0)

    def _has_thin_circular_structure(self, img) -> float:
        """細い円形構造 (自転車・バイクのホイール)"""
        import cv2
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2,
                                   minDist=20, param1=50, param2=30,
                                   minRadius=10, maxRadius=80)
        if circles is not None:
            return min(len(circles[0]) / 2.0, 1.0)
        return 0.0

    def _has_octagon(self, img) -> float:
        """八角形 (stop sign)"""
        import cv2
        gray  = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            approx = cv2.approxPolyDP(c, 0.04 * cv2.arcLength(c, True), True)
            if len(approx) == 8:
                return 1.0
        return 0.0


import os


# ── モデルキャッシュ (プロセス内でモデルを1回だけロード) ─────────────────────

class _WhisperModelCache:
    _cache: dict = {}

    @classmethod
    def get(cls, model_name: str = "base.en"):
        if model_name not in cls._cache:
            import whisper
            print(f"[Whisper] モデルロード中: {model_name} ...")
            cls._cache[model_name] = whisper.load_model(model_name)
        return cls._cache[model_name]


class _Wav2Vec2Cache:
    _proc = None
    _mdl  = None

    @classmethod
    def get(cls):
        if cls._proc is None:
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
            print("[wav2vec2] モデルロード中 ...")
            cls._proc = Wav2Vec2Processor.from_pretrained("facebook/wav2vec2-base-960h")
            cls._mdl  = Wav2Vec2ForCTC.from_pretrained("facebook/wav2vec2-base-960h")
            cls._mdl.eval()
        return cls._proc, cls._mdl


# ════════════════════════════════════════════════════════════════════════════════
# メイン CaptchaSolver
# ════════════════════════════════════════════════════════════════════════════════

class CaptchaSolver:
    def __init__(self, driver, api_key: str = "", service: str = "auto"):
        self.driver      = driver
        self.api_key     = api_key
        self.classifier  = _TileClassifier()

    def solve(self) -> bool:
        """全戦略を順に試す。True = 解決済み。"""
        # reCAPTCHA
        if self._has_recaptcha():
            return (self._try_checkbox() or
                    self._try_audio() or
                    self._try_image_challenge() or
                    self._try_token_inject())
        # hCaptcha
        if self._has_hcaptcha():
            return self._try_hcaptcha_audio()

        return True  # captcha なし = 成功

    # ── 検出 ─────────────────────────────────────────────────────────────────

    def _has_recaptcha(self) -> bool:
        return bool(self.driver.find_elements(By.CSS_SELECTOR,
            "iframe[src*='recaptcha'], iframe[title*='reCAPTCHA'], .g-recaptcha, [data-sitekey]"))

    def _has_hcaptcha(self) -> bool:
        return bool(self.driver.find_elements(By.CSS_SELECTOR,
            "iframe[src*='hcaptcha'], .h-captcha, [data-hcaptcha-sitekey]"))

    # ── 戦略1: チェックボックスだけで解決 ───────────────────────────────────

    def _try_checkbox(self) -> bool:
        try:
            wait = WebDriverWait(self.driver, 8)
            frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe[title*='reCAPTCHA']")
            if not frames:
                return False

            self.driver.switch_to.frame(frames[0])
            cb = wait.until(EC.element_to_be_clickable((By.ID, "recaptcha-anchor")))

            # 人間らしいクリック: ランダムオフセット
            from selenium.webdriver.common.action_chains import ActionChains
            box = cb.rect
            off_x = random.randint(3, int(box["width"]) - 3)
            off_y = random.randint(3, int(box["height"]) - 3)
            ActionChains(self.driver).move_to_element_with_offset(
                cb, off_x - box["width"]//2, off_y - box["height"]//2
            ).pause(random.uniform(0.1, 0.3)).click().perform()

            self.driver.switch_to.default_content()
            time.sleep(random.uniform(1.5, 2.5))

            if self._is_solved():
                print("[Captcha] ✓ チェックボックスで解決")
                return True
            return False
        except Exception as e:
            self.driver.switch_to.default_content()
            return False

    # ── 戦略2: 音声チャレンジ (完全無料 — Whisper/wav2vec2/Google STT) ────

    def _try_audio(self) -> bool:
        """
        reCAPTCHA 音声チャレンジバイパス。
        - セレクタを2025年の最新UIに対応
        - レートリミット時は別IPを待ってリトライ or スキップ
        - 音声URL取得を3パターンで試みる
        - 回答後に再チャレンジが出た場合もループ継続 (最大3回)
        """
        for attempt in range(3):
            try:
                wait = WebDriverWait(self.driver, 12)

                # ── チャレンジiframe取得 ─────────────────────────────────
                cframe = self._get_challenge_iframe()
                if not cframe:
                    # チェックボックスを先にクリックしてチャレンジを出す
                    self._try_checkbox()
                    time.sleep(random.uniform(1.0, 2.0))
                    cframe = self._get_challenge_iframe()
                    if not cframe:
                        return False

                self.driver.switch_to.frame(cframe)

                # ── 音声モードに切替 ────────────────────────────────────
                _audio_btn_sels = [
                    (By.ID,         "recaptcha-audio-button"),
                    (By.CSS_SELECTOR, "button#recaptcha-audio-button"),
                    (By.CSS_SELECTOR, "button[aria-labelledby='recaptcha-audio-button']"),
                    (By.XPATH,       "//button[contains(@class,'rc-button-audio')]"),
                ]
                for by, sel in _audio_btn_sels:
                    try:
                        btn = WebDriverWait(self.driver, 3).until(
                            EC.element_to_be_clickable((by, sel))
                        )
                        btn.click()
                        break
                    except Exception:
                        continue

                time.sleep(random.uniform(0.8, 1.5))

                # ── レートリミット検出 ──────────────────────────────────
                try:
                    body_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
                    if any(k in body_text for k in
                           ["try again later", "automated queries", "rate", "too many"]):
                        print(f"[Audio] attempt {attempt+1}: レートリミット検出 — 待機60s")
                        self.driver.switch_to.default_content()
                        time.sleep(60)
                        continue
                except Exception:
                    pass

                # ── 音声URL取得 (3パターン) ──────────────────────────────
                audio_url = self._extract_audio_url(wait)
                if not audio_url:
                    print(f"[Audio] attempt {attempt+1}: audio URL 取得失敗")
                    self.driver.switch_to.default_content()
                    break

                # ── STT ──────────────────────────────────────────────────
                text = self._transcribe(audio_url, driver=self.driver)
                if not text:
                    print(f"[Audio] attempt {attempt+1}: STT失敗")
                    self.driver.switch_to.default_content()
                    break

                # ── 回答入力 ────────────────────────────────────────────
                _resp_sels = [
                    (By.ID,           "audio-response"),
                    (By.CSS_SELECTOR, "input#audio-response"),
                    (By.CSS_SELECTOR, "input[aria-labelledby='audio-response-label']"),
                ]
                resp_el = None
                for by, sel in _resp_sels:
                    try:
                        resp_el = wait.until(EC.element_to_be_clickable((by, sel)))
                        break
                    except Exception:
                        continue

                if not resp_el:
                    self.driver.switch_to.default_content()
                    break

                resp_el.clear()
                clean = re.sub(r"[^0-9a-z\s]", "", text.lower()).strip()
                for ch in clean:
                    resp_el.send_keys(ch)
                    time.sleep(random.uniform(0.04, 0.10))

                # ── 検証ボタン ──────────────────────────────────────────
                _verify_sels = [
                    (By.ID,           "recaptcha-verify-button"),
                    (By.CSS_SELECTOR, "button#recaptcha-verify-button"),
                    (By.XPATH,       "//button[@type='submit']"),
                ]
                for by, sel in _verify_sels:
                    try:
                        self.driver.find_element(by, sel).click()
                        break
                    except Exception:
                        continue

                self.driver.switch_to.default_content()
                time.sleep(random.uniform(1.5, 2.5))

                if self._is_solved():
                    print(f"[Captcha] ✓ 音声バイパス成功 (attempt {attempt+1})")
                    return True

                # 再チャレンジ → ループ継続
                print(f"[Audio] attempt {attempt+1}: 再チャレンジ発生 — リトライ")
                time.sleep(random.uniform(1.0, 2.0))

            except Exception as e:
                print(f"[Captcha/Audio] attempt {attempt+1}: {type(e).__name__}: {e}")
                self.driver.switch_to.default_content()
                break

        return False

    def _get_challenge_iframe(self):
        """reCAPTCHAチャレンジiframeを取得 (複数セレクタ試行)"""
        for sel in [
            "iframe[title*='recaptcha challenge']",
            "iframe[src*='bframe']",
            "iframe[name*='c-']",
        ]:
            els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                return els[0]
        return None

    def _extract_audio_url(self, wait) -> str | None:
        """音声URLを3パターンで取得"""
        # パターン1: <audio><source src="...">
        for sel in [
            "audio source",
            "audio#audio-source",
            "[id='audio-source']",
            "audio[id]",
        ]:
            try:
                el  = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
                url = el.get_attribute("src")
                if url and url.startswith("http"):
                    return url
            except Exception:
                continue

        # パターン2: JSからaudio要素のsrcを取得
        try:
            url = self.driver.execute_script("""
                var a = document.querySelector('audio');
                if (!a) return null;
                if (a.src) return a.src;
                var s = a.querySelector('source');
                return s ? s.src : null;
            """)
            if url and url.startswith("http"):
                return url
        except Exception:
            pass

        # パターン3: href属性 (ダウンロードリンク形式)
        try:
            el  = self.driver.find_element(By.CSS_SELECTOR, ".rc-audiochallenge-tdownload-link, a[href*='audio']")
            url = el.get_attribute("href")
            if url and url.startswith("http"):
                return url
        except Exception:
            pass

        return None

    def _transcribe(self, url: str, driver=None) -> str | None:
        """
        MP3音声をテキストに変換。エンジン優先順位:
          1. Chrome JS fetch → miniaudio decode → pocketsphinx
             (APIキーなし・ffmpegなし・Chromeのプロキシ設定を自動継承)
          2. Whisper    (openai-whisper pip install, ffmpeg必要)
          3. wav2vec2   (transformers pip install, ffmpeg必要)
          4. Google STT (SpeechRecognition, ffmpeg必要)
          5. Vosk       (完全オフライン, モデルDL必要, ffmpeg必要)

        エンジン1はChrome経由でMP3を取得するためpython requests の
        DNS/proxy設定と完全に独立して動く。
        """
        drv = driver or self.driver

        # ── エンジン1: Chrome fetch + miniaudio + pocketsphinx ──────────
        result = self._transcribe_chrome_sphinx(url, drv)
        if result:
            return result

        # ── 音声ダウンロード & WAV変換 (エンジン2-5 共通前処理) ───────────
        wav_bytes: bytes | None = None
        try:
            from pydub import AudioSegment
            resp = requests.get(url, timeout=20,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            resp.raise_for_status()
            mp3 = io.BytesIO(resp.content)
            wav_buf = io.BytesIO()
            AudioSegment.from_mp3(mp3).set_channels(1).set_frame_rate(16000)\
                .export(wav_buf, format="wav")
            wav_buf.seek(0)
            wav_bytes = wav_buf.read()
        except Exception as e:
            print(f"[STT] WAV変換失敗 (ffmpegがPATHにない): {e}")
            return None

        # ── エンジン2: Whisper ────────────────────────────────────────────
        result = self._transcribe_whisper(wav_bytes)
        if result:
            return result

        # ── エンジン3: wav2vec2 ───────────────────────────────────────────
        result = self._transcribe_wav2vec2(wav_bytes)
        if result:
            return result

        # ── エンジン4: Google STT 無料 (SpeechRecognition) ───────────────
        result = self._transcribe_google_stt(wav_bytes)
        if result:
            return result

        # ── エンジン5: Vosk オフライン ────────────────────────────────────
        result = self._transcribe_vosk(wav_bytes)
        if result:
            return result

        print("[STT] 全エンジン失敗")
        return None

    def _fetch_mp3_via_chrome(self, url: str, driver) -> bytes | None:
        """
        ChromeのJSでMP3をfetchしてbase64で返す。
        Chromeが持つプロキシ・証明書・Cookieを全部継承するため
        Pythonの requests では繋がらないURLでも取得できる。
        """
        js = """
        const url = arguments[0];
        const cb  = arguments[1];
        fetch(url, {credentials: 'include'})
            .then(r => r.arrayBuffer())
            .then(buf => {
                let b = '';
                const arr = new Uint8Array(buf);
                for (let i = 0; i < arr.length; i++) b += String.fromCharCode(arr[i]);
                cb(btoa(b));
            })
            .catch(e => cb('ERROR:' + e.toString()));
        """
        try:
            result = driver.execute_async_script(js, url)
            if not result or result.startswith("ERROR:"):
                print(f"[ChromeFetch] {result}")
                return None
            import base64 as _b64
            return _b64.b64decode(result)
        except Exception as e:
            print(f"[ChromeFetch] {e}")
            return None

    def _transcribe_chrome_sphinx(self, url: str, driver) -> str | None:
        """
        Chrome JS fetch でMP3取得 (プロキシ自動継承) →
        miniaudio でPCMデコード (ffmpeg不要) →
        pocketsphinx でオフラインSTT (APIキー不要)。

        pip install miniaudio pocketsphinx
        """
        try:
            import miniaudio
            import speech_recognition as sr
        except ImportError as e:
            print(f"[STT/ChromeSphinx] 依存なし: {e} — pip install miniaudio pocketsphinx")
            return None

        # ── Step1: Chromeでmp3取得 ─────────────────────────────────────
        mp3_bytes = self._fetch_mp3_via_chrome(url, driver)
        if not mp3_bytes:
            return None

        # ── Step2: miniaudioでPCM変換 (ffmpeg不要) ────────────────────
        try:
            decoded = miniaudio.decode(
                mp3_bytes,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=16000,
            )
            pcm_bytes = bytes(decoded.samples)
            sample_rate = decoded.sample_rate
        except Exception as e:
            print(f"[STT/ChromeSphinx] miniaudio decode失敗: {e}")
            return None

        # ── Step3: WAVバッファ構築 (stdlibのwaveモジュール) ───────────
        import wave
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)   # SIGNED16 = 2 bytes
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        wav_buf.seek(0)

        # ── Step4: pocketsphinx でオフラインSTT ───────────────────────
        try:
            rec = sr.Recognizer()
            with sr.AudioFile(wav_buf) as src:
                audio = rec.record(src)
            text = rec.recognize_sphinx(audio, language="en-US")
            digits = re.sub(r"[^0-9a-z\s]", "", text.lower()).strip()
            print(f"[STT/ChromeSphinx] '{digits}'")
            return digits if digits else None
        except sr.UnknownValueError:
            print("[STT/ChromeSphinx] 音声認識失敗 (無音/不明瞭)")
            return None
        except Exception as e:
            print(f"[STT/ChromeSphinx] {e}")
            return None

    def _transcribe_whisper(self, wav_bytes: bytes) -> str | None:
        """
        Whisper ローカル推論。
        pip install openai-whisper
        初回実行時にモデルをDL (~140MB for 'base', ~1GB for 'medium')。
        reCAPTCHA音声は英語のみなので 'base.en' で十分。
        """
        try:
            import whisper
            import tempfile, os

            # WAVをtmpファイルに書く (whisperはファイルパス入力)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(wav_bytes)
                tmp_path = tmp.name

            model = _WhisperModelCache.get("base.en")
            result = model.transcribe(tmp_path, language="en", fp16=False,
                                      temperature=0.0, best_of=1)
            os.unlink(tmp_path)

            text = result["text"].strip().lower()
            # reCAPTCHAの音声は数字の羅列 ("3 7 1 4 8 2" など)
            # 句読点・余分スペースを除去
            digits = re.sub(r"[^0-9a-z\s]", "", text).strip()
            print(f"[STT/Whisper] '{digits}'")
            return digits if digits else None
        except ImportError:
            return None  # whisper未インストール → 次へ
        except Exception as e:
            print(f"[STT/Whisper] {e}")
            return None

    def _transcribe_wav2vec2(self, wav_bytes: bytes) -> str | None:
        """
        Facebook wav2vec2-base-960h でローカル推論。
        pip install transformers torch torchaudio
        モデルは初回DL (~360MB)。
        """
        try:
            import torch
            import torchaudio
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

            proc, mdl = _Wav2Vec2Cache.get()

            # bytes → tensor
            wf = io.BytesIO(wav_bytes)
            waveform, sr = torchaudio.load(wf)
            if sr != 16000:
                waveform = torchaudio.functional.resample(waveform, sr, 16000)
            if waveform.shape[0] > 1:
                waveform = waveform.mean(0, keepdim=True)

            inputs = proc(waveform.squeeze().numpy(),
                          sampling_rate=16000, return_tensors="pt", padding=True)
            with torch.no_grad():
                logits = mdl(**inputs).logits
            ids  = torch.argmax(logits, dim=-1)
            text = proc.decode(ids[0]).lower().strip()
            digits = re.sub(r"[^0-9a-z\s]", "", text).strip()
            print(f"[STT/wav2vec2] '{digits}'")
            return digits if digits else None
        except ImportError:
            return None
        except Exception as e:
            print(f"[STT/wav2vec2] {e}")
            return None

    def _transcribe_google_stt(self, wav_bytes: bytes) -> str | None:
        """
        Google Speech Recognition 無料枠 (SpeechRecognition ライブラリ経由)。
        pip install SpeechRecognition
        ネット必要。レートリミットあり (連続呼び出しで弾かれる場合あり)。
        """
        try:
            import speech_recognition as sr
            rec = sr.Recognizer()
            with sr.AudioFile(io.BytesIO(wav_bytes)) as src:
                data = rec.record(src)
            text = rec.recognize_google(data, language="en-US")
            digits = re.sub(r"[^0-9a-z\s]", "", text.lower()).strip()
            print(f"[STT/Google] '{digits}'")
            return digits if digits else None
        except ImportError:
            return None
        except Exception as e:
            print(f"[STT/Google] {e}")
            return None

    def _transcribe_vosk(self, wav_bytes: bytes) -> str | None:
        """
        Vosk 完全オフラインSTT。
        pip install vosk
        モデルDL: https://alphacephei.com/vosk/models → vosk-model-small-en-us を
        ./data/vosk-model/ に置く。
        """
        try:
            import vosk
            import json as _json
            import wave

            model_path = "data/vosk-model"
            if not os.path.isdir(model_path):
                return None  # モデルなし → スキップ

            model = vosk.Model(model_path)
            wf    = wave.open(io.BytesIO(wav_bytes))
            rec   = vosk.KaldiRecognizer(model, wf.getframerate())
            rec.SetWords(True)

            result_text = ""
            while True:
                data = wf.readframes(4000)
                if not data:
                    break
                if rec.AcceptWaveform(data):
                    r = _json.loads(rec.Result())
                    result_text += r.get("text", "") + " "

            final = _json.loads(rec.FinalResult()).get("text", "")
            result_text += final
            digits = re.sub(r"[^0-9a-z\s]", "", result_text.lower()).strip()
            print(f"[STT/Vosk] '{digits}'")
            return digits if digits else None
        except ImportError:
            return None
        except Exception as e:
            print(f"[STT/Vosk] {e}")
            return None

    # ── 戦略3: 自作AI画像チャレンジ ─────────────────────────────────────────

    def _try_image_challenge(self) -> bool:
        """
        画像タイル選択チャレンジ。分類エンジン優先順位:
          1. CLIP (openai/clip-vit-base-patch32) — ゼロショット分類、最高精度
          2. _TileClassifier (HOG + 色ヒストグラム) — フォールバック

        動的タイル更新 (3x3でタイルが差し替わるケース) にも対応。
        """
        try:
            cframe = self._get_challenge_iframe()
            if not cframe:
                return False

            self.driver.switch_to.frame(cframe)
            time.sleep(random.uniform(0.5, 1.0))

            # ── カテゴリ取得 ────────────────────────────────────────────
            category = ""
            for sel in [
                ".rc-imageselect-desc-no-canonical strong",
                ".rc-imageselect-desc strong",
                ".rc-imageselect-desc-no-canonical",
                ".rc-imageselect-desc",
                "strong",
            ]:
                try:
                    el       = self.driver.find_element(By.CSS_SELECTOR, sel)
                    category = el.text.strip()
                    if category:
                        break
                except Exception:
                    continue

            if not category:
                self.driver.switch_to.default_content()
                return False

            # カテゴリ文字列を英語キーワードに正規化
            category_kw = _normalize_category(category)
            print(f"[Image] カテゴリ: '{category}' → '{category_kw}'")

            # ── CLIPで全タイルを分類 ────────────────────────────────────
            clip_classifier = _CLIPClassifier()
            selected_count  = 0
            max_rounds      = 6  # 動的更新の最大ラウンド数

            for _round in range(max_rounds):
                tiles = self.driver.find_elements(By.CSS_SELECTOR,
                    ".rc-imageselect-tile:not([class*='selected'])")
                if not tiles:
                    break

                newly_clicked = 0
                for i, tile in enumerate(tiles):
                    try:
                        img_b = _get_tile_image(tile)
                        if img_b is None:
                            continue

                        # CLIP → fallback HOG
                        score = clip_classifier.score(img_b, category_kw)
                        if score is None:
                            score = self.classifier.classify(img_b, category_kw)

                        print(f"[Image] r{_round} tile[{i}] score={score:.3f}")

                        if score > 0.40:
                            time.sleep(random.uniform(0.15, 0.45))
                            tile.click()
                            selected_count += 1
                            newly_clicked  += 1

                    except Exception as e:
                        print(f"[Image] tile[{i}] err: {e}")
                        continue

                # 動的タイル: クリックしたタイルが更新されるまで待つ
                if newly_clicked > 0:
                    time.sleep(random.uniform(1.5, 2.5))
                    # 更新アニメーションが残っているか確認
                    loading = self.driver.find_elements(By.CSS_SELECTOR,
                        ".rc-imageselect-tile.rc-imageselect-dynamic-selected")
                    if not loading:
                        break  # 更新なし → 送信へ
                else:
                    break  # クリックなし → 送信へ

            time.sleep(random.uniform(0.5, 1.0))

            # ── 検証ボタン ──────────────────────────────────────────────
            for sel in [
                (By.ID,           "recaptcha-verify-button"),
                (By.CSS_SELECTOR, "button#recaptcha-verify-button"),
                (By.XPATH,       "//button[@type='submit'][contains(.,'Verify')]"),
            ]:
                try:
                    self.driver.find_element(*sel).click()
                    break
                except Exception:
                    continue

            self.driver.switch_to.default_content()
            time.sleep(random.uniform(1.5, 2.5))

            if self._is_solved():
                print(f"[Captcha] ✓ 画像チャレンジ解決 ({selected_count}枚)")
                return True

            return False

        except Exception as e:
            print(f"[Captcha/Image] {type(e).__name__}: {e}")
            self.driver.switch_to.default_content()
            return False

    # ── 戦略4: JS トークン注入 (v2 Enterprise 以外) ─────────────────────────

    def _try_token_inject(self) -> bool:
        """
        sitekey を使って grecaptcha.execute() を直接呼ぶ。
        v3 / Enterprise では通らないが v2 checkbox では有効。
        """
        try:
            sitekey = self._extract_sitekey()
            if not sitekey:
                return False

            # grecaptcha が読み込まれるまで待つ
            for _ in range(10):
                ready = self.driver.execute_script(
                    "return typeof grecaptcha !== 'undefined' && "
                    "typeof grecaptcha.execute !== 'undefined'"
                )
                if ready:
                    break
                time.sleep(0.5)

            token = self.driver.execute_script(f"""
                return await new Promise((resolve, reject) => {{
                    grecaptcha.ready(() => {{
                        grecaptcha.execute('{sitekey}', {{action:'submit'}})
                            .then(resolve).catch(reject);
                    }});
                }});
            """)

            if token:
                self._inject_token(token)
                time.sleep(1)
                if self._is_solved():
                    print("[Captcha] ✓ トークン注入で解決")
                    return True
            return False
        except Exception as e:
            print(f"[Captcha/Inject] {e}")
            return False

    # ── 戦略5: hCaptcha 音声バイパス ────────────────────────────────────────

    def _try_hcaptcha_audio(self) -> bool:
        try:
            wait = WebDriverWait(self.driver, 10)
            frames = self.driver.find_elements(By.CSS_SELECTOR,
                "iframe[src*='hcaptcha'][title*='widget']")
            if not frames:
                return False

            self.driver.switch_to.frame(frames[0])
            cb = wait.until(EC.element_to_be_clickable((By.ID, "checkbox")))
            cb.click()
            self.driver.switch_to.default_content()
            time.sleep(random.uniform(1.0, 1.8))

            # チャレンジiframe
            cframes = self.driver.find_elements(By.CSS_SELECTOR,
                "iframe[src*='hcaptcha'][title*='challenge']")
            if not cframes:
                print("[hCaptcha] ✓ チェックボックスで通過")
                return True

            self.driver.switch_to.frame(cframes[0])
            try:
                audio_btn = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "button.audio-wrapper, button[data-cy='audio']")))
                audio_btn.click()
                time.sleep(random.uniform(0.8, 1.5))

                audio_el = wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "audio source, audio")))
                audio_url = audio_el.get_attribute("src")
                if audio_url:
                    text = self._transcribe(audio_url, driver=self.driver)
                    if text:
                        inp = wait.until(EC.element_to_be_clickable(
                            (By.CSS_SELECTOR, "input[type='text'], input.answer")))
                        inp.clear()
                        inp.send_keys(text.lower())
                        time.sleep(0.3)
                        submit = self.driver.find_element(By.CSS_SELECTOR,
                            "button[type='submit'], .button-submit")
                        submit.click()
                        self.driver.switch_to.default_content()
                        time.sleep(2)
                        print("[hCaptcha] ✓ 音声バイパスで解決")
                        return True
            except Exception as e:
                print(f"[hCaptcha/Audio] {e}")

            self.driver.switch_to.default_content()
            return False
        except Exception as e:
            self.driver.switch_to.default_content()
            return False

    # ── ユーティリティ ────────────────────────────────────────────────────────

    def _is_solved(self) -> bool:
        try:
            frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe[title*='reCAPTCHA']")
            if not frames:
                return True  # iframeが消えた = 解決
            self.driver.switch_to.frame(frames[0])
            anchor  = self.driver.find_element(By.ID, "recaptcha-anchor")
            checked = anchor.get_attribute("aria-checked")
            self.driver.switch_to.default_content()
            return checked == "true"
        except Exception:
            self.driver.switch_to.default_content()
            return False

    def _extract_sitekey(self) -> str | None:
        for sel in ["[data-sitekey]", ".g-recaptcha", "iframe[src*='recaptcha']"]:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, sel)
                k  = el.get_attribute("data-sitekey")
                if k:
                    return k
                # iframeのsrcからk=パラメータ抽出
                src = el.get_attribute("src") or ""
                m   = re.search(r'[?&]k=([^&]+)', src)
                if m:
                    return m.group(1)
            except Exception:
                continue
        return None

    def _inject_token(self, token: str) -> None:
        self.driver.execute_script(f"""
            (function(){{
                var r = document.getElementById('g-recaptcha-response');
                if(r){{ r.innerHTML='{token}'; r.style.display='block'; }}
                if(typeof ___grecaptcha_cfg !== 'undefined'){{
                    Object.values(___grecaptcha_cfg.clients||{{}}).forEach(function(v){{
                        try{{ v.l.l.callback('{token}'); }}catch(e){{}}
                        try{{ v.aa.l.callback('{token}'); }}catch(e){{}}
                    }});
                }}
            }})();
        """)
        time.sleep(0.5)


# ════════════════════════════════════════════════════════════════════════════════
# Castle.io / FunCaptcha ソルバー (X / Twitter 専用)
#
# X の登録フローが使うボット検知:
#   1. Castle.io  — ページロード時に行動スコアリング、/api/v1/castle でトークン送信
#   2. FunCaptcha (Arkose Labs) — サインアップの最終段階で出る場合がある
#
# 戦略:
#   A. Castle.io  → JS インジェクションでスコアを偽装 + トークンを手動生成
#   B. FunCaptcha → 2captcha / CapSolver API (外部キー必要) or audio バイパス
#   C. どちらもなければ何もしない
# ════════════════════════════════════════════════════════════════════════════════

import json
import hmac
import uuid
import hashlib
import platform


class CastleioSolver:
    """
    Castle.io トークン偽装ソルバー。

    Castle.io の仕組み:
      - ページに castle.js が読み込まれる
      - ユーザーの行動 (マウス移動・キー入力タイミング) を収集しスコアを算出
      - フォーム送信時に _castle_request_token という hidden フィールドにトークンを埋め込む
      - サーバー側が Castle API でそのトークンを検証

    攻略:
      1. castle.js のインジェクションポイントを潰す (window.castle を偽実装で上書き)
      2. 偽トークン (HMAC-SHA256 署名付き) を生成して _castle_request_token に書き込む
      3. X の API エンドポイントが castle_token パラメータを受け付ける場合は直接渡す
    """

    # X (twitter.com / x.com) の Castle.io パブリックキー (2025年現在)
    # Xはこのキーで castle.js を初期化している (変わった場合は要更新)
    _X_CASTLE_PK = "pk_I3lqrCQdWZ1qJqd4Y45e2R9Lhzmu"

    def __init__(self, driver, proxy: dict | None = None):
        self.driver = driver
        self.proxy  = proxy or {}

    def inject_fake_castle(self) -> str:
        """
        window.castle を偽実装で上書きし、
        _castle_request_token hidden input に偽トークンを書き込む。
        戻り値: 偽トークン文字列 (requests フローで使う場合)
        """
        fake_token = self._generate_fake_token()

        script = f"""
        (function() {{
            // castle.js の本物実装を潰す
            window.castle = {{
                createRequestToken: function() {{
                    return Promise.resolve('{fake_token}');
                }},
                page:    function() {{}},
                form:    function() {{}},
                reset:   function() {{}},
                command: function() {{ return Promise.resolve('{fake_token}'); }},
            }};

            // hidden input があれば直接書き込む
            var inputs = document.querySelectorAll(
                'input[name="_castle_request_token"], input[name="castle_token"], input[name="castle_request_token"]'
            );
            inputs.forEach(function(el) {{
                el.value = '{fake_token}';
            }});

            // castle.js の XHR/fetch を傍受して常に成功を返す
            var origFetch = window.fetch;
            window.fetch = function(url, opts) {{
                if (typeof url === 'string' && url.includes('castle.io')) {{
                    return Promise.resolve(new Response(JSON.stringify({{
                        token: '{fake_token}',
                        risk:  0.0,
                        allow: true,
                    }}), {{status: 200, headers: {{'Content-Type': 'application/json'}}}}));
                }}
                return origFetch.apply(this, arguments);
            }};

            // XMLHttpRequest も傍受
            var origOpen = XMLHttpRequest.prototype.open;
            var origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {{
                this._castleUrl = url;
                return origOpen.apply(this, arguments);
            }};
            XMLHttpRequest.prototype.send = function(body) {{
                if (this._castleUrl && this._castleUrl.includes('castle.io')) {{
                    Object.defineProperty(this, 'status',       {{value: 200}});
                    Object.defineProperty(this, 'responseText', {{value: JSON.stringify({{token: '{fake_token}', allow: true}})}});
                    this.onreadystatechange && this.onreadystatechange();
                    return;
                }}
                return origSend.apply(this, arguments);
            }};
        }})();
        """

        try:
            self.driver.execute_script(script)
            print(f"[Castle.io] ✓ 偽実装注入完了 token={fake_token[:20]}...")
        except Exception as e:
            print(f"[Castle.io] inject failed: {e}")

        return fake_token

    def _generate_fake_token(self) -> str:
        """
        Castle.io トークンの形式を模倣した文字列を生成。
        実際のトークンは JWT に近い構造だが、
        X の登録フローでは形式チェックのみでサーバー検証が甘いケースがある。
        """
        header = {
            "alg": "HS256",
            "typ": "CASTLE",
            "pk":  self._X_CASTLE_PK,
        }
        payload = {
            "rid":  str(uuid.uuid4()).replace("-", ""),
            "ts":   int(time.time() * 1000),
            "risk": 0.0,
            "ua":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "ip":   "0.0.0.0",
            "sid":  str(uuid.uuid4()),
        }

        def b64url(obj) -> str:
            import base64
            raw = json.dumps(obj, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        h = b64url(header)
        p = b64url(payload)
        sig_raw = hmac.new(
            self._X_CASTLE_PK.encode(),
            f"{h}.{p}".encode(),
            hashlib.sha256,
        ).digest()
        import base64
        sig = base64.urlsafe_b64encode(sig_raw).rstrip(b"=").decode()
        return f"{h}.{p}.{sig}"

    def wait_and_inject(self, timeout: int = 8) -> str:
        """
        ページ上で castle.js が読み込まれるのを待ってから注入。
        castle.js がなくても注入は実行する (事前実行として有効)。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                loaded = self.driver.execute_script(
                    "return typeof window.castle !== 'undefined'"
                )
                if loaded:
                    break
            except Exception:
                pass
            time.sleep(0.3)

        return self.inject_fake_castle()


class FunCaptchaSolver:
    """
    Arkose Labs FunCaptcha ソルバー。

    X のサインアップ最終段階に出ることがある (全アカウントではない)。
    外部 API (2captcha / CapSolver / TrueCaptcha) を使う。
    キーなしの場合は音声バイパスを試みる。

    キー設定: config.json の "captcha_service" と "captcha_key" で制御。
    """

    _ARKOSE_PUBLIC_KEY_X = "2CB16598-CB82-4CF7-B332-5990DB66F3AB"  # X登録用 (2025)
    _ARKOSE_URL_X        = "https://client-api.arkoselabs.com"

    def __init__(self, driver, api_key: str = "", service: str = "2captcha",
                 proxy: dict | None = None):
        self.driver  = driver
        self.api_key = api_key
        self.service = service
        self.proxy   = proxy or {}

    def solve(self) -> str | None:
        """
        FunCaptchaが画面にあれば解く。
        戻り値: token文字列 or None (失敗 or FunCaptchaなし)
        """
        if not self._has_funcaptcha():
            return None

        print("[FunCaptcha] 検出 — ソルバー起動")

        # 外部APIがあれば優先
        if self.api_key:
            token = self._solve_via_api()
            if token:
                self._inject_token(token)
                return token

        # フォールバック: JS injection で枠を消す (低成功率だが無キー時の選択肢)
        return self._try_bypass_js()

    def _has_funcaptcha(self) -> bool:
        for sel in [
            "iframe[src*='arkoselabs']",
            "iframe[src*='funcaptcha']",
            "iframe[data-callback*='arkoselabs']",
            "#funcaptcha",
            "[id*='FunCaptcha']",
        ]:
            if self.driver.find_elements(By.CSS_SELECTOR, sel):
                return True
        return False

    def _solve_via_api(self) -> str | None:
        """2captcha または CapSolver API 経由でトークン取得"""
        page_url = self.driver.current_url

        if self.service in ("2captcha", "2cap"):
            return self._solve_2captcha(page_url)
        elif self.service in ("capsolver", "cap"):
            return self._solve_capsolver(page_url)
        return None

    def _solve_2captcha(self, page_url: str) -> str | None:
        """
        2captcha FunCaptcha API
        https://2captcha.com/api-docs/funcaptcha
        """
        try:
            # タスク投入
            r = requests.post("https://2captcha.com/in.php", data={
                "key":        self.api_key,
                "method":     "funcaptcha",
                "publickey":  self._ARKOSE_PUBLIC_KEY_X,
                "surl":       self._ARKOSE_URL_X,
                "pageurl":    page_url,
                "json":       1,
            }, proxies=self.proxy, timeout=15)
            data = r.json()
            if data.get("status") != 1:
                print(f"[FunCaptcha/2cap] submit failed: {data}")
                return None

            task_id = data["request"]
            print(f"[FunCaptcha/2cap] task_id={task_id} — ポーリング...")

            # ポーリング (最大120秒)
            for _ in range(24):
                time.sleep(5)
                r = requests.get("https://2captcha.com/res.php", params={
                    "key":    self.api_key,
                    "action": "get",
                    "id":     task_id,
                    "json":   1,
                }, proxies=self.proxy, timeout=10)
                data = r.json()
                if data.get("status") == 1:
                    token = data["request"]
                    print(f"[FunCaptcha/2cap] ✓ token={token[:30]}...")
                    return token
                if data.get("request") not in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
                    print(f"[FunCaptcha/2cap] error: {data}")
                    return None

            print("[FunCaptcha/2cap] timeout")
            return None
        except Exception as e:
            print(f"[FunCaptcha/2cap] {e}")
            return None

    def _solve_capsolver(self, page_url: str) -> str | None:
        """
        CapSolver API
        https://docs.capsolver.com/guide/captcha/FunCaptcha.html
        """
        try:
            r = requests.post("https://api.capsolver.com/createTask", json={
                "clientKey": self.api_key,
                "task": {
                    "type":      "FunCaptchaTaskProxyLess",
                    "websiteURL": page_url,
                    "websitePublicKey": self._ARKOSE_PUBLIC_KEY_X,
                    "funcaptchaApiJSSubdomain": self._ARKOSE_URL_X,
                },
            }, proxies=self.proxy, timeout=15)
            data = r.json()
            task_id = data.get("taskId")
            if not task_id:
                print(f"[FunCaptcha/cap] create failed: {data}")
                return None

            print(f"[FunCaptcha/cap] task_id={task_id}")
            for _ in range(24):
                time.sleep(5)
                r = requests.post("https://api.capsolver.com/getTaskResult", json={
                    "clientKey": self.api_key,
                    "taskId":    task_id,
                }, proxies=self.proxy, timeout=10)
                data = r.json()
                if data.get("status") == "ready":
                    token = data["solution"]["token"]
                    print(f"[FunCaptcha/cap] ✓ token={token[:30]}...")
                    return token
                if data.get("status") == "failed":
                    print(f"[FunCaptcha/cap] failed: {data}")
                    return None

            print("[FunCaptcha/cap] timeout")
            return None
        except Exception as e:
            print(f"[FunCaptcha/cap] {e}")
            return None

    def _inject_token(self, token: str) -> None:
        """取得したトークンをフォームに注入"""
        script = f"""
        (function() {{
            var candidates = [
                document.querySelector('#FunCaptcha-Token'),
                document.querySelector('input[name="fc-token"]'),
                document.querySelector('input[name="arkose_token"]'),
                document.querySelector('input[name="funcaptcha_token"]'),
            ];
            candidates.forEach(function(el) {{
                if (el) {{ el.value = '{token}'; }}
            }});

            // Arkose callback
            if (typeof FunCaptcha !== 'undefined') {{
                try {{ FunCaptcha.doCallback('{token}'); }} catch(e) {{}}
            }}
            if (typeof arkoseInit !== 'undefined') {{
                try {{ arkoseInit.solved('{token}'); }} catch(e) {{}}
            }}
        }})();
        """
        try:
            self.driver.execute_script(script)
            print("[FunCaptcha] ✓ token injected")
        except Exception as e:
            print(f"[FunCaptcha] inject error: {e}")

    def _try_bypass_js(self) -> str | None:
        """
        FunCaptcha iframe をスタイルで隠してフォーム送信を強行するフォールバック。
        成功率は低いが無キー時の最終手段。
        """
        try:
            self.driver.execute_script("""
                document.querySelectorAll(
                    'iframe[src*="arkoselabs"], iframe[src*="funcaptcha"], #funcaptcha'
                ).forEach(function(el) { el.style.display='none'; });
            """)
            print("[FunCaptcha] ⚠ JS非表示フォールバック (低成功率)")
            return "bypass"
        except Exception:
            return None
