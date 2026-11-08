"""CLI: python3 -m hoarder [scrape|analyze|serve|run] ..."""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import sys
import webbrowser
from pathlib import Path

from .analyze import export
from .db import DB
from .scrape import refresh_attrs, run

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(prog="hoarder", description="Scrape, price and browse RC Lens trading cards")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--db", default=str(ROOT / "data" / "hoarder.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scrape", help="collect listings from the sources")
    s.add_argument("--only", nargs="*", choices=["vinted", "delcampe", "ebay"])
    s.add_argument("--quick", action="store_true", help="club queries only, 2 pages max, no sold-check")
    s.add_argument("--deep", action="store_true", help="ignore the incremental stop (backfill)")
    im = sub.add_parser("import-ebay", help="import eBay search pages saved from your browser (Cmd+S)")
    im.add_argument("files", nargs="+")
    sub.add_parser("analyze", help="re-parse listings, compute fair values, write web/data.json")
    v = sub.add_parser("serve", help="serve the web UI")
    v.add_argument("--port", type=int, default=8765)
    v.add_argument("--no-open", action="store_true")
    r = sub.add_parser("run", help="scrape + analyze")
    r.add_argument("--quick", action="store_true")
    r.add_argument("--deep", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    db = DB(Path(args.db))

    if args.cmd in ("scrape", "run"):
        stats = run(cfg, db, set(args.only) if getattr(args, "only", None) else None, args.quick,
                    ROOT / "data", args.deep)
        print("scrape:", stats)
    if args.cmd == "import-ebay":
        from .scrape import import_ebay_files
        print("import-ebay:", import_ebay_files(cfg, db, [Path(f) for f in args.files]))
    if args.cmd in ("analyze", "run", "import-ebay"):
        refresh_attrs(db, cfg)
        data = export(db, cfg, WEB / "data.json")
        n = len(data["listings"])
        priced = sum(1 for l in data["listings"] if l["analysis"]["fair"] is not None)
        print(f"analyze: {n} listings in scope, {priced} with a fair value -> web/data.json")
    if args.cmd == "serve":
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB))
        url = f"http://localhost:{args.port}"
        print(f"serving {WEB} on {url}")
        if not args.no_open:
            webbrowser.open(url)
        http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
