"""eBay source — two modes:

* ``html`` (EXPERIMENTAL, opt-in via ``ebay.html_experimental``): parses the public search page.
  ``sold`` mode (LH_Sold=1&LH_Complete=1) yields concluded sales with price + date, which is the only
  way to get eBay comparables since the Finding API is gone and Marketplace Insights is restricted.
  ``active`` mode yields current listings. eBay's ToS forbid automated scraping: keep the volume tiny.
  Safety rails: anonymous session (never your eBay cookies), 4–6 s randomised delay, a page cap,
  max one run per day, and the source stops for the run — and backs off 48 h — at the first
  403/429/challenge page instead of retrying.
* ``api``: official Browse API (active listings only), needs EBAY_CLIENT_ID / EBAY_CLIENT_SECRET (read from
  the environment or from `.env` at the repo root). Listings that vanish from the API results are re-checked
  by id and marked ``ended`` once gone. The API cannot tell a sale from an unsold expiry, so ended listings
  simply leave the analysis instead of counting as probable sales.
"""
from __future__ import annotations

import base64
import html as htmlmod
import json
import os
import random
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from ..fetch import Client, FetchError

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_BY_LEGACY_URL = "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id"
API_SCOPE = "https://api.ebay.com/oauth/api_scope"
TOKEN_ATTEMPTS = 3
TOKEN_RETRY_SECONDS = 3

CHALLENGE_MARKERS = ("pardon our interruption", "splashui/challenge", "captcha", "checking your browser",
                     "verify yourself", "access denied", "/splashui/")
FR_MONTHS = {"janv": 1, "févr": 2, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6, "juil": 7, "août": 8,
             "aout": 8, "sept": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12}
EN_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


class Blocked(Exception):
    pass


class ApiError(Exception):
    """Unrecoverable Browse API failure (bad credentials, disabled keyset): stop the API path loudly."""


# ---------------------------------------------------------------- parsing (kept in one place)

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_price(raw: str) -> tuple[float | None, str]:
    raw = raw.replace("\xa0", " ").replace(" ", " ")
    if re.search(r"\bà\b|\bto\b", raw):  # price ranges (multi-variation listings) are not comparable
        return None, "EUR"
    currency = "USD" if "$" in raw else "GBP" if "£" in raw else "EUR"
    m = re.search(r"(\d{1,3}(?:[ .,]\d{3})*(?:[.,]\d{2})?)", raw)
    if not m:
        return None, currency
    num = m.group(1).replace(" ", "")
    if re.search(r",\d{2}$", num):
        num = num.replace(".", "").replace(",", ".")
    else:
        num = num.replace(",", "")
    try:
        return float(num), currency
    except ValueError:
        return None, currency


def parse_sold_date(raw: str) -> str | None:
    t = raw.lower().replace("\xa0", " ")
    m = re.search(r"vendu\w* le (\d{1,2}) ([a-zéû]+)\.? (\d{4})", t)
    if m:
        mon = next((v for k, v in FR_MONTHS.items() if m.group(2).startswith(k)), None)
        if mon:
            return date(int(m.group(3)), mon, int(m.group(1))).isoformat()
    m = re.search(r"sold\s+([a-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})", t)
    if m and m.group(1) in EN_MONTHS:
        return date(int(m.group(3)), EN_MONTHS[m.group(1)], int(m.group(2))).isoformat()
    return None


def parse_search_html(page: str, sold: bool) -> list[dict]:
    """Supports the current `.s-card` markup and the legacy `.s-item` one."""
    low = page[:20000].lower()
    if any(m in low for m in CHALLENGE_MARKERS) and "s-card" not in page and "s-item" not in page:
        raise Blocked("challenge page")
    starts = [m.start() for m in re.finditer(r'<li[^>]+class="[^"]*\bs-(?:card|item)\b[^"]*"', page)]
    items = []
    for i, start in enumerate(starts):
        block = page[start:starts[i + 1] if i + 1 < len(starts) else start + 15000]
        link = re.search(r'href="(https://www\.ebay\.[a-z.]+/itm/(\d+)[^"]*)"', block)
        title = re.search(r'class="[^"]*\bs-(?:card|item)__title\b[^"]*"[^>]*>(.*?)</(?:div|h3|span|a)>', block, re.S)
        price = re.search(r'class="[^"]*\bs-(?:card|item)__price\b[^"]*"[^>]*>(.*?)</span>', block, re.S)
        if not (link and title and price):
            continue
        t = _text(title.group(1))
        t = re.sub(r"^(nouvelle annonce|new listing)\s*", "", t, flags=re.I)
        t = re.sub(r"\s*(la page s'ouvre dans une nouvelle fenêtre ou un nouvel onglet|opens in a new window or tab)$", "", t, flags=re.I)
        if not t or re.match(r"(shop on ebay|achetez sur ebay)", t, re.I):
            continue
        value, currency = parse_price(_text(price.group(1)))
        if value is None:
            continue
        img = re.search(r'<img[^>]+(?:data-defer-load|src)="(https://i\.ebayimg\.com/[^"]+)"', block) \
            or re.search(r'<img[^>]+class="[^"]*s-(?:card|item)__image[^"]*"[^>]*src="([^"]+)"', block) \
            or re.search(r'<img[^>]+src="([^"]+)"[^>]*class="[^"]*s-(?:card|item)__image', block)
        item = {
            "source": "ebay", "source_id": link.group(2), "url": link.group(1).split("?")[0], "title": t,
            "price": value, "currency": currency, "image": htmlmod.unescape(img.group(1)) if img else None,
            "status": "sold" if sold else "active",
        }
        plain = _text(re.sub(r"<(script|style)[^>]*>.*?</\1>", "", block, flags=re.S))
        if not sold and re.search(r"\b\d+\s*ench[eè]res?\b|\b\d+\s*bids?\b|\bil reste \d|\btime left\b", plain, re.I):
            # Current bid, not a price: the final price will be higher.
            item["description"] = "[enchère en cours]"
        if sold:
            item["sold_at"] = parse_sold_date(plain)
        items.append(item)
    return items


# ---------------------------------------------------------------- client

class Ebay:
    name = "ebay"

    def __init__(self, client: Client, cfg: dict, state_path: Path | None = None):
        self.client = client
        self.cfg = cfg
        self.token: str | None = None
        self.token_expires = 0.0
        self.state_path = state_path

    # --- html (experimental)
    def _state(self) -> dict:
        if self.state_path and self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {}

    def _save_state(self, **kw):
        if self.state_path:
            self.state_path.write_text(json.dumps({**self._state(), **kw}, indent=2))

    def html_allowed(self) -> tuple[bool, str]:
        st = self._state()
        nowdt = datetime.now(timezone.utc)
        if st.get("blocked_until") and nowdt.isoformat() < st["blocked_until"]:
            return False, f"blocked earlier, backing off until {st['blocked_until']}"
        if st.get("last_run") and st["last_run"][:10] == nowdt.date().isoformat():
            return False, "already ran today (max one run per day)"
        return True, ""

    def html_search(self, query: str, sold: bool, pages: int) -> Iterator[dict]:
        hcfg = self.cfg.get("html", {})
        for pgn in range(1, pages + 1):
            params = {"_nkw": query, "_ipg": hcfg.get("per_page", 120), "_pgn": pgn, "_sacat": hcfg.get("category", 0)}
            if sold:
                params.update(LH_Sold=1, LH_Complete=1)
            url = f"https://{hcfg.get('domain', 'www.ebay.fr')}/sch/i.html?{urllib.parse.urlencode(params)}"
            lo, hi = hcfg.get("delay_seconds", [4, 6])
            time.sleep(random.uniform(lo, hi))
            try:
                page = self.client.get(url)
            except FetchError as e:
                if e.status in (403, 429, 503):
                    raise Blocked(f"HTTP {e.status}") from None
                raise
            items = parse_search_html(page, sold)
            yield from items
            if len(items) < int(params["_ipg"]) * 0.8:
                return

    def run_html(self, on_item) -> dict:
        ok, why = self.html_allowed()
        if not ok:
            print(f"[ebay html] skipped: {why}")
            return {"skipped": why}
        hcfg = self.cfg.get("html", {})
        stats = {"requests_cap": hcfg.get("max_requests", 8), "items": 0}
        budget = stats["requests_cap"]
        self._save_state(last_run=datetime.now(timezone.utc).isoformat())
        try:
            for mode in hcfg.get("modes", ["sold"]):
                for q in hcfg.get("queries", []):
                    if budget <= 0:
                        return stats
                    pages = min(hcfg.get("pages", 1), budget)
                    print(f"[ebay html {mode}] {q}")
                    before = self.client.requests
                    for item in self.html_search(q, mode == "sold", pages):
                        on_item(item, q)
                        stats["items"] += 1
                    budget -= self.client.requests - before
        except Blocked as e:
            until = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
            self._save_state(blocked_until=until, last_block=str(e))
            print(f"[ebay html] STOP: {e}. Source paused until {until}.")
            stats["blocked"] = str(e)
        return stats

    # --- official API (active listings only)
    @staticmethod
    def api_available() -> bool:
        return bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))

    def _auth(self) -> str:
        """Application token (OAuth client_credentials): App ID + Cert ID -> bearer token valid ~2 h."""
        if self.token and time.monotonic() < self.token_expires:
            return self.token
        creds = f"{os.environ['EBAY_CLIENT_ID']}:{os.environ['EBAY_CLIENT_SECRET']}".encode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": API_SCOPE}).encode()
        headers = {"Authorization": "Basic " + base64.b64encode(creds).decode(),
                   "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        for attempt in range(TOKEN_ATTEMPTS):
            try:
                raw = self.client.get(TOKEN_URL, data=body, headers=headers)
                break
            except FetchError as e:
                # eBay's token endpoint sometimes answers a spurious 401 that succeeds on retry.
                if attempt + 1 < TOKEN_ATTEMPTS and e.status in (401, 500, 502, 503):
                    time.sleep(TOKEN_RETRY_SECONDS * (attempt + 1))
                    continue
                hint = " (check EBAY_CLIENT_ID / EBAY_CLIENT_SECRET in .env: Production App ID + Cert ID," \
                       " keyset enabled with OAuth)" if e.status in (400, 401) else ""
                raise ApiError(f"eBay OAuth token request failed: HTTP {e.status}{hint}") from None
        payload = json.loads(raw)
        self.token = payload["access_token"]
        self.token_expires = time.monotonic() + int(payload.get("expires_in", 7200)) - 120
        return self.token

    def _api_get(self, url: str) -> dict:
        try:
            raw = self.client.get(url, headers={
                "Authorization": f"Bearer {self._auth()}", "Accept": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": self.cfg.get("marketplace", "EBAY_FR")})
        except FetchError as e:
            if e.status == 401:  # token revoked/expired early: retry once with a fresh one
                self.token = None
                raw = self.client.get(url, headers={
                    "Authorization": f"Bearer {self._auth()}", "Accept": "application/json",
                    "X-EBAY-C-MARKETPLACE-ID": self.cfg.get("marketplace", "EBAY_FR")})
            else:
                raise
        return json.loads(raw)

    def api_search(self, query: str, max_items: int | None = None) -> Iterator[dict]:
        """Browse API item_summary/search, paginated (200 per page, eBay caps offset+limit at 10 000)."""
        max_items = max_items or self.cfg.get("api_max_items_per_query", 1000)
        params = {"q": query, "limit": min(200, max_items), "offset": 0}
        if self.cfg.get("api_category_ids"):
            params["category_ids"] = ",".join(map(str, self.cfg["api_category_ids"]))
        if self.cfg.get("api_filter"):
            params["filter"] = self.cfg["api_filter"]
        seen = 0
        while seen < max_items and params["offset"] + params["limit"] <= 10_000:
            page = self._api_get(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}")
            summaries = page.get("itemSummaries", [])
            for it in summaries:
                item = api_item(it)
                if item:
                    yield item
            seen += len(summaries)
            if not page.get("next") or not summaries:
                return
            params["offset"] += params["limit"]

    def api_item_status(self, legacy_id: str) -> str:
        """'active' if the listing is still live, 'ended' otherwise (sold or withdrawn: the API does not say)."""
        try:
            it = self._api_get(f"{ITEM_BY_LEGACY_URL}?{urllib.parse.urlencode({'legacy_item_id': legacy_id})}")
        except FetchError as e:
            if e.status == 404:
                return "ended"
            raise
        end = it.get("itemEndDate")
        if end and end < datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"):
            return "ended"
        avail = (it.get("estimatedAvailabilities") or [{}])[0].get("estimatedAvailabilityStatus")
        return "ended" if avail == "OUT_OF_STOCK" else "active"


def legacy_id(it: dict) -> str | None:
    """Numeric eBay item id, the same id the HTML/import path uses (so both paths dedupe on one row)."""
    if it.get("legacyItemId"):
        return str(it["legacyItemId"])
    m = re.match(r"v1\|(\d+)\|", it.get("itemId", ""))
    return m.group(1) if m else None


def api_item(it: dict) -> dict | None:
    """Map a Browse API itemSummary to the listing dict stored by the DB. No seller data is kept on purpose
    (the keyset is exempted from Marketplace Account Deletion because we persist no eBay user data)."""
    sid = legacy_id(it)
    auction = "AUCTION" in (it.get("buyingOptions") or []) and "FIXED_PRICE" not in (it.get("buyingOptions") or [])
    money = (it.get("currentBidPrice") if auction else None) or it.get("price") or {}
    try:
        price = float(money.get("value"))
    except (TypeError, ValueError):
        price = None
    if not sid or not price:
        return None
    m = re.match(r"(https://www\.ebay\.[a-z.]+/itm/)", it.get("itemWebUrl") or "")
    item = {
        "source": "ebay", "source_id": sid, "url": f"{m.group(1) if m else 'https://www.ebay.fr/itm/'}{sid}",
        "title": it.get("title", ""), "price": price, "currency": money.get("currency", "EUR"),
        "image": (it.get("image") or {}).get("imageUrl")
                 or ((it.get("thumbnailImages") or [{}])[0]).get("imageUrl"),
        "condition": it.get("condition"), "status": "active",
    }
    if auction:
        item["description"] = "[enchère en cours]"  # current bid, not a price: same marker as the HTML path
    return item
