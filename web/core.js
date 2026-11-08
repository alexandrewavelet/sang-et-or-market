/* Ledger core — data, filters, sorting and formatting shared by every UI version.
 * web/index.html + app.js own the rendering ("Sang & Or Market" design).
 * Exposes window.Ledger. No dependencies.
 */
(function () {
  const BASE = new URL(".", document.currentScript.src).href; // folder holding core.js, data.json, ebay_img/

  const VERDICTS = [
    { key: "bonne affaire", label: "Bonne affaire", tone: "good" },
    { key: "prix juste", label: "Prix juste", tone: "fair" },
    { key: "au-dessus", label: "Au-dessus", tone: "high" },
    { key: "cher", label: "Cher", tone: "bad" },
    { key: "enchère", label: "Enchère en cours", tone: "auction" },
    { key: "insuffisant", label: "Pas assez de données", tone: "none" },
  ];
  const STATUS = { active: "En vente", reserved: "Réservée", sold: "Vendue", removed: "Retirée" };
  const ERAS = { current: "Effectif actuel", recent: "Ancien (récent)", legend: "Légende" };
  const SOURCES = { vinted: "Vinted", delcampe: "Delcampe", ebay: "eBay" };
  const BANDS = ["1/1", "/2-5", "/6-10", "/11-25", "/26-50", "/51-99", "/100-199", "/200-499", "/500+", "numérotée (tirage ?)"];
  const SORTS = [
    { key: "deal", label: "Meilleures affaires" },
    { key: "rare", label: "Tirage le plus rare" },
    { key: "price_desc", label: "Prix décroissant" },
    { key: "price_asc", label: "Prix croissant" },
    { key: "recent", label: "Plus récentes" },
    { key: "fav", label: "Les plus suivies" },
  ];

  const DEFAULT_STATE = {
    q: "", status: "active", sort: "deal", player: "", product: "", band: "", era: "", source: "",
    pmin: "", pmax: "", numbered: false, autos: false, patches: false, hideLots: true, hideStickers: true,
    priced: false, verdicts: [],
  };

  const nf0 = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
  const nf2 = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmt = {
    eur: v => v == null ? "—" : (v % 1 ? nf2 : nf0).format(v),
    date: s => s ? new Date(s).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" }) : "—",
    pct: r => r == null ? "—" : `${r >= 1 ? "+" : ""}${Math.round((r - 1) * 100)} %`,
    run: l => l.print_run ? (l.serial ? `${l.serial}/${l.print_run}` : `/${l.print_run}`) : null,
    esc: s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  };
  const norm = s => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

  function img(u) {
    if (!u) return null;
    return /^https?:/.test(u) ? u : BASE + u;
  }

  function enrich(l) {
    const a = l.analysis || { comps: [] };
    l.image = img(l.image);
    l.images = (l.images && l.images.length ? l.images : [l.image]).map(img).filter(Boolean);
    l.isSold = l.status === "sold" || l.status === "removed";
    l.verdict = VERDICTS.find(v => v.key === a.verdict) || VERDICTS[VERDICTS.length - 1];
    l.runLabel = fmt.run(l);
    // 0 (common) .. 1 (one of one): drives foil/rarity effects.
    l.rarity = l.print_run ? Math.max(0.15, Math.min(1, 1.15 - Math.log10(l.print_run) * 0.42)) : (l.is_auto || l.is_patch ? 0.2 : 0);
    l.subtitle = [l.product, l.season, l.parallel, l.special].filter(Boolean).join(" · ");
    l.displayName = l.player || "Joueur non identifié";
    l.searchText = norm([l.title, l.player, l.product, l.parallel, l.season, l.special, l.description].join(" "));
    return l;
  }

  async function load() {
    const res = await fetch(BASE + "data.json", { cache: "no-store" });
    if (!res.ok) throw new Error("data.json introuvable — lance `python3 -m hoarder run`");
    const data = await res.json();
    const byId = new Map();
    data.listings.forEach(l => { enrich(l); byId.set(l.id, l); });
    (data.extra || []).forEach(l => { enrich(l); byId.set(l.id, l); });
    data.byId = byId;
    data.featured = data.listings.filter(l => l.watched);
    return data;
  }

  function filter(list, s) {
    const q = norm(s.q).trim();
    const pmin = parseFloat(s.pmin), pmax = parseFloat(s.pmax);
    const verdicts = new Set(s.verdicts || []);
    return list.filter(l => {
      if (s.status === "active" && l.isSold) return false;
      if (s.status === "sold" && !l.isSold) return false;
      if (s.player && l.player !== s.player) return false;
      if (s.product && l.product !== s.product) return false;
      if (s.band && l.band !== s.band) return false;
      if (s.source && l.source !== s.source) return false;
      if (s.era === "none" ? !!l.player : s.era && l.player_era !== s.era) return false;
      if (s.numbered && !l.print_run) return false;
      if (s.autos && !l.is_auto) return false;
      if (s.patches && !l.is_patch) return false;
      if (s.hideLots && l.is_lot) return false;
      if (s.hideStickers && l.is_sticker) return false;
      if (s.priced && l.analysis.fair == null) return false;
      if (!isNaN(pmin) && (l.price ?? 0) < pmin) return false;
      if (!isNaN(pmax) && (l.price ?? 0) > pmax) return false;
      if (verdicts.size && !verdicts.has(l.analysis.verdict)) return false;
      if (q && !q.split(/\s+/).every(t => l.searchText.includes(t))) return false;
      return true;
    });
  }

  function sort(list, key) {
    const cmp = {
      deal: (a, b) => (a.analysis.ratio ?? 99) - (b.analysis.ratio ?? 99) || (b.price || 0) - (a.price || 0),
      rare: (a, b) => (a.print_run ?? 1e9) - (b.print_run ?? 1e9) || (b.price || 0) - (a.price || 0),
      price_desc: (a, b) => (b.price || 0) - (a.price || 0),
      price_asc: (a, b) => (a.price || 0) - (b.price || 0),
      recent: (a, b) => (b.sold_at || b.first_seen || "").localeCompare(a.sold_at || a.first_seen || ""),
      fav: (a, b) => (b.favourites || 0) - (a.favourites || 0),
    }[key] || (() => 0);
    return [...list].sort(cmp);
  }

  /** Values + counts for the select filters, computed on the given (usually full) list. */
  function facets(list) {
    const count = key => {
      const m = new Map();
      list.forEach(l => { if (l[key] != null && l[key] !== "") m.set(l[key], (m.get(l[key]) || 0) + 1); });
      return [...m].map(([value, n]) => ({ value, n }));
    };
    return {
      players: count("player").sort((a, b) => a.value.localeCompare(b.value, "fr")),
      products: count("product").sort((a, b) => b.n - a.n),
      bands: count("band").sort((a, b) => BANDS.indexOf(a.value) - BANDS.indexOf(b.value)),
      sources: count("source").map(f => ({ ...f, label: SOURCES[f.value] || f.value })),
      eras: Object.entries(ERAS).map(([value, label]) => ({ value, label })).concat([{ value: "none", label: "Non identifié" }]),
      verdicts: VERDICTS, sorts: SORTS,
    };
  }

  function stats(list) {
    const active = list.filter(l => !l.isSold && !l.is_lot && !l.is_sticker);
    return {
      active: active.length,
      numbered: active.filter(l => l.print_run).length,
      autos: active.filter(l => l.is_auto).length,
      sold: list.filter(l => l.isSold).length,
      deals: active.filter(l => l.analysis.verdict === "bonne affaire").length,
      players: new Set(active.map(l => l.player).filter(Boolean)).size,
    };
  }

  /** Comparables grouped by tier, each comp resolved to its listing (title, image, url, source). */
  function compGroups(l, data) {
    const groups = [];
    for (const c of l.analysis.comps || []) {
      let g = groups.find(x => x.tier === c.tier);
      if (!g) groups.push(g = { tier: c.tier, label: c.tier_label, items: [] });
      g.items.push({ ...c, listing: data.byId.get(c.id) || null });
    }
    return groups;
  }

  /** Points for a price-over-time chart: comps (date, price, status, weight) + the listing itself. */
  function series(l, data) {
    const pts = (l.analysis.comps || []).filter(c => c.date && c.price)
      .map(c => ({ t: +new Date(c.date), price: c.price, status: c.status, weight: c.weight, listing: data.byId.get(c.id) }));
    return { points: pts, self: { t: +new Date(l.first_seen), price: l.price }, fair: l.analysis.fair, low: l.analysis.low, high: l.analysis.high };
  }

  /** State persisted per UI version (localStorage may be unavailable: never throws). */
  function store(ns) {
    const key = `ledger:${ns}`;
    return {
      load() {
        try { return { ...DEFAULT_STATE, ...JSON.parse(localStorage.getItem(key) || "{}") }; } catch (_) { return { ...DEFAULT_STATE }; }
      },
      save(state) { try { localStorage.setItem(key, JSON.stringify(state)); } catch (_) {} },
      reset() { try { localStorage.removeItem(key); } catch (_) {} return { ...DEFAULT_STATE }; },
    };
  }

  /** Count of non-default filters (for a badge on the filters button). */
  function activeFilters(s) {
    return ["player", "product", "band", "era", "source", "pmin", "pmax"].filter(k => s[k]).length
      + ["numbered", "autos", "patches", "priced"].filter(k => s[k]).length
      + (s.verdicts || []).length + (s.hideLots ? 0 : 1) + (s.hideStickers ? 0 : 1);
  }

  /** Deep link helpers: #card=<id> */
  const hash = {
    get: () => { const m = location.hash.match(/^#card=(.+)$/); return m ? decodeURIComponent(m[1]) : null; },
    set: id => history.replaceState(null, "", id ? "#card=" + encodeURIComponent(id) : location.pathname + location.search),
  };

  const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  window.Ledger = { load, filter, sort, facets, stats, compGroups, series, store, activeFilters, hash, fmt, norm,
    reducedMotion, VERDICTS, STATUS, ERAS, SOURCES, BANDS, SORTS, DEFAULT_STATE };
})();
