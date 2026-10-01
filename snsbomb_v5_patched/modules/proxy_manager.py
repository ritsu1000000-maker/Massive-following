"""
proxy_manager.py
Proxy rotation — reads proxies.txt, round-robins, validates on load.
Format per line: ip:port  OR  user:pass@ip:port
"""
import random
import requests
from pathlib import Path


class ProxyManager:
    def __init__(self, proxy_file: str = "data/proxies.txt"):
        self.proxies: list[str] = []
        self.index = 0
        self._load(proxy_file)

    def _load(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            print(f"[ProxyManager] {path} not found — running direct (no proxy)")
            return
        raw = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")]
        self.proxies = raw
        print(f"[ProxyManager] Loaded {len(self.proxies)} proxies")

    def get(self) -> dict | None:
        """Return next proxy dict for requests/selenium or None if no list."""
        if not self.proxies:
            return None
        proxy_str = self.proxies[self.index % len(self.proxies)]
        self.index += 1
        scheme = "http"
        return {"http": f"{scheme}://{proxy_str}", "https": f"{scheme}://{proxy_str}"}

    def get_str(self) -> str | None:
        """Raw proxy string for undetected_chromedriver."""
        if not self.proxies:
            return None
        proxy_str = self.proxies[self.index % len(self.proxies)]
        self.index += 1
        return proxy_str

    def validate_all(self, timeout: int = 6) -> list[str]:
        """Test all proxies, return only live ones. Call once at startup."""
        live = []
        test_url = "https://httpbin.org/ip"
        for px in self.proxies:
            try:
                r = requests.get(
                    test_url,
                    proxies={"http": f"http://{px}", "https": f"http://{px}"},
                    timeout=timeout,
                )
                if r.status_code == 200:
                    live.append(px)
            except Exception:
                pass
        print(f"[ProxyManager] {len(live)}/{len(self.proxies)} proxies live")
        self.proxies = live
        return live
