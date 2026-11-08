"""Collect listings from every enabled source into the SQLite store."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .db import DB, now
from .fetch import Client, FetchError, RateLimited
from .parse import PlayerMatcher, analyse_text, norm
from .sources.delcampe import Delcampe
from .sources.ebay import Ebay
from .sources.vinted import Vinted, player_query


def _client(cfg: dict, delay=None) -> Client:
    h = cfg["http"]
    return Client(h["user_agent"], delay or h["delay_seconds"], h["timeout_seconds"],
                  h.get("max_429_retries", 2), h.get("backoff_seconds", 60))


def refresh_attrs(db: DB, cfg: dict):
    """Re-parse every listing. In scope = mentions the club (proxy for 'card in a Lens shirt') or watched."""
    matcher = PlayerMatcher(cfg["players"])
    for row in db.all():
        info = analyse_text(row["title"] or "", row["description"] or "", matcher, cfg["club"]["keywords"])
        attrs = info.to_dict()
        attrs.update(cfg.get("overrides", {}).get(row["id"], {}))
        db.update(row["id"], attrs=json.dumps(attrs, ensure_ascii=False),
                  relevant=int(bool(attrs["club_context"]) or bool(row["watched"])))
    db.commit()


def _queries(cfg: dict, quick: bool) -> list[tuple[str, int]]:
    vcfg = cfg["vinted"]
    out = []
    for q in vcfg["club_queries"]:
        q, pages = (q["q"], q["pages"]) if isinstance(q, dict) else (q, vcfg.get("default_pages", 3))
        out.append((q, min(pages, 2) if quick else pages))
    eras = vcfg.get("player_query_eras", ["current"])
    if not quick and vcfg.get("player_queries", True):
        out += [(player_query(p), vcfg.get("player_query_pages", 1)) for p in cfg["players"] if p.get("era") in eras]
    return list(dict.fromkeys(out))


def run_vinted(cfg: dict, db: DB, ts: str, stats: dict, quick: bool, deep: bool = False):
    vcfg = cfg["vinted"]
    vinted = Vinted(_client(cfg), vcfg)
    known = {r["source_id"] for r in db.conn.execute("SELECT source_id FROM listings WHERE source='vinted'")}
    first_run = deep or not known
    seen_this_run: set[str] = set()
    queries = _queries(cfg, quick)
    try:
        for i, (q, pages) in enumerate(queries):
            print(f"[vinted {i + 1}/{len(queries)}] {q} (≤{pages} p.)")
            # Incremental: results are newest-first, so a page made of known ids means we caught up.
            stop = None if first_run else (lambda ids: sum(1 for x in ids if x in known) >= vcfg.get("known_ratio_stop", 0.9) * len(ids))
            for item in vinted.search(q, pages, stop):
                lid, is_new = db.upsert(item, ts, q)
                seen_this_run.add(lid)
                stats["new" if is_new else "seen"] += 1
            db.commit()

        for url in cfg.get("watch", []):
            sid = Vinted.id_from_url(url)
            if "vinted." not in url or not sid:
                continue
            detail = vinted.item(url)
            lid, _ = db.upsert({"source": "vinted", "source_id": sid, "url": url.split("?")[0], **detail,
                                "image": (detail.get("images") or [detail.get("image_hd")])[0]}, ts, "watch")
            db.update(lid, watched=1, detail_fetched_at=ts)
            seen_this_run.add(lid)
        db.commit()

        # Descriptions: needed when the title lacks the club or the print run ("Panini select RC Lens" -> "/4" lives
        # in the description). Priority: club-query hits whose title does not say "lens", then the priciest.
        refresh_attrs(db, cfg)
        club_qs = [norm(q["q"] if isinstance(q, dict) else q) for q in vcfg["club_queries"]]
        rows = db.conn.execute(
            "SELECT id, url, title, price, queries, relevant FROM listings WHERE source='vinted' "
            "AND detail_fetched_at IS NULL AND status='active' AND price >= ?", (vcfg["detail_min_price"],)).fetchall()

        def priority(r):
            from_club_query = any(norm(q) in club_qs for q in json.loads(r["queries"] or "[]"))
            return (not (from_club_query and not r["relevant"]), not r["relevant"], -(r["price"] or 0))

        todo = sorted(rows, key=priority)[: vcfg["detail_max_per_run"]]
        for n, row in enumerate(todo):
            print(f"[vinted detail {n + 1}/{len(todo)}] {row['title'][:60]}")
            try:
                _apply_detail(db, row["id"], vinted.item(row["url"]), ts, stats)
                stats["details"] += 1
            except RateLimited:
                raise
            except FetchError as e:
                print(f"  ! {e}")
                stats["errors"] += 1
            if n % 20 == 0:
                db.commit()

        # In-scope listings that vanished from search results: sold or removed?
        if not quick:
            gone = db.conn.execute(
                "SELECT id, url FROM listings WHERE source='vinted' AND status IN ('active','reserved') "
                "AND relevant=1 AND last_seen < ? ORDER BY price DESC LIMIT ?", (ts, vcfg.get("gone_check_max", 80))).fetchall()
            for row in gone:
                if row["id"] in seen_this_run:
                    continue
                try:
                    _apply_detail(db, row["id"], vinted.item(row["url"]), ts, stats)
                except RateLimited:
                    raise
                except FetchError as e:
                    print(f"  ! {e}")
                    stats["errors"] += 1
    except RateLimited as e:
        print(f"[vinted] STOP: rate limited ({e}). Partial run kept; try again later.")
        stats["vinted_rate_limited"] = True
    db.commit()


def run_delcampe(cfg: dict, db: DB, ts: str, stats: dict, quick: bool):
    dcfg = cfg["delcampe"]
    delcampe = Delcampe(_client(cfg), dcfg)
    try:
        for q in dcfg["queries"]:
            print(f"[delcampe] {q}")
            for sold in ([False, True] if dcfg.get("sold") else [False]):
                for item in delcampe.search(q, 1 if quick else dcfg["max_pages_per_query"], sold=sold):
                    _, is_new = db.upsert(item, ts, q)
                    stats["new" if is_new else "seen"] += 1
        db.commit()
        refresh_attrs(db, cfg)
        todo = db.conn.execute(
            "SELECT id, url FROM listings WHERE source='delcampe' AND status='sold' AND sold_at IS NULL "
            "AND relevant=1 AND detail_fetched_at IS NULL ORDER BY price DESC LIMIT ?",
            (dcfg["sold_detail_max_per_run"],)).fetchall()
        for row in todo:
            print(f"[delcampe sold date] {row['url'][-70:]}")
            try:
                detail = delcampe.item(row["url"])
            except RateLimited:
                raise
            except FetchError as e:
                print(f"  ! {e}")
                stats["errors"] += 1
                continue
            db.update(row["id"], detail_fetched_at=ts,
                      **{k: v for k, v in detail.items() if k in ("sold_at", "description")})
    except RateLimited as e:
        print(f"[delcampe] STOP: rate limited ({e})")
    db.commit()


def run_ebay(cfg: dict, db: DB, ts: str, stats: dict, state_path: Path):
    ecfg = cfg["ebay"]
    fx = ecfg.get("fx_to_eur", {"EUR": 1.0})

    def on_item(item: dict, q: str):
        rate = fx.get(item.get("currency", "EUR"))
        if rate is None:
            return
        if item.get("currency") != "EUR":
            item["title"] = f"{item['title']} [{item['price']} {item['currency']}]"
            item["price"] = round(item["price"] * rate, 2)
            item["currency"] = "EUR"
        _, is_new = db.upsert(item, ts, q)
        stats["new" if is_new else "seen"] += 1

    if ecfg.get("html_experimental"):
        hcfg = ecfg.get("html", {})
        # Single anonymous session, no account cookies, slow randomised pacing; no 429 retry loop.
        client = Client(cfg["http"]["user_agent"], hcfg.get("delay_seconds", [4, 6]), cfg["http"]["timeout_seconds"], max_429=0)
        client.delay = (0, 0)  # pacing handled inside Ebay.html_search
        stats["ebay_html"] = Ebay(client, ecfg, state_path).run_html(on_item)
    else:
        print("[ebay html] disabled (set ebay.html_experimental=true to enable, at your own risk)")
    if Ebay.api_available():
        api = Ebay(_client(cfg), ecfg)
        for q in ecfg.get("api_queries", []):
            print(f"[ebay api] {q}")
            for item in api.api_search(q):
                on_item(item, q)
    db.commit()


def run(cfg: dict, db: DB, only: set[str] | None = None, quick: bool = False, state_dir: Path | None = None,
        deep: bool = False) -> dict:
    ts = now()
    stats = {"new": 0, "seen": 0, "details": 0, "sold_detected": 0, "removed_detected": 0, "errors": 0}

    def want(name: str) -> bool:
        return (only is None or name in only) and cfg.get(name, {}).get("enabled", True)

    if want("vinted"):
        run_vinted(cfg, db, ts, stats, quick, deep)
    if want("delcampe"):
        run_delcampe(cfg, db, ts, stats, quick)
    if want("ebay"):
        run_ebay(cfg, db, ts, stats, (state_dir or Path("data")) / "ebay_state.json")
    refresh_attrs(db, cfg)
    db.log_run(ts, stats)
    db.commit()
    return stats


def _apply_detail(db: DB, lid: str, detail: dict, ts: str, stats: dict):
    row = db.get(lid)
    status = detail.get("status", row["status"])
    fields: dict = {"detail_fetched_at": ts}
    for k in ("description", "favourites", "brand"):
        if detail.get(k) is not None:
            fields[k] = detail[k]
    if detail.get("images"):
        fields["images"] = json.dumps(detail["images"])
    if detail.get("price") is not None and detail["price"] != row["price"]:
        fields["price"] = detail["price"]
        db.observe(lid, ts, detail["price"], status)
    if status != row["status"]:
        fields["status"] = status
        db.observe(lid, ts, detail.get("price", row["price"]), status)
        if status in ("sold", "removed"):
            # The sale happened between the last sighting and now; the midpoint is the best estimate.
            last = datetime.fromisoformat(row["last_seen"])
            mid = last + (datetime.fromisoformat(ts) - last) / 2
            fields["sold_at"] = mid.replace(microsecond=0).isoformat()
            stats["sold_detected" if status == "sold" else "removed_detected"] += 1
    elif status in ("active", "reserved"):
        fields["last_seen"] = ts
    db.update(lid, **fields)


def import_ebay_files(cfg: dict, db: DB, files: list[Path]) -> dict:
    """Zero-request eBay path: parse search result pages the user saved from a normal browser session."""
    from .sources.ebay import parse_search_html
    ts = now()
    fx = cfg["ebay"].get("fx_to_eur", {"EUR": 1.0})
    stats = {"files": 0, "new": 0, "seen": 0, "sold": 0}
    img_dir = Path(__file__).resolve().parent.parent / "web" / "ebay_img"
    img_dir.mkdir(exist_ok=True)
    for f in files:
        page = f.read_text(errors="replace")
        # The page's own URL says whether it is a sold search; filter links elsewhere in the page also carry LH_Sold.
        origin = re.search(r"saved from url=\(\d+\)(\S+)", page) or re.search(r'<link rel="canonical" href="([^"]+)"', page)
        sold = bool(origin and "LH_Sold=1" in origin.group(1))
        for item in parse_search_html(page, sold):
            item["image"] = _local_image(item.get("image"), f.parent, item["source_id"], img_dir)
            rate = fx.get(item.get("currency", "EUR"))
            if rate is None:
                continue
            if item["currency"] != "EUR":
                item["title"] = f"{item['title']} [{item['price']} {item['currency']}]"
                item["price"], item["currency"] = round(item["price"] * rate, 2), "EUR"
            _, is_new = db.upsert(item, ts, f"import:{f.name}")
            stats["new" if is_new else "seen"] += 1
            stats["sold"] += item["status"] == "sold"
        stats["files"] += 1
    refresh_attrs(db, cfg)
    db.commit()
    return stats


def _local_image(src: str | None, base: Path, item_id: str, img_dir: Path) -> str | None:
    """Saved pages point images at './<page>_files/x.webp': copy them next to the UI (no network)."""
    import shutil
    import urllib.parse as up
    if not src or src.startswith("http"):
        return src
    path = (base / up.unquote(src)).resolve()
    if not path.is_file() or base.resolve() not in path.parents:
        return None
    dest = img_dir / f"{item_id}{path.suffix.lower()}"
    if not dest.exists():
        shutil.copyfile(path, dest)
    return f"ebay_img/{dest.name}"
