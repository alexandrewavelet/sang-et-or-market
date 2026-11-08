"""Comparable-based fair value for each in-scope listing, plus export for the web UI."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import DB

# (key, label, weight, attributes that must match). Numbering band must always match when the
# target is numbered: print run dominates value, so an unnumbered comp says little about a /4.
TIERS = [
    ("PSN", "Même joueur · même gamme · même tirage", 1.0, ("player", "product", "band")),
    ("PN", "Même joueur · même tirage", 0.75, ("player", "band")),
    ("SN", "Même gamme · même tirage (Lens)", 0.6, ("product", "band")),
    ("PS", "Même joueur · même gamme", 0.5, ("player", "product")),
    ("SN~", "Même gamme · tirage voisin (Lens)", 0.3, ("product", "band~")),
    ("N", "Même tirage (Lens)", 0.35, ("band",)),
    ("P", "Même joueur", 0.3, ("player",)),
    ("S", "Même gamme (Lens)", 0.2, ("product",)),
]
# Too broad to compare on their own ("Panini" covers stickers, chrome, prizm...).
GENERIC_PRODUCTS = {"Panini", "Topps", None}
STATUS_FACTOR = {"sold": 1.0, "removed": 0.95, "active": None, "reserved": 0.95}


def _wquantile(points: list[tuple[float, float]], q: float) -> float:
    pts = sorted(points)
    total = sum(w for _, w in pts)
    acc = 0.0
    for v, w in pts:
        acc += w
        if acc >= q * total:
            return v
    return pts[-1][0]


def _row_dict(row, history: dict) -> dict:
    attrs = json.loads(row["attrs"] or "{}")
    images = json.loads(row["images"] or "[]")
    return {
        "id": row["id"], "source": row["source"], "url": row["url"], "title": row["title"],
        "description": (row["description"] or "")[:400], "price": row["price"], "price_total": row["price_total"],
        "currency": row["currency"], "image": (images[0] if images else row["image"]), "images": images,
        "favourites": row["favourites"], "status": row["status"], "condition": row["condition"],
        "first_seen": row["first_seen"], "last_seen": row["last_seen"], "sold_at": row["sold_at"],
        "relevant": bool(row["relevant"]), "watched": bool(row["watched"]), **attrs,
        "history": history.get(row["id"], []),
    }


def _comparable(target: dict, other: dict, attrs: tuple[str, ...]) -> bool:
    if other["id"] == target["id"] or other["is_lot"] or not other["price"] or other.get("is_auction"):
        return False
    if other["is_auto"] != target["is_auto"] or other["is_patch"] != target["is_patch"] \
            or bool(other["graded"]) != bool(target["graded"]):
        return False
    if (target["band"] is not None) != (other["band"] is not None):
        return False
    # Parallel colour must match for unnumbered cards; for numbered ones the print run already carries the rarity.
    if target["band"] is None and (target.get("parallel") or None) != (other.get("parallel") or None):
        return False
    if target.get("is_sticker") != other.get("is_sticker"):
        return False
    if (target.get("special") or None) != (other.get("special") or None):
        return False
    tb, ob = target.get("era_bucket"), other.get("era_bucket")
    if tb and ob and tb != ob:
        return False
    if "product" in attrs and "player" not in attrs and target["product"] in GENERIC_PRODUCTS:
        return False
    if target["band"] is not None and "band" not in attrs and "band~" not in attrs:
        return False
    if "band~" in attrs:
        bands = ["1/1", "/2-5", "/6-10", "/11-25", "/26-50", "/51-99", "/100-199", "/200-499", "/500+"]
        if target["band"] not in bands or other["band"] not in bands \
                or abs(bands.index(target["band"]) - bands.index(other["band"])) != 1:
            return False
        attrs = tuple(a for a in attrs if a != "band~")
    for a in attrs:
        if target[a] is None or other[a] != target[a]:
            return False
    return True


def fair_value(target: dict, pool: list[dict], cfg: dict) -> dict:
    acfg = cfg["analysis"]
    min_comps = acfg["min_comps"]
    used: list[dict] = []
    tiers_used: list[str] = []
    taken: set[str] = set()
    for key, label, weight, attrs in TIERS:
        found = [o for o in pool if o["id"] not in taken and _comparable(target, o, attrs)]
        # Same player outside a Lens context is still a useful signal; other tiers stay Lens-only.
        found = [o for o in found if o["relevant"] or "player" in attrs]
        if not found:
            continue
        # Keep the most informative comps of the tier: concluded sales first, then the most recent.
        found.sort(key=lambda o: (o["status"] not in ("sold", "removed"), "~" if not (o["sold_at"] or o["last_seen"]) else "",
                                  -(datetime.fromisoformat((o["sold_at"] or o["last_seen"]).replace("Z", "+00:00")).timestamp()
                                    if (o["sold_at"] or o["last_seen"]) else 0)))
        found = found[: acfg.get("max_comps_per_tier", 12)]
        for o in found:
            taken.add(o["id"])
            factor = STATUS_FACTOR.get(o["status"]) or acfg["asking_discount"]
            used.append({"id": o["id"], "tier": key, "tier_label": label, "weight": weight * (1 if o["relevant"] else 0.6),
                         "price": o["price"], "adj_price": round(o["price"] * factor, 2), "status": o["status"],
                         "date": o["sold_at"] or o["last_seen"]})
        tiers_used.append(key)
        # Stop widening as soon as we have enough evidence: broader tiers only add noise.
        if len(used) >= min_comps:
            break

    result = {"comps": used, "tiers": tiers_used, "n_comps": len(used),
              "n_sold": sum(1 for u in used if u["status"] in ("sold", "removed"))}
    if target.get("is_auction"):
        fair = _wquantile([(u["adj_price"], u["weight"]) for u in used], 0.5) if len(used) >= 2 else None
        result.update(fair=round(fair, 2) if fair else None, low=None, high=None, verdict="enchère",
                      confidence="faible" if fair else "aucune", ratio=None)
        return result
    if len(used) < 2 or not target["price"]:
        result.update(fair=None, low=None, high=None, verdict="insuffisant", confidence="aucune", ratio=None)
        return result
    # Closest evidence dominates: broader tiers may weigh at most half of the same-player/same-run comps,
    # otherwise twelve "neighbour run, other player" listings drown the one exact match.
    close = sum(u["weight"] for u in used if u["tier"] in ("PSN", "PN"))
    broad = sum(u["weight"] for u in used if u["tier"] not in ("PSN", "PN"))
    if close and broad > close * 0.5:
        k = close * 0.5 / broad
        for u in used:
            if u["tier"] not in ("PSN", "PN"):
                u["weight"] = round(u["weight"] * k, 3)
    pts = [(u["adj_price"], u["weight"]) for u in used]
    fair = _wquantile(pts, 0.5)
    low, high = _wquantile(pts, 0.25), _wquantile(pts, 0.75)
    ratio = target["price"] / fair if fair else None
    if ratio is None:
        verdict = "insuffisant"
    elif abs(target["price"] - fair) <= acfg.get("abs_tolerance_eur", 1.5):
        verdict = "prix juste"  # a 1 € gap on a 2 € card is noise, not a signal
    elif low and high / low > 4 and low <= target["price"] <= high:
        verdict = "prix juste"  # comps disagree wildly: only call it when outside the whole range
    elif ratio <= 0.75:
        verdict = "bonne affaire"
    elif ratio <= 1.2:
        verdict = "prix juste"
    elif ratio <= 1.6:
        verdict = "au-dessus"
    else:
        verdict = "cher"
    strong = sum(1 for u in used if u["tier"] in ("PSN", "PN"))
    broad_only = tiers_used[0] in ("N", "P", "S")
    if broad_only or (low and high / low > 4):
        confidence = "faible"
    elif strong >= 4 and result["n_sold"] >= 2:
        confidence = "haute"
    elif strong >= 2 or (len(used) >= min_comps and tiers_used[0] in ("PSN", "PN", "SN")):
        confidence = "moyenne"
    else:
        confidence = "faible"
    result.update(fair=round(fair, 2), low=round(low, 2), high=round(high, 2), verdict=verdict,
                  confidence=confidence, ratio=round(ratio, 2) if ratio else None)
    return result


def build(db: DB, cfg: dict) -> dict:
    history = db.history()
    window = (datetime.now(timezone.utc) - timedelta(days=cfg["analysis"]["window_days"])).isoformat()
    rows = [_row_dict(r, history) for r in db.all()]
    # Pool: everything still on sale + sales/removals inside the analysis window.
    pool = [r for r in rows if r["status"] in ("active", "reserved") or (r["sold_at"] or "") >= window]
    listings = []
    for r in rows:
        if not r["relevant"]:
            continue
        if r["status"] not in ("active", "reserved") and (r["sold_at"] or "") < window and not r["watched"]:
            continue
        r["analysis"] = fair_value(r, pool, cfg)
        listings.append(r)
    referenced = {c["id"] for r in listings for c in r["analysis"]["comps"]}
    by_id = {r["id"]: r for r in rows}
    extra = [by_id[i] for i in referenced if i in by_id and not by_id[i]["relevant"]]
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "club": cfg["club"]["name"],
        "window_days": cfg["analysis"]["window_days"],
        "runs": db.runs(),
        "listings": listings,
        "extra": [{k: v for k, v in e.items() if k != "history"} for e in extra],
        "tiers": [{"key": k, "label": l, "weight": w} for k, l, w, _ in TIERS],
        "sources": {
            "vinted": "Annonces actives + ventes détectées (suivi entre deux passages)",
            "delcampe": "Annonces actives + objets vendus (date de vente)",
            "ebay": "API officielle Browse (annonces actives) si EBAY_CLIENT_ID/SECRET configurés",
        },
    }


def export(db: DB, cfg: dict, out: Path) -> dict:
    data = build(db, cfg)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return data
