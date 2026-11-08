"""Vinted: catalog search pages (server-rendered HTML) + item pages for description/status."""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import Iterator

from ..fetch import Client, FetchError, RateLimited
from ..parse import norm

BASE = "https://www.vinted.fr"

ITEM_BLOCK_RE = re.compile(r'data-testid="product-item-id-(\d+)"')
TITLE_ATTR_RE = re.compile(r'<a href="(/items/[^"?]+)[^"]*"[^>]*title="([^"]*)"')
IMG_RE = re.compile(r'<img src="(https://images\d*\.vinted\.net/[^"]+)"')
FAV_RE = re.compile(r"ajouté aux favoris par (\d+)")
TAIL_RE = re.compile(r"^(?P<title>.*?)(?:, Marque: (?P<brand>.*?))?(?:, Taille: [^,]*)?(?:, État: (?P<cond>[^,]*))?, "
                     r"(?P<price>[\d.,\s ]+) €(?:, (?P<total>[\d.,\s ]+) €)?$")


def _money(s: str | None) -> float | None:
    if not s:
        return None
    s = re.sub(r"[\s ]", "", s).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


class Vinted:
    name = "vinted"

    def __init__(self, client: Client, cfg: dict):
        self.client = client
        self.cfg = cfg
        self._warm = False

    def _warmup(self):
        if not self._warm:
            self.client.get(BASE + "/")
            self._warm = True

    def search(self, query: str, max_pages: int, stop=None) -> Iterator[dict]:
        self._warmup()
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            params = [("search_text", query), ("order", "newest_first"), ("page", str(page))]
            params += [("catalog[]", str(c)) for c in self.cfg.get("catalog_ids", [])]
            url = f"{BASE}/catalog?{urllib.parse.urlencode(params)}"
            try:
                page_html = self.client.get(url)
            except RateLimited:
                raise
            except FetchError as e:
                print(f"  ! vinted search failed: {e}")
                return
            new = 0
            page_items = list(self.parse_catalog(page_html))
            for item in page_items:
                if item["source_id"] in seen:
                    continue
                seen.add(item["source_id"])
                new += 1
                yield item
            if new == 0 or (stop and page_items and stop([i["source_id"] for i in page_items])):
                return

    @staticmethod
    def parse_catalog(page_html: str) -> Iterator[dict]:
        starts = [m.start() for m in ITEM_BLOCK_RE.finditer(page_html)]
        ids = [m.group(1) for m in ITEM_BLOCK_RE.finditer(page_html)]
        done: set[str] = set()
        for i, (pos, item_id) in enumerate(zip(starts, ids)):
            if item_id in done:
                continue
            done.add(item_id)
            end = next((s for s, other in zip(starts[i + 1:], ids[i + 1:]) if other != item_id), pos + 12000)
            block = page_html[pos:end]
            t = TITLE_ATTR_RE.search(block)
            if not t:
                continue
            raw = html.unescape(t.group(2))
            m = TAIL_RE.match(raw)
            if not m:
                continue
            img = IMG_RE.search(block)
            fav = FAV_RE.search(block)
            yield {
                "source": "vinted",
                "source_id": item_id,
                "url": BASE + t.group(1),
                "title": m.group("title").strip(),
                "brand": m.group("brand"),
                "condition": m.group("cond"),
                "price": _money(m.group("price")),
                "price_total": _money(m.group("total")),
                "currency": "EUR",
                "image": img.group(1) if img else None,
                "favourites": int(fav.group(1)) if fav else None,
                "status": "active",
            }

    def item(self, url: str) -> dict:
        """Fetch one item page. Returns description/status/price/images; status 'removed' on 404/410."""
        self._warmup()
        try:
            page = self.client.get(url)
        except FetchError as e:
            if e.status in (404, 410):
                return {"status": "removed"}
            raise
        out: dict = {}
        ld = re.search(r'<script type="application/ld\+json">(\{"@type":"Product".*?\})</script>', page)
        if ld:
            try:
                data = json.loads(ld.group(1))
                offer = data.get("offers") or {}
                out["title"] = data.get("name")
                out["description"] = data.get("description") or ""
                out["price"] = _money(str(offer.get("price"))) if offer.get("price") is not None else None
                avail = (offer.get("availability") or "").lower()
                out["status"] = "active" if "instock" in avail else "sold"
                if data.get("image"):
                    out["image_hd"] = data["image"]
                out["brand"] = (data.get("brand") or {}).get("name")
            except json.JSONDecodeError:
                pass
        if re.search(r'\\"is_closed\\":true|\\"status\\":\\"sold\\"', page):
            out["status"] = "sold"
        if re.search(r'\\"is_reserved\\":true', page) and out.get("status") == "active":
            out["status"] = "reserved"
        fav = re.search(r'\\"favourite_count\\":(\d+)', page)
        if fav:
            out["favourites"] = int(fav.group(1))
        start = page.find('\\"photos\\":[{')
        if start >= 0:
            chunk = page[start + len('\\"photos\\":'):start + 60000].replace('\\"', '"').replace("\\\\", "\\")
            try:
                photos, _ = json.JSONDecoder().raw_decode(chunk)
                urls = [p.get("url") for p in photos if isinstance(p, dict) and p.get("url")]
                if urls:
                    out["images"] = urls[:8]
            except (json.JSONDecodeError, TypeError):
                pass
        return out

    @staticmethod
    def id_from_url(url: str) -> str | None:
        m = re.search(r"/items/(\d+)", url)
        return m.group(1) if m else None


def player_query(player: dict) -> str:
    surname = norm(player["name"]).replace("-", " ").split()
    last = " ".join(surname[1:]) if len(surname) > 1 else surname[0]
    if player.get("era") == "current":
        return last
    return f"{last} lens"
