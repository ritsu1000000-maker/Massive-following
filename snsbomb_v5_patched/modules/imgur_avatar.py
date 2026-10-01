"""
imgur_avatar.py — Imgurからランダム画像を取得してプロフィール画像に使う
Client-ID: 匿名アクセス (公開ギャラリー)
"""
import io
import random
import requests

# Imgur公開Client-ID (匿名読み取り専用)
_CLIENT_ID = "546c25a59c58ad7"

# アバター向けサブレディット/タグ
_TAGS = [
    "portraits", "selfie", "faces", "person",
    "anime", "avatar", "profile",
]

_HEADERS = {"Authorization": f"Client-ID {_CLIENT_ID}"}


def fetch_random_avatar(proxy=None) -> bytes | None:
    """
    Imgurのギャラリーからランダムに画像を1枚取得してバイト列で返す。
    失敗したらNone。
    """
    proxies = proxy or {}
    try:
        tag = random.choice(_TAGS)
        r = requests.get(
            f"https://api.imgur.com/3/gallery/t/{tag}/time/all/{random.randint(0, 5)}",
            headers=_HEADERS,
            proxies=proxies,
            timeout=10,
        )
        items = r.json().get("data", {}).get("items", [])
        if not items:
            return _fallback(proxies)

        # 画像のみフィルタ
        images = []
        for item in items:
            if item.get("is_album"):
                imgs = item.get("images", [])
                images.extend([i for i in imgs if i.get("type", "").startswith("image/")])
            elif item.get("type", "").startswith("image/"):
                images.append(item)

        if not images:
            return _fallback(proxies)

        img = random.choice(images)
        url = img.get("link", "")
        if not url:
            return _fallback(proxies)

        r2 = requests.get(url, headers=_HEADERS, proxies=proxies, timeout=15)
        if r2.status_code == 200:
            return r2.content
        return _fallback(proxies)

    except Exception as e:
        return _fallback(proxies)


def _fallback(proxies) -> bytes | None:
    """UIアバター生成サービスでフォールバック"""
    try:
        import string
        name = "".join(random.choices(string.ascii_uppercase, k=2))
        r = requests.get(
            f"https://ui-avatars.com/api/?name={name}&size=256&background=random",
            proxies=proxies, timeout=10,
        )
        return r.content if r.status_code == 200 else None
    except Exception:
        return None


def set_discord_avatar(driver, image_bytes: bytes) -> bool:
    """
    Discordのプロフィール画像をSelenium経由で設定。
    参加後、discord.com/channels/... が開いてる状態で呼ぶ。
    白画面対策: /@me には飛ばず現在のURLのまま左下のアバターから設定。
    """
    import tempfile
    import os
    import time
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    if not image_bytes:
        return False

    # 白画面チェック — ロード完了まで待つ
    for _ in range(10):
        if "channels" in driver.current_url and driver.execute_script(
            "return document.readyState"
        ) == "complete":
            break
        time.sleep(0.5)

    suffix = ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(image_bytes)
        tmp_path = f.name

    wait = WebDriverWait(driver, 12)
    try:
        # 左下のユーザーアバター (現行UI) — /@me に飛ばない
        for sel in [
            "[aria-label*='Edit User Profile']",
            "[class*='avatarUploaderInner']",
            # 左下パネルのアバターボタン
            "button[aria-label*='avatar' i]",
            "[data-testid='user-area-avatar']",
            # 設定歯車 → User Settings → プロフィール
            "button[aria-label='User Settings']",
        ]:
            try:
                el = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                el.click()
                time.sleep(1.0)
                break
            except Exception:
                continue

        # プロフィールタブ
        for sel in [
            "//div[@id='my-account--profile']",
            "//div[contains(text(),'Profiles')]",
            "//li[@aria-label='Profiles']",
        ]:
            try:
                driver.find_element(By.XPATH, sel).click()
                time.sleep(0.5)
                break
            except Exception:
                continue

        # Change Avatarボタン or file input
        for sel in [
            "//button[contains(.,'Change Avatar')]",
            "//button[contains(.,'アバターを変更')]",
            "[aria-label='Change Avatar']",
        ]:
            try:
                by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
                driver.find_element(by, sel).click()
                time.sleep(0.5)
                break
            except Exception:
                continue

        # file input
        file_input = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='file']")
        ))
        file_input.send_keys(tmp_path)
        time.sleep(1.2)

        # Apply / Save / Done
        for sel in [
            "//button[contains(.,'Apply')]",
            "//button[contains(.,'Save Changes')]",
            "//button[contains(.,'Done')]",
            "//button[@type='submit']",
        ]:
            try:
                driver.find_element(By.XPATH, sel).click()
                time.sleep(0.8)
                break
            except Exception:
                continue

        # ESCで設定閉じる
        from selenium.webdriver.common.keys import Keys
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass

        return True

    except Exception as e:
        return False
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass


def upload_screenshot(driver, tag: str, username: str, proxy=None) -> str | None:
    """
    スクリーンショットをPNGバイトとして取得 → Imgurにアップロード → URLを返す。
    ディスクに保存しない。
    """
    try:
        png = driver.get_screenshot_as_png()
        return upload_bytes(png, title=f"dc_{tag}_{username[:8]}", proxy=proxy)
    except Exception as e:
        return None


def upload_bytes(data: bytes, title: str = "upload", proxy=None) -> str | None:
    """
    バイト列をImgurにアップロードしてURLを返す。
    """
    import base64
    proxies = proxy or {}
    try:
        r = requests.post(
            "https://api.imgur.com/3/image",
            headers={"Authorization": f"Client-ID {_CLIENT_ID}"},
            data={
                "image": base64.b64encode(data).decode(),
                "type":  "base64",
                "title": title,
            },
            proxies=proxies,
            timeout=15,
        )
        data_r = r.json().get("data", {})
        link = data_r.get("link")
        return link
    except Exception:
        return None
