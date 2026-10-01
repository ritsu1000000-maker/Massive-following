"""
parse_services.py
smmjp.com の Services.html から全サービスをパースして
data/services_clean.json に保存する。

Usage:
    python parse_services.py Services.html
    python parse_services.py Services.html --out data/services_clean.json
"""
import re
import sys
import json
import argparse
from pathlib import Path


def detect_platform(name: str, category: str) -> str:
    text = (name + category).lower()
    checks = [
        ("instagram",  ["instagram"]),
        ("twitter",    ["twitter", "ツイッター", "x（旧twitter", "x(旧twitter"]),
        ("tiktok",     ["tiktok"]),
        ("youtube",    ["youtube"]),
        ("discord",    ["discord"]),
        ("facebook",   ["facebook"]),
        ("telegram",   ["telegram"]),
        ("twitch",     ["twitch"]),
        ("spotify",    ["spotify"]),
        ("linkedin",   ["linkedin"]),
        ("google",     ["google"]),
        ("line",       ["line"]),
        ("reddit",     ["reddit"]),
        ("pinterest",  ["pinterest"]),
        ("threads",    ["threads"]),
        ("bluesky",    ["bluesky"]),
        ("snapchat",   ["snapchat"]),
        ("soundcloud", ["soundcloud"]),
        ("note",       ["noteいい", "noteフォロ", "note スキ"]),
    ]
    for platform, keywords in checks:
        if any(kw in text for kw in keywords):
            return platform
    return "other"


def parse_html(html_path: str) -> list[dict]:
    content = Path(html_path).read_text(encoding="utf-8")

    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', content, re.DOTALL)

    services = []
    current_category = ""

    for r in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
        if not cells:
            continue

        def clean(s):
            s = re.sub(r'<[^>]+>', '', s)
            s = s.replace('\xa0', '').replace('&nbsp;', '').replace(',', '')
            return re.sub(r'\s+', ' ', s).strip()

        cleaned = [clean(c) for c in cells]

        if len(cleaned) == 1 and cleaned[0]:
            current_category = cleaned[0]
            continue

        if len(cleaned) >= 5:
            sid_raw = cleaned[1].strip()
            if not sid_raw.isdigit():
                continue
            sid  = int(sid_raw)
            name = cleaned[2]
            rate = cleaned[3]
            try:
                min_q = int(re.sub(r'[^\d]', '', cleaned[4]) or '10')
            except ValueError:
                min_q = 10
            try:
                max_q = int(re.sub(r'[^\d]', '', cleaned[5]) or str(min_q)) if len(cleaned) > 5 else min_q
            except ValueError:
                max_q = min_q

            services.append({
                "id":       sid,
                "name":     name,
                "category": current_category,
                "rate":     rate,
                "min":      min_q,
                "max":      max_q,
                "platform": detect_platform(name, current_category),
            })

    return services


def main():
    p = argparse.ArgumentParser(description="Services.html → services_clean.json")
    p.add_argument("html", help="Services.html のパス")
    p.add_argument("--out", default="data/services_clean.json")
    args = p.parse_args()

    services = parse_html(args.html)

    from collections import Counter
    plat_count = Counter(s["platform"] for s in services)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(services, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"パース完了: {len(services)}件 → {out}")
    print("プラットフォーム内訳:")
    for plat, cnt in plat_count.most_common():
        print(f"  {plat:<14}: {cnt}件")


if __name__ == "__main__":
    main()
