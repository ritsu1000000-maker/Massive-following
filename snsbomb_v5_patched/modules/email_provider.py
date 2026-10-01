"""
email_provider.py
マルチプロバイダ temp mail — Discord対応優先順:
  1. mail.tm  (Discord通過率高い、REST API)
  2. tempmail.plus
  3. Guerrilla Mail (フォールバック)
"""
import re
import time
import random
import string
import requests

# ── mail.tm ──────────────────────────────────────────────────────────────────
class MailTm:
    BASE = "https://api.mail.tm"

    def __init__(self, proxy=None):
        self.proxy   = proxy or {}
        self.session = requests.Session()
        self.email   = ""
        self.token   = ""
        self._account_id = ""
        self._init()

    def _init(self):
        try:
            # ドメイン一覧取得
            r = self.session.get(f"{self.BASE}/domains", proxies=self.proxy, timeout=10)
            domain = r.json()["hydra:member"][0]["domain"]
            slug   = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
            self.email = f"{slug}@{domain}"
            pw = "".join(random.choices(string.ascii_letters + string.digits, k=16))
            # アカウント作成
            r = self.session.post(f"{self.BASE}/accounts",
                json={"address": self.email, "password": pw},
                proxies=self.proxy, timeout=10)
            r.raise_for_status()
            # JWT取得
            r = self.session.post(f"{self.BASE}/token",
                json={"address": self.email, "password": pw},
                proxies=self.proxy, timeout=10)
            data = r.json()
            self.token = data["token"]
            self._account_id = data.get("id", "")
            self.session.headers["Authorization"] = f"Bearer {self.token}"
            print(f"[TempMail/mailtm] {self.email}")
        except Exception as e:
            print(f"[TempMail/mailtm] init failed: {e}")
            self.email = ""

    def wait_for_verification(self, keyword="verify", timeout=90, poll=2):
        if not self.token:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self.session.get(f"{self.BASE}/messages",
                    proxies=self.proxy, timeout=10)
                for msg in r.json().get("hydra:member", []):
                    subj = msg.get("subject", "").lower()
                    if keyword.lower() in subj or "discord" in subj:
                        # 本文取得
                        mid = msg["id"]
                        r2 = self.session.get(f"{self.BASE}/messages/{mid}",
                            proxies=self.proxy, timeout=10)
                        body = r2.json().get("text", "") + r2.json().get("html", "")
                        result = _extract(body)
                        if result:
                            return result
            except Exception as e:
                print(f"[TempMail/mailtm] poll: {e}")
            time.sleep(poll)
        print("[TempMail] Timed out")
        return None


# ── tempmail.plus ─────────────────────────────────────────────────────────────
class TempMailPlus:
    BASE = "https://tempmail.plus/api"

    def __init__(self, proxy=None):
        self.proxy = proxy or {}
        self.session = requests.Session()
        slug = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        self.email = f"{slug}@tempmail.plus"
        print(f"[TempMail/plus] {self.email}")

    def wait_for_verification(self, keyword="verify", timeout=90, poll=2):
        name = self.email.split("@")[0]
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self.session.get(
                    f"{self.BASE}/mails",
                    params={"email": name, "limit": 10, "epin": ""},
                    proxies=self.proxy, timeout=10,
                )
                for msg in r.json().get("mail_list", []):
                    subj = msg.get("subject", "").lower()
                    if keyword.lower() in subj or "discord" in subj:
                        mid  = msg["mail_id"]
                        r2   = self.session.get(
                            f"{self.BASE}/mails/{mid}",
                            params={"email": name, "epin": ""},
                            proxies=self.proxy, timeout=10,
                        )
                        body = r2.json().get("html", "") + r2.json().get("text", "")
                        result = _extract(body)
                        if result:
                            return result
            except Exception as e:
                print(f"[TempMail/plus] poll: {e}")
            time.sleep(poll)
        print("[TempMail] Timed out")
        return None


# ── Guerrilla Mail (フォールバック) ───────────────────────────────────────────
class GuerrillaMail:
    BASE = "https://api.guerrillamail.com/ajax.php"

    def __init__(self, proxy=None):
        self.proxy   = proxy or {}
        self.session = requests.Session()
        self.session_token = ""
        self.email = ""
        self._init()

    def _init(self):
        try:
            r = self.session.get(self.BASE,
                params={"f": "get_email_address", "lang": "en", "site": "guerrillamail.com"},
                proxies=self.proxy, timeout=10)
            data = r.json()
            self.session_token = data["sid_token"]
            self.email = data["email_addr"]
            print(f"[TempMail/guerrilla] {self.email}")
        except Exception as e:
            slug = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
            self.email = f"{slug}@guerrillamail.com"

    def wait_for_verification(self, keyword="verify", timeout=90, poll=2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self.session.get(self.BASE,
                    params={"f": "check_email", "sid_token": self.session_token, "seq": "0"},
                    proxies=self.proxy, timeout=10)
                for mail in r.json().get("list", []):
                    if keyword.lower() in mail.get("mail_subject", "").lower():
                        body = self._fetch(mail["mail_id"])
                        result = _extract(body)
                        if result:
                            return result
            except Exception:
                pass
            time.sleep(poll)
        print("[TempMail] Timed out")
        return None

    def _fetch(self, mid):
        try:
            r = self.session.get(self.BASE,
                params={"f": "fetch_email", "sid_token": self.session_token, "email_id": mid},
                proxies=self.proxy, timeout=10)
            return r.json().get("mail_body", "")
        except Exception:
            return ""


# ── ファクトリ ────────────────────────────────────────────────────────────────
def TempMail(proxy=None, prefer="mailtm"):
    """
    prefer: "mailtm" | "plus" | "guerrilla"
    失敗したら次のプロバイダにフォールバック
    """
    providers = {
        "mailtm":   MailTm,
        "plus":     TempMailPlus,
        "guerrilla": GuerrillaMail,
    }
    order = [prefer] + [k for k in ["mailtm", "plus", "guerrilla"] if k != prefer]
    for key in order:
        try:
            obj = providers[key](proxy=proxy)
            if obj.email:
                return obj
        except Exception:
            continue
    # 最終フォールバック
    return GuerrillaMail(proxy=proxy)


# ── ユーティリティ ─────────────────────────────────────────────────────────────
def _extract(html: str) -> str | None:
    """本文から6桁コード or URL を抽出"""
    text = re.sub(r'<[^>]+>', ' ', html)
    # 6桁コード優先
    m = re.search(r'\b(\d{6})\b', text)
    if m:
        return m.group(1)
    # URL
    m = re.search(r'https?://[^\s"\'<>]+', html)
    return m.group(0) if m else None
