"""
modules/ai_login_patch.py
actions.py の _ig_login / _x_login 等を AIRegister ベースに差し替えるパッチ。
actions.py を書き換えずに monkey-patch する。

main.py や cli.py の import 後にこれを import するだけで有効になる:
    import modules.ai_login_patch  # noqa
"""
from __future__ import annotations
import modules.actions as _actions
from modules.ai_register import AIRegister
from modules.email_provider import TempMail


# ── プラットフォームごとの登録URL ────────────────────────────────────────────
_REGISTER_URLS = {
    "instagram": "https://www.instagram.com/accounts/emailsignup/",
    "twitter":   "https://twitter.com/i/flow/signup",
    "youtube":   "https://accounts.google.com/signup/v2/createaccount?flowName=GlifWebSignIn",
    "tiktok":    "https://www.tiktok.com/signup",
    "facebook":  "https://www.facebook.com/r.php",
}


def _make_ai_login(platform: str):
    """指定プラットフォーム用のログイン関数を生成して返す。"""
    url = _REGISTER_URLS[platform]

    def _login(driver, identity: dict, proxy_dict, captcha_api_key: str,
               _api_key: str = "") -> bool:
        """AI Vision ベースの登録/ログイン。"""
        import os
        api_key = _api_key or os.environ.get("ANTHROPIC_API_KEY", "")

        mail = TempMail(proxy=proxy_dict)
        identity["email"] = mail.email

        driver.get(url)
        import time, random
        time.sleep(random.uniform(1.5, 3.0))

        reg = AIRegister(
            driver=driver,
            platform=platform,
            identity=identity,
            mail=mail,
            api_key=api_key,
            captcha_api_key=captcha_api_key,
            debug=False,
        )
        return reg.run()

    return _login


# ── 全プラットフォームを差し替え ──────────────────────────────────────────────
_actions._ig_login = _make_ai_login("instagram")
_actions._x_login  = _make_ai_login("twitter")
_actions._yt_login = _make_ai_login("youtube")
_actions._tt_login = _make_ai_login("tiktok")
_actions._fb_login = _make_ai_login("facebook")

print("[ai_login_patch] AI Vision ベースの登録関数に差し替えました ✓")


# ── config.json から API キーを自動ロード ─────────────────────────────────────
import json as _json
import os as _os
from pathlib import Path as _Path

def _load_api_key() -> str:
    cfg_path = _Path("config.json")
    if cfg_path.exists():
        try:
            cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
            key = cfg.get("anthropic_api_key", "")
            if key and not key.startswith("sk-ant-YOUR"):
                _os.environ.setdefault("ANTHROPIC_API_KEY", key)
                return key
        except Exception:
            pass
    return _os.environ.get("ANTHROPIC_API_KEY", "")

_load_api_key()
