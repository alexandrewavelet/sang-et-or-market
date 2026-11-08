# Sang & Or Market

Scrapes RC Lens trading cards (any era — a card is in scope when the listing mentions Lens, our proxy for
"the player is wearing the Lens shirt"), prices each listing against comparable cards, and serves a web UI to browse
the results.

Pure Python 3.11+ standard library — no dependencies.

```bash
python3 -m hoarder run            # scrape every source + analyse -> web/data.json
python3 -m hoarder serve          # http://localhost:8765
python3 -m hoarder scrape --quick # club queries only, 2 pages, no sold-check (fast refresh)
python3 -m hoarder scrape --deep  # ignore the incremental stop (backfill after a partial run)
python3 -m hoarder analyze        # re-parse + re-price only (after editing config.json)
python3 -m unittest               # parser + eBay fixture tests
```

Run it **once a day at most**: sold-detection works by comparing runs, and the sites rate-limit.

## Sources

| Source   | What we get | How |
|----------|-------------|-----|
| Vinted   | Active listings; **sales detected over time** (a tracked listing that disappears from search is re-checked: `sold` / `removed`, dated at the midpoint between the two sightings) | Server-rendered catalog pages (`catalog[]=4875`, cartes à l'unité) + item pages for description/status. Vinted does not expose past sales publicly, so sold history builds up from the first run onward. |
| Delcampe | Active listings + sold items with the exact sale date | Search pages (`display_state=sold_items`) + item page for the date |
| eBay     | **Experimental, off by default.** Sold (`LH_Sold=1&LH_Complete=1`) and active search pages | `ebay.html_experimental: true` in `config.json`. Anonymous session, 4–6 s random delay, ≤ 8 requests per run, one run per day, stops and backs off 48 h on the first 403/429/challenge (state in `data/ebay_state.json`). eBay's ToS forbid automated scraping — personal, low-volume use at your own risk. The parser was validated on **hand-written fixtures** only; save a real page into `tests/fixtures/` on the first successful run. |
| eBay (manual import) | Sold + active, **zero automated request** | Open e.g. `https://www.ebay.fr/sch/i.html?_nkw=rc+lens+panini&LH_Sold=1&LH_Complete=1&_ipg=240` in your browser, save it (Cmd+S, "page HTML"), then `python3 -m hoarder import-ebay ~/Downloads/*.html`. Same parser as the experimental scraper. As of 2026-11-08 the automated path gets a 403 on the very first request, so this is the reliable route. |
| eBay API | Active listings only | Official Browse API when `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` are set. Sold prices are not available through the public API (Finding API retired, Marketplace Insights restricted). |

Rate limits: Vinted answers `429` after a few dozen fast requests. The client paces at 2.5–4 s, honours `Retry-After`,
retries twice, then stops the source for the run and keeps what it collected.

## Pricing model (`hoarder/analyze.py`)

For each in-scope listing, comparables are searched tier by tier, from the closest to the broadest, and the search
stops as soon as `min_comps` are found:

1. `PSN` same player · same product · same print-run band — weight 1.0
2. `PN` same player · same print-run band — 0.75
3. `SN` same product · same band (Lens) — 0.6
4. `PS` same player · same product — 0.5
5. `N` same band (Lens) — 0.35
6. `P` same player (any club, weight × 0.6 outside Lens) — 0.3
7. `S` same product (Lens) — 0.2

Hard filters for every tier: not a lot; same auto / patch / graded status; same parallel and insert; same
numbered-ness (a numbered card is only compared with numbered cards); same era bucket (≤1999, 2000-09, 2010-19, 2020+);
generic products ("Panini", "Topps") are never compared on product alone.

Prices: sold = 1.0, removed (probable sale) = 0.95, asking = × 0.85 (asking prices sit above transaction prices).
Fair value = weighted median, range = weighted IQR. Verdict on price ÷ fair: ≤ 0.75 good deal, ≤ 1.2 fair,
≤ 1.6 above market, otherwise expensive. A gap ≤ 1.5 € is always "fair". When comparables disagree wildly
(IQR ratio > 4) the verdict is only given outside the range. Confidence reflects tier closeness and the number of sold comps.

Print-run bands: `1/1`, `/2-5`, `/6-10`, `/11-25`, `/26-50`, `/51-99`, `/100-199`, `/200-499`, `/500+`, plus
`numérotée (tirage ?)` when the listing says numbered without the number.

## Configuration (`config.json`)

- `watch`: listing URLs to always track and feature at the top of the UI.
- `players`: Lens players (current squad, recent, legends) with `aliases`. Add anyone missing — unmatched in-scope
  listings show up as "Joueur non identifié".
- `vinted.club_queries`: search terms with page depth; `player_query_eras` adds one query per player of those eras.
- `analysis`: window (180 days), asking-price discount, minimum comps, tolerance.

## Layout

```
hoarder/
  fetch.py        polite HTTP client (pacing, 429 backoff)
  parse.py        title/description -> player, product, season, print run, parallel, flags
  sources/        vinted.py, delcampe.py, ebay.py
  db.py           SQLite store (listings + price/status observations + runs)
  scrape.py       orchestration, sold detection
  analyze.py      comparables, fair value, export
web/              static UI "Sang & Or Market": core.js (data, filters, sorting) + index.html/app.js/style.css,
                  data.json (generated) and ebay_img/ (images copied from imported eBay pages)
data/             hoarder.db, logs (git-ignored)
```

## Publishing

`web/` is deployed to GitHub Pages by `.github/workflows/pages.yml` on every push to `main`.
To refresh the published data: `python3 -m hoarder run`, then commit `web/data.json` (and `web/ebay_img/` after an
eBay import) and push.
