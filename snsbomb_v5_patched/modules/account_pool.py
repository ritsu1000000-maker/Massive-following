"""
modules/account_pool.py
アカウントプール — JSON永続化 + 使い回し管理

pool.json の構造:
{
  "instagram": [
    {
      "username": "foo_bar12",
      "email":    "abc@guerrillamail.com",
      "password": "Xk3!mQpL9@rz",
      "cookies":  [...],          # selenium cookie list
      "created":  "2026-09-28T12:00:00",
      "last_used": "2026-09-28T13:00:00",
      "uses":     3,
      "status":   "active"        # active / banned / cooldown
    },
    ...
  ],
  "twitter": [...],
  "tiktok":  [...],
  "youtube": [...],
  "facebook": [...]
}

使い方:
    pool = AccountPool()
    acc = pool.get("instagram")          # クールダウン済みアカウントを取得
    if acc:
        # cookieでログイン → アクション実行
        pool.mark_used("instagram", acc)
    else:
        # プールが空 or 全クールダウン → 新規登録してから add()
        pool.add("instagram", new_account_dict)
"""
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path


POOL_FILE   = Path("data/pool.json")
COOLDOWN_H  = 2      # 同一アカウントの再使用までのクールダウン (時間)
MAX_USES    = 30     # 1アカウントの最大使用回数 (超えたら retired へ)
PLATFORMS   = ["instagram", "twitter", "tiktok", "youtube", "facebook", "discord"]


class AccountPool:
    def __init__(self, pool_file: str | Path = POOL_FILE):
        self._path  = Path(pool_file)
        self._lock  = threading.Lock()
        self._data  = self._load()

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {p: [] for p in PLATFORMS}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 追加 ─────────────────────────────────────────────────────────────────

    def add(self, platform: str, account: dict, cookies: list | None = None) -> None:
        """
        新規アカウントをプールに追加。
        account は generate_identity() の返り値 + email が入ったdict。
        cookies は driver.get_cookies() の結果。
        """
        platform = platform.lower().replace("x", "twitter")
        entry = {
            "username":  account.get("username", ""),
            "email":     account.get("email", ""),
            "password":  account.get("password", ""),
            "cookies":   cookies or [],
            "created":   _now(),
            "last_used": None,
            "uses":      0,
            "status":    "active",
        }
        with self._lock:
            self._data.setdefault(platform, []).append(entry)
            self._save()
        print(f"[Pool] added {entry['username']} to {platform} "
              f"(total: {len(self._data[platform])})")

    # ── 取得 ─────────────────────────────────────────────────────────────────

    def get(self, platform: str) -> dict | None:
        """
        クールダウン済みの active アカウントを1件返す。
        なければ None (→ 新規登録フローへ)。
        """
        platform = platform.lower().replace("x", "twitter")
        cutoff   = datetime.utcnow() - timedelta(hours=COOLDOWN_H)

        with self._lock:
            candidates = [
                a for a in self._data.get(platform, [])
                if a["status"] == "active"
                and a["uses"] < MAX_USES
                and (
                    a["last_used"] is None
                    or datetime.fromisoformat(a["last_used"]) < cutoff
                )
            ]
            if not candidates:
                return None
            # 使用回数が少ないものを優先
            candidates.sort(key=lambda a: a["uses"])
            return candidates[0]

    # ── 使用済みマーク ────────────────────────────────────────────────────────

    def mark_used(self, platform: str, account: dict,
                  new_cookies: list | None = None,
                  skip_increment: bool = False) -> None:
        """アクション後に呼ぶ。uses++、クッキー更新。
        skip_increment=True: クールダウンのみ記録してusesは増やさない
                             (既フォロー済みアカウントを別ターゲットに回す場合)"""
        platform = platform.lower().replace("x", "twitter")
        with self._lock:
            for a in self._data.get(platform, []):
                if a["username"] == account["username"]:
                    if not skip_increment:
                        a["uses"] += 1
                    a["last_used"] = _now()
                    if new_cookies:
                        a["cookies"] = new_cookies
                    if a["uses"] >= MAX_USES:
                        a["status"] = "retired"
                    break
            self._save()

    # ── BANマーク ─────────────────────────────────────────────────────────────

    def mark_banned(self, platform: str, account: dict) -> None:
        platform = platform.lower().replace("x", "twitter")
        with self._lock:
            for a in self._data.get(platform, []):
                if a["username"] == account["username"]:
                    a["status"] = "banned"
                    break
            self._save()

    # ── クッキー更新 ──────────────────────────────────────────────────────────

    def update_cookies(self, platform: str, account: dict, cookies: list) -> None:
        platform = platform.lower().replace("x", "twitter")
        with self._lock:
            for a in self._data.get(platform, []):
                if a["username"] == account["username"]:
                    a["cookies"] = cookies
                    break
            self._save()

    # ── 統計 ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            out = {}
            for p, accs in self._data.items():
                out[p] = {
                    "active":   sum(1 for a in accs if a["status"] == "active"),
                    "retired":  sum(1 for a in accs if a["status"] == "retired"),
                    "banned":   sum(1 for a in accs if a["status"] == "banned"),
                    "total":    len(accs),
                }
            return out

    def print_stats(self) -> None:
        print("\n── Pool Stats ──")
        for p, s in self.stats().items():
            if s["total"] > 0:
                print(f"  {p:<12} active={s['active']}  "
                      f"retired={s['retired']}  banned={s['banned']}  "
                      f"total={s['total']}")
        print()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")
