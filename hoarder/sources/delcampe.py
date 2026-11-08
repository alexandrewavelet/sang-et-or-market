"""Delcampe: ongoing listings and sold items (sale date read from the item page)."""
from __future__ import annotations

import html
import re
import urllib.parse
from datetime import datetime
from typing import Iterator

from ..fetch import Client, FetchError, RateLimited

BASE = "https://www.delcampe.net"
MONTHS = {m: i + 1 for i, m in enumerate(
    ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"])}


def _money(s: str) -> float | None:
    m = re.search(r"([\d\s  ]+(?:,\d+)?)", s or "")
    if not m:
        return None
    try:
        return float(re.sub(r"[\s  ]", "", m.group(1)).replace(",", "."))
    except ValueError:
        return None


class Delcampe:
    name = "delcampe"

    def __init__(self, client: Client, cfg: dict):
        self.client = client
        self.cfg = cfg

    def search(self, query: str, max_pages: int, sold: bool = False) -> Iterator[dict]:
        for page in range(1, max_pages + 1):
            params = {"term": query, "page": page, "display_state": "sold_items" if sold else "ongoing",
                      "order": "sale_end_datetime" if sold else "sale_start_datetime"}
            url = f"{BASE}/fr/collections/search?{urllib.parse.urlencode(params)}"
            try:
                page_html = self.client.get(url)
            except RateLimited:
                raise
            except FetchError as e:
                print(f"  ! delcampe search failed: {e}")
                return
            items = list(self.parse_search(page_html, sold))
            yield from items
            if len(items) < 20:
                return

    @staticmethod
    def parse_search(page_html: str, sold: bool) -> Iterator[dict]:
        for m in re.finditer(r'<div id="item-(\d+)" data-watch-item>', page_html):
            block = page_html[m.start():m.start() + 9000]
            title = re.search(r'alt="Delcampe - ([^"]*)"', block)
            link = re.search(r'href="(/fr/collections/[^"]+-' + m.group(1) + r'\.html)"', block)
            price = re.search(r'class="item-price[^"]*">([^<]*)', block)
            img = re.search(r'data-original="([^"]+)"', block)
            if not (title and link and price):
                continue
            yield {
                "source": "delcampe",
                "source_id": m.group(1),
                "url": BASE + link.group(1),
                "title": html.unescape(title.group(1)).replace(" - Trading Cards", "").strip(),
                "price": _money(price.group(1)),
                "currency": "EUR",
                "image": img.group(1).replace("img_thumb", "img_large") if img else None,
                "status": "sold" if sold else "active",
            }

    def item(self, url: str) -> dict:
        try:
            page = self.client.get(url)
        except FetchError as e:
            if e.status in (404, 410):
                return {"status": "removed"}
            raise
        out: dict = {}
        sold = re.search(r"Vendu le \w+ (\d{1,2}) (\w+) (\d{4}) à (\d{1,2}):(\d{2})", page)
        if sold:
            d, mon, y, hh, mm = sold.groups()
            if mon in MONTHS:
                out["sold_at"] = datetime(int(y), MONTHS[mon], int(d), int(hh), int(mm)).isoformat()
                out["status"] = "sold"
        desc = re.search(r'<div[^>]*id="description"[^>]*>(.*?)</div>', page, re.S)
        if desc:
            out["description"] = html.unescape(re.sub(r"<[^>]+>", " ", desc.group(1))).strip()[:2000]
        return out
