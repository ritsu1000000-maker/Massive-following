"""
cli.py v2 — snsbomb 全プラットフォーム対応インタラクティブメニュー
Python 3.10+  /  pip install questionary
"""
from __future__ import annotations

import json
import sys
import time
import random
import signal
from pathlib import Path

try:
    import questionary
    from questionary import Style
except ImportError:
    print("[cli] questionary が見つかりません → pip install questionary")
    sys.exit(1)

from modules.proxy_manager import ProxyManager
from modules.account_gen import generate_identity
from modules.browser import make_driver
from modules.actions import ACTION_MAP

# ── スタイル ──────────────────────────────────────────────────────────────────
STYLE = Style([
    ("qmark",       "fg:#e74c3c bold"),
    ("question",    "bold"),
    ("answer",      "fg:#2ecc71 bold"),
    ("pointer",     "fg:#e74c3c bold"),
    ("highlighted", "fg:#e74c3c bold"),
    ("selected",    "fg:#2ecc71"),
    ("separator",   "fg:#666666"),
    ("instruction", "fg:#888888"),
    ("disabled",    "fg:#858585 italic"),
])

CONFIG_PATH = Path("config.json")
PLATFORM_LABELS = {
    "instagram": "Instagram",
    "x":         "X (Twitter)",
    "youtube":   "YouTube",
    "tiktok":    "TikTok",
    "facebook":  "Facebook",
    "discord":   "Discord",
    "note":      "note",
}

# ── ユーティリティ ─────────────────────────────────────────────────────────────
def _load_cfg() -> dict:
    defaults = {
        "headless": True,
        "proxy_list_file": "data/proxies.txt",
        "delay_between_actions": [1, 3],
        "recaptcha": {"api_key": ""},
    }
    if CONFIG_PATH.exists():
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        defaults.update(saved)
    return defaults

def _save_cfg(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def _header() -> None:
    print("\n\033[1;31m╔══════════════════════════════════════════╗")
    print("║      SNS フォロ爆ツール  v2  全対応      ║")
    print("╚══════════════════════════════════════════╝\033[0m\n")

def _hr(ch="─", n=46): print("\033[90m" + ch * n + "\033[0m")
def _ok(m):   print(f"\033[32m✓ {m}\033[0m")
def _err(m):  print(f"\033[31m✗ {m}\033[0m")
def _info(m): print(f"\033[36mℹ {m}\033[0m")

def _save_account(identity: dict, platform: str, action: str) -> None:
    Path("data").mkdir(exist_ok=True)
    line = f"{platform}|{action}|{identity['username']}|{identity.get('email','')}|{identity['password']}\n"
    with open("data/accounts.txt", "a", encoding="utf-8") as f:
        f.write(line)

# ── メニュー: プラットフォーム → アクション → ターゲット → 実行 ───────────────
def menu_run(cfg: dict) -> None:
    # 1. プラットフォーム選択
    platform = questionary.select(
        "プラットフォームを選択",
        choices=[PLATFORM_LABELS[p] for p in ACTION_MAP] + ["← 戻る"],
        style=STYLE,
    ).ask()
    if not platform or "戻る" in platform:
        return

    # label → key
    pkey = next((k for k, v in PLATFORM_LABELS.items() if v == platform), None)
    if pkey is None:
        _err(f"プラットフォーム解決失敗: {platform}")
        return

    # 2. アクション選択
    actions = list(ACTION_MAP[pkey].keys())
    action_name = questionary.select(
        f"{platform} — アクションを選択",
        choices=actions + ["← 戻る"],
        style=STYLE,
    ).ask()
    if not action_name or "戻る" in action_name:
        return

    action_cfg = ACTION_MAP[pkey][action_name]

    # 3. ターゲット入力
    target = questionary.text(
        f"ターゲット ({action_cfg['label']})",
        validate=lambda v: bool(v.strip()) or "入力してください",
        style=STYLE,
    ).ask()
    if not target:
        return
    target = target.strip().lstrip("@") if action_cfg["target"] == "username" else target.strip()

    # 4. コメントテキスト (has_text のみ)
    comment_text = ""
    if action_cfg.get("has_text"):
        comment_text = questionary.text(
            "テキスト内容 (空欄でデフォルト)",
            style=STYLE,
        ).ask() or ""

    # 5. 実行数
    count_str = questionary.text(
        "実行数 (アカウント数)",
        default="10",
        validate=lambda v: v.isdigit() and int(v) > 0 or "1以上の整数",
        style=STYLE,
    ).ask()
    if not count_str:
        return
    count = int(count_str)

    headless = cfg.get("headless", True)
    captcha_key = cfg.get("recaptcha", {}).get("api_key", "")
    delay_range = cfg.get("delay_between_actions", [1, 3])

    _hr()
    print(f"\033[1m🚀  実行確認\033[0m")
    print(f"  プラットフォーム : {platform}")
    print(f"  アクション       : {action_name}")
    print(f"  ターゲット       : {target}")
    if comment_text:
        print(f"  テキスト         : {comment_text[:40]}{'…' if len(comment_text)>40 else ''}")
    print(f"  実行数           : {count}")
    print(f"  ヘッドレス       : {headless}")
    _hr()

    confirm = questionary.confirm("実行しますか？", default=False, style=STYLE).ask()
    if not confirm:
        _info("キャンセル")
        return

    fn = action_cfg["fn"]
    proxies = ProxyManager(cfg.get("proxy_list_file", "data/proxies.txt"))

    interrupted = False
    def _sigint(sig, frame):
        nonlocal interrupted
        print("\n\033[33m[ctrl+c] 今のアカウントが終わったら止まります\033[0m")
        interrupted = True
    signal.signal(signal.SIGINT, _sigint)

    success = 0
    fail = 0
    i = 0

    for i in range(1, count + 1):
        if interrupted:
            break

        proxy_str  = proxies.get_str()
        proxy_dict = proxies.get()
        identity   = generate_identity()

        _hr("─", 50)
        print(f"\033[1m  {i}/{count} — {identity['username']}\033[0m")

        driver = make_driver(headless=headless, proxy=proxy_str)
        try:
            kwargs = dict(
                driver=driver,
                target=target,
                identity=identity,
                proxy_dict=proxy_dict,
                captcha_api_key=captcha_key,
            )
            if action_cfg.get("has_text"):
                kwargs["comment_text"] = comment_text

            ok = fn(**kwargs)

            if ok:
                success += 1
                _save_account(identity, pkey, action_name)
                _ok(f"{identity['username']} → {action_name} 完了")
            else:
                fail += 1
                _err(f"{identity['username']} → 失敗")
        except KeyboardInterrupt:
            interrupted = True
        except Exception as e:
            _err(f"例外: {e}")
            fail += 1
        finally:
            try: driver.quit()
            except Exception: pass

        if not interrupted and i < count:
            s = random.uniform(*delay_range)
            _info(f"待機 {s:.1f}s …")
            time.sleep(s)

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    _hr("═")
    print(f"\033[1m  結果  ✓ {success}  ✗ {fail}  計 {i}\033[0m")
    _hr("═")

# ── メニュー: 設定 ────────────────────────────────────────────────────────────
def menu_settings(cfg: dict) -> dict:
    _hr()
    print(f"\033[1m⚙  設定\033[0m")
    print(f"  ヘッドレス       : {cfg.get('headless', True)}")
    print(f"  プロキシファイル  : {cfg.get('proxy_list_file', 'data/proxies.txt')}")
    drange = cfg.get('delay_between_actions', [3, 8])
    print(f"  待機時間         : {drange[0]}〜{drange[1]}s")
    print(f"  2captcha APIキー : {'設定済み' if cfg.get('recaptcha',{}).get('api_key') else '未設定'}")
    _hr()

    action = questionary.select(
        "変更する項目",
        choices=[
            "ヘッドレスモードを切替",
            "待機時間を変更",
            "2captcha APIキーを設定",
            "← 戻る",
        ],
        style=STYLE,
    ).ask()

    if not action or "戻る" in action:
        return cfg

    if "ヘッドレス" in action:
        cfg["headless"] = not cfg.get("headless", True)
        _ok(f"ヘッドレス → {'ON' if cfg['headless'] else 'OFF (ブラウザ表示)'}")

    elif "待機時間" in action:
        lo = questionary.text("最小秒数", default=str(drange[0]),
            validate=lambda v: v.replace('.','',1).isdigit() or "数値を入力", style=STYLE).ask()
        hi = questionary.text("最大秒数", default=str(drange[1]),
            validate=lambda v: v.replace('.','',1).isdigit() or "数値を入力", style=STYLE).ask()
        if lo and hi:
            cfg["delay_between_actions"] = [float(lo), float(hi)]
            _ok(f"待機時間 → {lo}〜{hi}s")

    elif "2captcha" in action:
        key = questionary.text(
            "2captcha APIキー (空欄 = 音声バイパスのみ)",
            default=cfg.get("recaptcha", {}).get("api_key", ""),
            style=STYLE,
        ).ask()
        if key is not None:
            cfg.setdefault("recaptcha", {})["api_key"] = key
            _ok("APIキー更新")

    _save_cfg(cfg)
    return cfg

# ── メニュー: プロキシ ─────────────────────────────────────────────────────────
def menu_proxy(cfg: dict) -> None:
    proxy_file = cfg.get("proxy_list_file", "data/proxies.txt")
    p = Path(proxy_file)
    lines = []
    if p.exists():
        lines = [l for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")]

    _hr()
    print(f"\033[1m🌐  プロキシ ({len(lines)} 件)\033[0m")
    _hr()

    action = questionary.select(
        "操作",
        choices=["リストを表示", "追加", "全検証", "← 戻る"],
        style=STYLE,
    ).ask()

    if not action or "戻る" in action:
        return

    if "表示" in action:
        for i, l in enumerate(lines[-30:], 1):
            print(f"  {i:3}. {l}")

    elif "追加" in action:
        entry = questionary.text("ip:port または user:pass@ip:port", style=STYLE).ask()
        if entry and entry.strip():
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(entry.strip() + "\n")
            _ok(f"追加: {entry.strip()}")

    elif "検証" in action:
        pm = ProxyManager(proxy_file)
        live = pm.validate_all()
        _ok(f"生存: {len(live)} / {len(lines)}")

# ── メニュー: アカウント履歴 ──────────────────────────────────────────────────

# ── Discord メン爆 ────────────────────────────────────────────────────────────
def menu_discord(cfg: dict) -> None:
    from modules.platforms.discord import join_server
    from modules.account_gen import generate_identity
    from modules.browser import make_driver
    from modules.proxy_manager import ProxyManager

    _hr()
    print("\033[1m💬  Discord メン爆\033[0m")
    _hr()

    invite_url = questionary.text(
        "招待URL (例: https://discord.gg/xxxxxxx)",
        validate=lambda v: ("discord.gg" in v or "discord.com/invite" in v) or "Discord招待URLを入力してください",
        style=STYLE,
    ).ask()
    if not invite_url:
        return
    invite_url = invite_url.strip()

    count_str = questionary.text(
        "何人増やすか",
        default="10",
        validate=lambda v: v.isdigit() and int(v) > 0 or "1以上の整数を入力",
        style=STYLE,
    ).ask()
    if not count_str:
        return
    count = int(count_str)

    headless    = cfg.get("headless", True)
    captcha_key = cfg.get("recaptcha", {}).get("api_key", "")
    delay_range = cfg.get("delay_between_actions", [2, 5])
    proxies     = ProxyManager(cfg.get("proxy_list_file", "data/proxies.txt"))

    _hr()
    print(f"  招待URL   : {invite_url}")
    print(f"  増やす人数 : {count}")
    print(f"  ヘッドレス : {headless}")
    _hr()

    confirm = questionary.confirm("実行しますか？", default=False, style=STYLE).ask()
    if not confirm:
        _info("キャンセル")
        return

    import signal as _signal
    interrupted = False
    def _sigint(sig, frame):
        nonlocal interrupted
        print("\n\033[33m[ctrl+c] 今のアカウントが終わったら止まります\033[0m")
        interrupted = True
    _signal.signal(_signal.SIGINT, _sigint)

    success = 0
    fail    = 0

    for i in range(1, count + 1):
        if interrupted:
            break
        identity   = generate_identity()
        proxy_str  = proxies.get_str()
        proxy_dict = proxies.get()

        print("\n" + "─" * 50)
        print(f"  {i}/{count} — {identity['username']}")

        driver = make_driver(headless=headless, proxy=proxy_str)
        try:
            ok = join_server(
                driver=driver,
                invite_url=invite_url,
                identity=identity,
                proxy_dict=proxy_dict,
                captcha_api_key=captcha_key,
            )
            if ok:
                success += 1
                line = f"discord|メン爆|{identity['username']}|{identity.get('email','')}|{identity['password']}\n"
                from pathlib import Path as _P
                _P("data/accounts.txt").parent.mkdir(parents=True, exist_ok=True)
                with open("data/accounts.txt", "a", encoding="utf-8") as f:
                    f.write(line)
                _ok(f"参加成功 ({success}/{count})")
            else:
                fail += 1
                _info("失敗 — 次へ")
        except Exception as e:
            print(f"\033[31m✗ 例外: {e}\033[0m")
            fail += 1
        finally:
            try:
                driver.quit()
            except Exception:
                pass

        if i < count and not interrupted:
            delay = random.uniform(*delay_range)
            _info(f"待機 {delay:.1f}s …")
            time.sleep(delay)

    _hr()
    print(f"\033[1m完了 — 成功: {success}  失敗: {fail}  合計: {count}\033[0m")
    _hr()

def menu_accounts() -> None:
    p = Path("data/accounts.txt")
    if not p.exists() or not p.read_text().strip():
        _info("accounts.txt がまだありません")
        return

    lines = p.read_text(encoding="utf-8").splitlines()
    _hr()
    print(f"\033[1m📋  作成済みアカウント ({len(lines)} 件)\033[0m")
    _hr()

    # platform filter
    platforms = sorted({l.split("|")[0] for l in lines if "|" in l})
    pf = "すべて"
    if len(platforms) > 1:
        pf = questionary.select("プラットフォームで絞り込み",
            choices=["すべて"] + platforms, style=STYLE).ask() or "すべて"

    filtered = [l for l in lines if pf == "すべて" or l.startswith(pf)]
    for i, line in enumerate(filtered[-30:], 1):
        parts = line.split("|")
        if len(parts) >= 5:
            print(f"  {i:3}. [{parts[0]}][{parts[1]}] @{parts[2]}  {parts[3]}")
        else:
            print(f"  {i:3}. {line}")
    if len(filtered) > 30:
        _info(f"最新30件 (全 {len(filtered)} 件)")

# ── メインループ ──────────────────────────────────────────────────────────────
def main() -> None:
    _header()
    cfg = _load_cfg()

    while True:
        platforms_str = " / ".join(PLATFORM_LABELS.values())
        print(f"\033[90m  対応: {platforms_str}\033[0m")

        choice = questionary.select(
            "メニュー",
            choices=[
                "🚀  実行",
                "💬  Discord メン爆",
                "⚙   設定",
                "🌐  プロキシ管理",
                "📋  アカウント履歴",
                "❌  終了",
            ],
            style=STYLE,
        ).ask()

        if not choice or "終了" in choice:
            print("\n\033[90mbye\033[0m\n")
            sys.exit(0)

        print()

        if "実行" in choice:
            menu_run(cfg)
        elif "Discord" in choice:
            menu_discord(cfg)
        elif "設定" in choice:
            cfg = menu_settings(cfg)
        elif "プロキシ" in choice:
            menu_proxy(cfg)
        elif "アカウント" in choice:
            menu_accounts()

        print()

if __name__ == "__main__":
    main()
