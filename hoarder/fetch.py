"""Polite HTTP client: one session per site, randomised delay, backs off on 429 and gives up quickly."""
from __future__ import annotations

import gzip
import http.cookiejar
import random
import time
import urllib.error
import urllib.request


class FetchError(Exception):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url


class RateLimited(FetchError):
    """The site keeps answering 429: the caller must stop this source for the run."""


class Client:
    def __init__(self, user_agent: str, delay: float | list[float] = 1.3, timeout: float = 25,
                 max_429: int = 2, backoff: float = 60):
        self.delay = (delay, delay) if isinstance(delay, (int, float)) else tuple(delay)
        self.timeout = timeout
        self.max_429 = max_429
        self.backoff = backoff
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip",
        }
        self._last = 0.0
        self.requests = 0

    def _pace(self):
        wait = random.uniform(*self.delay) - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, headers: dict | None = None, data: bytes | None = None) -> str:
        for attempt in range(self.max_429 + 1):
            self._pace()
            req = urllib.request.Request(url, data=data, headers={**self.headers, **(headers or {})})
            self._last = time.monotonic()
            self.requests += 1
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    return raw.decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    raise FetchError(e.code, url) from None
                if attempt == self.max_429:
                    raise RateLimited(429, url) from None
                retry = e.headers.get("Retry-After")
                pause = float(retry) if retry and retry.isdigit() else self.backoff * (attempt + 1)
                print(f"  … 429, pausing {pause:.0f}s")
                time.sleep(min(pause, 300))
        raise RateLimited(429, url)
