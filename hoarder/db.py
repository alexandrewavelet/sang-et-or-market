from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT,
    title TEXT,
    description TEXT,
    brand TEXT,
    condition TEXT,
    price REAL,
    price_total REAL,
    currency TEXT,
    image TEXT,
    images TEXT,
    favourites INTEGER,
    status TEXT,
    first_seen TEXT,
    last_seen TEXT,
    sold_at TEXT,
    detail_fetched_at TEXT,
    queries TEXT,
    attrs TEXT,
    relevant INTEGER DEFAULT 0,
    watched INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS observations (
    listing_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    price REAL,
    status TEXT
);
CREATE INDEX IF NOT EXISTS obs_listing ON observations(listing_id);
CREATE TABLE IF NOT EXISTS runs (
    ts TEXT PRIMARY KEY,
    stats TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=120)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def get(self, lid: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()

    def upsert(self, item: dict, ts: str, query: str | None = None) -> tuple[str, bool]:
        """Insert or refresh a listing seen in a search/item page. Returns (id, is_new)."""
        lid = f"{item['source']}:{item['source_id']}"
        row = self.get(lid)
        if row is None:
            self.conn.execute(
                """INSERT INTO listings (id, source, source_id, url, title, description, brand, condition, price,
                   price_total, currency, image, images, favourites, status, first_seen, last_seen, sold_at, queries)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lid, item["source"], item["source_id"], item.get("url"), item.get("title"), item.get("description"),
                 item.get("brand"), item.get("condition"), item.get("price"), item.get("price_total"),
                 item.get("currency", "EUR"), item.get("image"), json.dumps(item.get("images") or []),
                 item.get("favourites"), item.get("status", "active"), ts, ts, item.get("sold_at"),
                 json.dumps([query] if query else [])))
            self.observe(lid, ts, item.get("price"), item.get("status", "active"))
            return lid, True
        queries = set(json.loads(row["queries"] or "[]"))
        if query:
            queries.add(query)
        fields = {k: item[k] for k in ("url", "title", "brand", "condition", "price", "price_total", "image",
                                        "favourites", "status", "sold_at", "description") if item.get(k) is not None}
        if item.get("images"):
            fields["images"] = json.dumps(item["images"])
        fields["queries"] = json.dumps(sorted(queries))
        if item.get("status", "active") in ("active", "reserved"):
            fields["last_seen"] = ts
        self.update(lid, **fields)
        if (item.get("price") is not None and item["price"] != row["price"]) or \
                (item.get("status") and item["status"] != row["status"]):
            self.observe(lid, ts, item.get("price", row["price"]), item.get("status", row["status"]))
        return lid, False

    def update(self, lid: str, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE listings SET {cols} WHERE id=?", (*fields.values(), lid))

    def observe(self, lid: str, ts: str, price: float | None, status: str | None):
        self.conn.execute("INSERT INTO observations VALUES (?,?,?,?)", (lid, ts, price, status))

    def all(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM listings").fetchall()

    def history(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for r in self.conn.execute("SELECT * FROM observations ORDER BY ts"):
            out.setdefault(r["listing_id"], []).append({"ts": r["ts"], "price": r["price"], "status": r["status"]})
        return out

    def log_run(self, ts: str, stats: dict):
        self.conn.execute("INSERT OR REPLACE INTO runs VALUES (?,?)", (ts, json.dumps(stats)))

    def runs(self) -> list[dict]:
        return [{"ts": r["ts"], **json.loads(r["stats"])} for r in
                self.conn.execute("SELECT * FROM runs ORDER BY ts DESC LIMIT 30")]

    def commit(self):
        self.conn.commit()
