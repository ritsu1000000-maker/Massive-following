"""
modules/local_login_patch.py
actions.py の登録関数を LocalVision (moondream2) ベースに差し替え。
API不要・完全ローカル動作。

cli.py で:
    import modules.local_login_patch  # noqa
の1行で有効化。
"""
from __future__ import annotations
import modules.actions as _actions
from modules.local_vision import LocalVision
from modules.email_provider import TempMail

_REGISTER_URLS = {
    "instagram": "https://www.instagram.com/accounts/emailsignup/",
    "twitter":   "https://twitter.com/i/flow/signup",
    "youtube":   "https://accounts.google.com/signup/v2/createaccount?flowName=GlifWebSignIn",
    "tiktok":    "https://www.tiktok.com/signup",
    "facebook":  "https://www.facebook.com/r.php",
}


def _make_local_login(platform: str):
    url = _REGISTER_URLS[platform]

    def _login(driver, identity: dict, proxy_dict, captcha_api_key: str) -> bool:
        import time, random
        from modules.ai_register import AIRegister

        mail = TempMail(proxy=proxy_dict)
        identity["email"] = mail.email

        driver.get(url)
        time.sleep(random.uniform(1.5, 3.0))

        # AIRegister に LocalVision を注入
        reg = AIRegister.__new__(AIRegister)
        reg.driver          = driver
        reg.platform        = platform
        reg.identity        = identity
        reg.mail            = mail
        reg.captcha_api_key = captcha_api_key
        reg.debug           = False
        reg.vision          = LocalVision(driver, debug=False)

        return reg.run()

    return _login


_actions._ig_login = _make_local_login("instagram")
_actions._x_login  = _make_local_login("twitter")
_actions._yt_login = _make_local_login("youtube")
_actions._tt_login = _make_local_login("tiktok")
_actions._fb_login = _make_local_login("facebook")

print("[local_login_patch] moondream2 ローカルVisionに差し替え ✓")
