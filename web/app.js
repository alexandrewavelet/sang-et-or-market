/* Sang & Or Market — "sport" UI. Rendering only; data/filter logic lives in core.js (window.Ledger). */
(function () {
  "use strict";
  const L = window.Ledger;
  const { fmt } = L;
  const esc = fmt.esc;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const store = L.store("sport");
  const CHUNK = 36;

  let data, all, listingIds, facets, tierInfo = {};
  let state = store.load();
  let view = [];
  let rendered = 0;
  let observer;

  /* ---------------- helpers ---------------- */
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  /** 0–100 "deal score", 50 = fair price. ratio = price / fair. */
  function dealScore(l) {
    const r = l.analysis && l.analysis.ratio;
    if (r == null || !(r > 0)) return null;
    // Raw position from the ratio, then kept inside the verdict's band so the score never contradicts the verdict.
    const raw = 50 - 40 * Math.log2(r);
    const band = { good: [72, 99], fair: [45, 71], high: [28, 44], bad: [1, 27] }[l.verdict.tone] || [0, 100];
    return Math.round(clamp(raw, band[0], band[1]));
  }
  const toneOf = l => l.verdict.tone;
  const SRC_LABEL = s => L.SOURCES[s] || s;
  const ICON = {
    x: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    ext: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
    chart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19h16M7 15l4-5 3 3 5-7"/></svg>',
    tick: '<svg class="tick" viewBox="0 0 16 16" aria-hidden="true"><path d="m3 8.5 3 3 7-7"/></svg>',
    card: '<svg viewBox="0 0 48 48" aria-hidden="true"><rect x="10" y="5" width="28" height="38" rx="4"/><circle cx="24" cy="20" r="6"/><path d="M15 36c2-5 5-7 9-7s7 2 9 7"/></svg>',
    eye: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3C4 3 1.5 6.5 1 8c.5 1.5 3 5 7 5s6.5-3.5 7-5c-.5-1.5-3-5-7-5Zm0 8a3 3 0 1 1 0-6 3 3 0 0 1 0 6Z"/></svg>',
  };
  const placeholder = (txt = "Pas d'image") => `<div class="ph">${ICON.card.replace("<svg", '<svg')}<span>${esc(txt)}</span></div>`;
  const imgTag = (src, alt, cls = "") => src
    ? `<img ${cls ? `class="${cls}"` : ""} src="${esc(src)}" alt="${esc(alt)}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
    : placeholder();

  // Graceful image fallback (capture phase: error events don't bubble).
  document.addEventListener("error", e => {
    const t = e.target;
    if (t.tagName !== "IMG" || t.dataset.failed) return;
    t.dataset.failed = "1";
    const ph = document.createElement("div");
    ph.innerHTML = placeholder("Image indisponible");
    t.replaceWith(ph.firstChild);
  }, true);
  document.addEventListener("load", e => { if (e.target.tagName === "IMG") e.target.classList.add("is-loaded"); }, true);

  function flagsFor(l, { sold = true } = {}) {
    const f = [];
    if (l.is_auto) f.push('<span class="flag flag--auto">Auto</span>');
    if (l.is_patch) f.push('<span class="flag flag--patch">Patch</span>');
    if (l.is_auction) f.push('<span class="flag flag--auction">Enchère</span>');
    if (l.graded) f.push(`<span class="flag flag--graded">${esc(typeof l.graded === "string" ? l.graded : "Gradée")}</span>`);
    if (l.is_rookie) f.push('<span class="flag">Rookie</span>');
    if (sold && l.isSold) f.push(`<span class="flag flag--sold">${esc(L.STATUS[l.status] || "Vendue")}</span>`);
    return f.join("");
  }
  const ring = (l, cls = "") => {
    const s = dealScore(l);
    return s == null
      ? `<div class="ring ring--na ${cls}" data-tone="none" style="--v:0" role="img" aria-label="Score indisponible"><b>N/A</b></div>`
      : `<div class="ring ${cls}" data-tone="${toneOf(l)}" style="--v:${s}" role="img" aria-label="Score affaire ${s} sur 100"><b>${s}</b></div>`;
  };
  const runBadge = l => l.runLabel ? `<span class="card__run ${l.print_run <= 10 ? "is-rare" : ""}">${esc(l.runLabel)}</span>` : "";
  const srcTag = s => `<span class="src" data-src="${esc(s)}"><i aria-hidden="true"></i>${esc(SRC_LABEL(s))}</span>`;

  /* Scoreboard ticker */
  function tickTo(el, to) {
    const dur = 1100;
    if (L.reducedMotion()) { el.textContent = to; return; }
    const t0 = performance.now();
    const step = t => {
      const p = clamp((t - t0) / dur, 0, 1);
      const e = 1 - Math.pow(1 - p, 4);
      el.textContent = Math.round(to * e);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /* ---------------- boot ---------------- */
  L.load().then(d => {
    data = d;
    all = d.listings;
    listingIds = new Set(all.map(l => l.id));
    (d.tiers || []).forEach(t => { tierInfo[t.key] = t; });
    facets = L.facets(all);
    $("#loader").remove();
    renderHeader();
    renderTicker();
    renderFeatured();
    buildFilters();
    syncControls();
    render();
    window.addEventListener("hashchange", routeHash);
    routeHash();
  }).catch(err => {
    const el = $("#loader");
    el.classList.add("is-error");
    el.textContent = err.message || "Erreur de chargement";
  });

  /* ---------------- header ---------------- */
  function renderHeader() {
    const s = L.stats(all);
    $$("[data-count]").forEach(el => tickTo(el, s[el.dataset.count] ?? 0));
    const run = (data.runs || [])[0];
    const srcs = [...new Set(all.map(l => l.source))].map(SRC_LABEL).join(", ");
    $("#heroMeta").textContent = `Mis à jour le ${fmt.date(data.generated_at)} · ${all.length} annonces suivies · ${srcs}`
      + (run ? ` · dernier passage : ${run.new} nouvelles` : "");
    $("#foot").innerHTML = `<p><strong>Comment lire le score ?</strong> 50 = prix juste face aux comparables pondérés ; au-dessus, la carte est moins chère que sa cote ; en dessous, plus chère. Fenêtre d'analyse : ${esc(data.window_days)} jours.</p>`
      + Object.entries(data.sources || {}).map(([k, v]) => `<p><strong>${esc(SRC_LABEL(k))}</strong> — ${esc(v)}</p>`).join("");
  }

  function renderTicker() {
    const deals = L.sort(all.filter(l => !l.isSold && !l.is_lot && !l.is_sticker && l.analysis.verdict === "bonne affaire"), "deal").slice(0, 16);
    const track = $("#tickerTrack");
    if (!deals.length) { $("#ticker").hidden = true; return; }
    const item = (l, dup) => `<a class="ticker__item" href="#card=${encodeURIComponent(l.id)}" ${dup ? 'tabindex="-1" aria-hidden="true"' : ""}>
      <b>${esc(l.displayName)}</b><span class="num">${fmt.eur(l.price)}</span><span class="delta">${fmt.pct(l.analysis.ratio)}</span></a>`;
    track.innerHTML = `<div class="ticker__rail">${deals.map(l => item(l, false)).join("")}${deals.map(l => item(l, true)).join("")}</div>`;
    const btn = $("#tickerPause");
    btn.addEventListener("click", () => {
      const p = $("#ticker").classList.toggle("is-paused");
      btn.setAttribute("aria-pressed", String(p));
      btn.setAttribute("aria-label", p ? "Reprendre le défilement" : "Mettre le défilement en pause");
    });
  }

  /* ---------------- featured ---------------- */
  function renderFeatured() {
    const f = data.featured || [];
    if (!f.length) return;
    $("#featured").hidden = false;
    $("#featuredList").innerHTML = f.map(l => {
      const a = l.analysis;
      const s = dealScore(l);
      const note = a.fair == null
        ? `Pas encore de cote fiable : ${a.n_comps} annonce${a.n_comps > 1 ? "s" : ""} comparable${a.n_comps > 1 ? "s" : ""} seulement${a.tiers && a.tiers.length ? ` (${esc(a.tiers.map(t => tierInfo[t]?.label || t).join(", "))})` : ""}. Carte rare : le marché se construit à chaque passage.`
        : `Cote estimée entre ${fmt.eur(a.low)} et ${fmt.eur(a.high)} à partir de ${a.n_comps} comparables (${a.n_sold} vendus). Confiance : ${esc(a.confidence)}.`;
      return `<article class="feat" aria-label="${esc(l.displayName)}">
        <div class="feat__media">
          <button class="feat__card" type="button" data-open="${esc(l.id)}" aria-label="Voir l'analyse de ${esc(l.displayName)}">${imgTag(l.image, l.title)}</button>
          ${l.runLabel ? `<span class="feat__run">${esc(l.runLabel)}</span>` : ""}
        </div>
        <div class="feat__body">
          <div class="feat__eyebrow">
            <span class="tag-watch">${ICON.eye}Suivie</span>
            <span class="verdict" data-tone="${toneOf(l)}">${esc(l.verdict.label)}</span>
            ${flagsFor(l)}
          </div>
          <div>
            <h3 class="feat__name">${esc(l.displayName)}</h3>
            <p class="feat__sub">${esc(l.subtitle || l.title)}</p>
          </div>
          <dl class="feat__stats">
            <div class="fstat fstat--price"><dt>Prix demandé</dt><dd>${fmt.eur(l.price)}</dd></div>
            <div class="fstat"><dt>Cote</dt><dd>${a.fair == null ? "—" : fmt.eur(a.fair)}</dd></div>
            <div class="fstat"><dt>Score affaire</dt><dd>${s == null ? "—" : s}<small>${s == null ? "" : " /100"}</small></dd></div>
            <div class="fstat"><dt>Comparables</dt><dd>${a.n_comps}<small> · ${a.n_sold} vendu${a.n_sold > 1 ? "s" : ""}</small></dd></div>
          </dl>
          <p class="feat__note">${note}</p>
          <div class="feat__cta">
            <button class="btn btn--gold" type="button" data-open="${esc(l.id)}">${ICON.chart}Voir l'analyse</button>
            <a class="btn btn--outline-light" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">Ouvrir l'annonce ${SRC_LABEL(l.source)} ${ICON.ext}<span class="sr-only">(nouvel onglet)</span></a>
          </div>
        </div>
      </article>`;
    }).join("");
  }

  /* ---------------- filters ---------------- */
  const TOGGLES = [
    ["numbered", "Numérotées"], ["autos", "Autographes"], ["patches", "Patchs"], ["priced", "Avec cote"],
    ["hideLots", "Masquer lots"], ["hideStickers", "Masquer stickers"],
  ];

  function fillSelect(id, items, allLabel) {
    $("#" + id).innerHTML = `<option value="">${esc(allLabel)}</option>` + items.map(i =>
      `<option value="${esc(i.value)}">${esc(i.label || i.value)}${i.n != null ? ` (${i.n})` : ""}</option>`).join("");
  }

  function buildFilters() {
    $("#sort").innerHTML = L.SORTS.map(s => `<option value="${s.key}">${esc(s.label)}</option>`).join("");
    fillSelect("player", facets.players, "Tous les joueurs");
    fillSelect("product", facets.products, "Toutes les gammes");
    fillSelect("band", facets.bands, "Tous les tirages");
    fillSelect("era", facets.eras, "Toutes les époques");
    fillSelect("source", facets.sources, "Toutes les sources");
    $("#verdictChips").innerHTML = L.VERDICTS.map(v =>
      `<button type="button" class="chip" data-verdict="${esc(v.key)}" data-tone="${v.tone}" aria-pressed="false"><span class="dot" aria-hidden="true"></span>${esc(v.label)} <span class="n"></span></button>`).join("");
    $("#toggleChips").innerHTML = TOGGLES.map(([k, label]) =>
      `<button type="button" class="chip" data-toggle="${k}" aria-pressed="false">${ICON.tick}${esc(label)}</button>`).join("");

    let qTimer;
    $("#q").addEventListener("input", e => { clearTimeout(qTimer); qTimer = setTimeout(() => set({ q: e.target.value }), 160); });
    $("#sort").addEventListener("change", e => set({ sort: e.target.value }));
    ["player", "product", "band", "era", "source"].forEach(k => $("#" + k).addEventListener("change", e => set({ [k]: e.target.value })));
    ["pmin", "pmax"].forEach(k => {
      let t; $("#" + k).addEventListener("input", e => { clearTimeout(t); t = setTimeout(() => set({ [k]: e.target.value }), 250); });
    });
    $("#statusSeg").addEventListener("click", e => { const b = e.target.closest("[data-status]"); if (b) set({ status: b.dataset.status }); });
    $("#statusSeg").addEventListener("keydown", e => {
      if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
      const keys = ["active", "sold", "all"];
      const i = (keys.indexOf(state.status) + (e.key === "ArrowRight" ? 1 : 2)) % 3;
      set({ status: keys[i] });
      $(`#statusSeg [data-status="${keys[i]}"]`).focus();
      e.preventDefault();
    });
    $("#verdictChips").addEventListener("click", e => {
      const b = e.target.closest("[data-verdict]"); if (!b) return;
      const v = new Set(state.verdicts); v.has(b.dataset.verdict) ? v.delete(b.dataset.verdict) : v.add(b.dataset.verdict);
      set({ verdicts: [...v] });
    });
    $("#toggleChips").addEventListener("click", e => {
      const b = e.target.closest("[data-toggle]"); if (b) set({ [b.dataset.toggle]: !state[b.dataset.toggle] });
    });
    $("#activeChips").addEventListener("click", e => {
      const b = e.target.closest("[data-clear]"); if (!b) return;
      const [k, v] = b.dataset.clear.split("|");
      if (k === "verdicts") set({ verdicts: state.verdicts.filter(x => x !== v) });
      else set({ [k]: L.DEFAULT_STATE[k] });
    });
    const reset = () => { state = store.reset(); syncControls(); render(); };
    ["#resetBtn", "#resetBtn2", "#resetBtn3"].forEach(s => $(s).addEventListener("click", reset));

    // Mobile filter sheet
    const panel = $("#filters"), scrim = $("#filtersScrim"), openBtn = $("#filtersBtn");
    const open = () => { panel.classList.add("is-open"); scrim.hidden = false; openBtn.setAttribute("aria-expanded", "true"); setTimeout(() => $("#filtersClose").focus(), 50); };
    const close = () => { panel.classList.remove("is-open"); scrim.hidden = true; openBtn.setAttribute("aria-expanded", "false"); openBtn.focus(); };
    openBtn.addEventListener("click", open);
    $("#filtersClose").addEventListener("click", close);
    $("#filtersApply").addEventListener("click", () => { close(); $("#grid").focus({ preventScroll: true }); });
    scrim.addEventListener("click", close);
    panel.addEventListener("keydown", e => { if (e.key === "Escape" && panel.classList.contains("is-open")) close(); });

    // Sticky toolbar shadow
    const tb = $("#toolbar");
    const onScroll = () => tb.classList.toggle("is-stuck", tb.getBoundingClientRect().top <= 0 && window.scrollY > 200);
    window.addEventListener("scroll", onScroll, { passive: true });

    // Open detail from anywhere
    document.addEventListener("click", e => {
      const b = e.target.closest("[data-open]");
      if (b) { e.preventDefault(); openDetail(b.dataset.open); }
    });
  }

  function set(patch) {
    state = { ...state, ...patch };
    store.save(state);
    syncControls();
    render();
  }

  function syncControls() {
    const v = (id, val) => { const el = $("#" + id); if (el && document.activeElement !== el) el.value = val ?? ""; };
    ["q", "sort", "player", "product", "band", "era", "source", "pmin", "pmax"].forEach(k => v(k, state[k]));
    const keys = ["active", "sold", "all"];
    $$("#statusSeg [data-status]").forEach(b => {
      const on = b.dataset.status === state.status;
      b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1;
    });
    $(".seg__thumb").style.transform = `translateX(${Math.max(0, keys.indexOf(state.status)) * 100}%)`;
    $$("#verdictChips [data-verdict]").forEach(b => b.setAttribute("aria-pressed", String(state.verdicts.includes(b.dataset.verdict))));
    $$("#toggleChips [data-toggle]").forEach(b => b.setAttribute("aria-pressed", String(!!state[b.dataset.toggle])));
    const n = L.activeFilters(state) + (state.q ? 1 : 0);
    const c = $("#filterCount");
    if (n) { if (c.hidden || c.textContent !== String(n)) { c.hidden = false; c.textContent = n; c.style.animation = "none"; void c.offsetWidth; c.style.animation = ""; } }
    else c.hidden = true;
    $("#resetBtn").hidden = !n;
  }

  function activeChipList() {
    const out = [];
    const opt = (id, val) => { const o = $(`#${id} option[value="${CSS.escape(val)}"]`); return o ? o.textContent.replace(/ \(\d+\)$/, "") : val; };
    if (state.q) out.push(["q", `« ${state.q} »`]);
    ["player", "product", "band", "era", "source"].forEach(k => { if (state[k]) out.push([k, opt(k, state[k])]); });
    if (state.pmin) out.push(["pmin", `≥ ${fmt.eur(+state.pmin)}`]);
    if (state.pmax) out.push(["pmax", `≤ ${fmt.eur(+state.pmax)}`]);
    TOGGLES.forEach(([k, label]) => {
      const def = L.DEFAULT_STATE[k];
      if (!!state[k] !== !!def) out.push([k, def ? label.replace("Masquer", "Avec") : label]);
    });
    state.verdicts.forEach(v => out.push([`verdicts|${v}`, (L.VERDICTS.find(x => x.key === v) || {}).label || v]));
    return out;
  }

  /* ---------------- grid ---------------- */
  function render() {
    view = L.sort(L.filter(all, state), state.sort);
    // Verdict chip counts (all filters except verdicts)
    const base = L.filter(all, { ...state, verdicts: [] });
    const counts = {};
    base.forEach(l => { counts[l.analysis.verdict] = (counts[l.analysis.verdict] || 0) + 1; });
    $$("#verdictChips [data-verdict]").forEach(b => { b.querySelector(".n").textContent = counts[b.dataset.verdict] || 0; });

    const sortLabel = (L.SORTS.find(s => s.key === state.sort) || L.SORTS[0]).label;
    $("#resultCount").innerHTML = `<span class="num">${view.length}</span>carte${view.length > 1 ? "s" : ""} · ${esc(sortLabel.toLowerCase())}`;
    $("#applyCount").textContent = view.length;
    $("#activeChips").innerHTML = activeChipList().map(([k, label]) =>
      `<button type="button" class="achip" data-clear="${esc(k)}" aria-label="Retirer le filtre ${esc(label)}">${esc(label)}${ICON.x}</button>`).join("");

    const grid = $("#grid");
    grid.innerHTML = "";
    rendered = 0;
    $("#empty").hidden = view.length > 0;
    appendChunk();
    setupObserver();
  }

  function cardHTML(l, i) {
    const a = l.analysis;
    const fair = a.fair != null
      ? `Cote <b>${fmt.eur(a.fair)}</b> · ${a.n_comps} comp.`
      : `${a.n_comps ? `${a.n_comps} comp. · ` : ""}pas de cote`;
    return `<li class="card ${l.isSold ? "is-sold" : ""}" style="--i:${i}">
      <button type="button" class="card__btn" data-open="${esc(l.id)}" aria-label="${esc(l.displayName)}, ${esc(fmt.eur(l.price))}, ${esc(l.verdict.label)}">
        <div class="card__media">
          ${imgTag(l.image, "")}
          ${runBadge(l)}
          <div class="card__score">${ring(l)}</div>
          <div class="card__flags">${flagsFor(l)}</div>
        </div>
        <div class="card__body">
          <div class="card__src">${srcTag(l.source)}${l.favourites ? `<span>♥ ${l.favourites}</span>` : ""}</div>
          <h3 class="card__player">${esc(l.displayName)}</h3>
          <p class="card__sub">${esc(l.subtitle || l.title)}</p>
          <div class="card__price-row">
            <span class="card__price">${fmt.eur(l.price)}</span>
            <span class="verdict" data-tone="${toneOf(l)}">${esc(l.verdict.label)}</span>
          </div>
          <div class="card__fair">${fair}</div>
        </div>
      </button>
    </li>`;
  }

  function appendChunk() {
    if (rendered >= view.length) return;
    const slice = view.slice(rendered, rendered + CHUNK);
    $("#grid").insertAdjacentHTML("beforeend", slice.map((l, i) => cardHTML(l, i)).join(""));
    rendered += slice.length;
  }

  function setupObserver() {
    if (observer) observer.disconnect();
    observer = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) {
        appendChunk();
        if (rendered >= view.length) observer.disconnect();
      }
    }, { rootMargin: "900px 0px" });
    observer.observe($("#sentinel"));
  }

  /* ---------------- detail ---------------- */
  const sheet = $("#sheet");
  let lastFocus = null;

  function routeHash() {
    const id = L.hash.get();
    if (id && data.byId.has(id)) openDetail(id, { fromHash: true });
    else if (!id && sheet.open) closeDetail({ fromHash: true });
  }

  function openDetail(id, { fromHash = false } = {}) {
    const l = data.byId.get(id);
    if (!l) return;
    if (!sheet.open) lastFocus = document.activeElement;
    $("#sheetBody").innerHTML = detailHTML(l);
    if (!sheet.open) { sheet.showModal(); document.documentElement.style.overflow = "hidden"; }
    $("#sheetBody").scrollTop = 0;
    if (!fromHash || L.hash.get() !== id) L.hash.set(id);
    wireDetail(l);
    $(".d-close", sheet).focus({ preventScroll: true });
  }

  function closeDetail({ fromHash = false } = {}) {
    if (!sheet.open) return;
    const done = () => {
      sheet.classList.remove("is-closing");
      sheet.close();
      document.documentElement.style.overflow = "";
      if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
    };
    if (!fromHash) L.hash.set(null);
    if (L.reducedMotion()) done();
    else { sheet.classList.add("is-closing"); setTimeout(done, 260); }
  }

  sheet.addEventListener("cancel", e => { e.preventDefault(); closeDetail(); });
  sheet.addEventListener("click", e => { if (e.target === sheet) closeDetail(); });

  function factRows(l) {
    const rows = [
      ["Joueur", l.player], ["Époque", L.ERAS[l.player_era]], ["Gamme", l.product], ["Saison", l.season],
      ["Parallèle", l.parallel], ["Spécial", l.special], ["Tirage", l.runLabel ? `${l.runLabel}${l.band ? ` (${l.band})` : ""}` : null],
      ["Gradée", l.graded], ["État", l.condition], ["Statut", L.STATUS[l.status] || l.status],
      ["Vue la 1re fois", fmt.date(l.first_seen)], ["Vendue le", l.sold_at ? fmt.date(l.sold_at) : null],
      ["Favoris", l.favourites], ["Prix total (port)", l.price_total && l.price_total !== l.price ? fmt.eur(l.price_total) : null],
      ["Source", SRC_LABEL(l.source)],
    ];
    return rows.filter(([, v]) => v != null && v !== "" && v !== false)
      .map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
  }

  function gaugeHTML(l) {
    const s = dealScore(l);
    const len = Math.PI * 80;
    const v = s == null ? 0 : s;
    const color = `var(--${s == null ? "none" : toneOf(l)})`;
    return `<div class="gauge" role="img" aria-label="${s == null ? "Score affaire indisponible" : `Score affaire ${s} sur 100`}">
      <svg viewBox="0 0 200 112" aria-hidden="true">
        <defs><linearGradient id="gTrack" x1="0" x2="1"><stop offset="0" stop-color="#e0102a" stop-opacity=".18"/><stop offset=".5" stop-color="#f2b400" stop-opacity=".22"/><stop offset="1" stop-color="#12a150" stop-opacity=".22"/></linearGradient></defs>
        <path d="M20 100a80 80 0 0 1 160 0" fill="none" stroke="url(#gTrack)" stroke-width="16" stroke-linecap="round"/>
        <path class="gauge__arc" d="M20 100a80 80 0 0 1 160 0" fill="none" stroke="${color}" stroke-width="16" stroke-linecap="round"
          stroke-dasharray="${len.toFixed(1)}" stroke-dashoffset="${len.toFixed(1)}" data-target="${(len * (1 - v / 100)).toFixed(1)}"/>
      </svg>
      <div class="gauge__val"><b>${s == null ? "N/A" : s}</b><span>Score affaire</span></div>
    </div>`;
  }

  function rangeHTML(l) {
    const a = l.analysis;
    if (a.fair == null || a.low == null || a.high == null) return "";
    const lo = Math.min(a.low, l.price, a.fair), hi = Math.max(a.high, l.price, a.fair);
    const pad = (hi - lo) * 0.08 || 1;
    const min = Math.max(0, lo - pad), max = hi + pad;
    const x = v => ((v - min) / (max - min)) * 100;
    const pl = x(l.price), fl = x(a.fair);
    const cls = p => p < 12 ? "l" : p > 88 ? "r" : "";
    const lx = x(a.low), hx = x(a.high);
    const lowHigh = hx - lx < 26
      ? `<span class="${cls((lx + hx) / 2)}" style="left:${(lx + hx) / 2}%">Fourchette<b>${fmt.eur(a.low)} – ${fmt.eur(a.high)}</b></span>`
      : `<span class="${cls(lx)}" style="left:${lx}%">Bas<b>${fmt.eur(a.low)}</b></span><span class="${cls(hx)}" style="left:${hx}%">Haut<b>${fmt.eur(a.high)}</b></span>`;
    return `<div class="rangebar" aria-label="Fourchette de cote de ${fmt.eur(a.low)} à ${fmt.eur(a.high)}, cote ${fmt.eur(a.fair)}, prix ${fmt.eur(l.price)}" role="img">
      <div class="rangebar__top" aria-hidden="true">${Math.abs(pl - fl) < 9 ? `<span class="is-price ${cls(pl)}" style="left:${pl}%">Prix ≈ cote</span>` : `<span class="${cls(fl)}" style="left:${fl}%">Cote</span><span class="is-price ${cls(pl)}" style="left:${pl}%">Prix</span>`}</div>
      <div class="rangebar__track">
        <div class="rangebar__band" style="left:${lx}%;width:${hx - lx}%"></div>
        <div class="rangebar__mark rangebar__mark--fair" style="left:${fl}%"></div>
        <div class="rangebar__mark rangebar__mark--price" style="left:${pl}%"></div>
      </div>
      <div class="rangebar__labels" aria-hidden="true">${lowHigh}</div>
    </div>`;
  }

  function reportHTML(l) {
    const a = l.analysis;
    const gapTone = a.fair == null ? "none" : toneOf(l);
    const diff = a.fair != null ? l.price - a.fair : null;
    const note = a.fair == null
      ? `Pas assez d'annonces comparables pour coter cette carte (${a.n_comps} trouvée${a.n_comps > 1 ? "s" : ""}).`
      : `Cote pondérée calculée sur ${a.n_comps} comparable${a.n_comps > 1 ? "s" : ""} dont ${a.n_sold} vendu${a.n_sold > 1 ? "s" : ""}${a.tiers && a.tiers.length ? ` · niveaux : ${a.tiers.map(t => tierInfo[t]?.label || t).join(" / ")}` : ""}.`;
    return `<section class="report" aria-labelledby="repTitle">
      <div class="report__head"><h3 id="repTitle">Rapport de match</h3><span class="conf">Confiance : <b>${esc(a.confidence || "aucune")}</b></span></div>
      <div class="report__body">
        ${gaugeHTML(l)}
        <div>
          <dl class="vs">
            <div class="vs__side"><dt>Prix demandé</dt><dd>${fmt.eur(l.price)}</dd></div>
            <span class="vs__mid" aria-hidden="true">VS</span>
            <div class="vs__side vs__side--fair"><dt>Cote</dt><dd>${a.fair == null ? "—" : fmt.eur(a.fair)}</dd></div>
          </dl>
          <div class="gap-line">
            <span class="gap" data-tone="${gapTone}">${a.ratio == null ? "—" : fmt.pct(a.ratio)}</span>
            <span class="verdict" data-tone="${toneOf(l)}">${esc(l.verdict.label)}</span>
            ${diff != null ? `<span class="muted" style="font-size:13px;font-weight:600">${diff > 0 ? `${fmt.eur(Math.round(diff * 100) / 100)} au-dessus` : diff < 0 ? `${fmt.eur(Math.round(-diff * 100) / 100)} d'économie` : "pile la cote"}</span>` : ""}
          </div>
          ${rangeHTML(l)}
        </div>
      </div>
      <p class="report__note">${esc(note)}</p>
    </section>`;
  }

  /* Price chart: time-based when the comparables span several days, otherwise a price dot-plot. */
  function chartHTML(l) {
    const s = L.series(l, data);
    const pts = s.points;
    if (!pts.length) return `<p class="muted">Aucun comparable daté pour tracer un historique.</p>`;
    const W = Math.round(clamp(window.innerWidth - 72, 300, 640)), H = W < 500 ? 230 : 250, m = { t: 18, r: 14, b: 34, l: W < 500 ? 44 : 52 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const prices = pts.map(p => p.price).concat([s.self.price, s.fair, s.low, s.high].filter(v => v != null));
    const ts = pts.map(p => p.t).concat([s.self.t]).filter(Number.isFinite);
    const span = Math.max(...ts) - Math.min(...ts);
    const timeMode = span > 36 * 3600e3;
    const pMin = Math.min(...prices), pMax = Math.max(...prices);
    const useLog = pMax / Math.max(pMin, 0.01) > 12;
    const f = useLog ? Math.log10 : (v => v);
    const lo = useLog ? f(Math.max(pMin * 0.8, 0.1)) : Math.max(0, pMin - (pMax - pMin) * 0.12);
    const hi = useLog ? f(pMax * 1.25) : pMax + (pMax - pMin) * 0.12 + 0.5;
    const ticks = niceTicks(useLog ? Math.pow(10, lo) : lo, useLog ? Math.pow(10, hi) : hi, useLog);
    const dotColor = p => p.status === "sold" ? "var(--ink)" : "#fff";
    let svg = "";

    if (timeMode) {
      const t0 = Math.min(...ts), t1 = Math.max(...ts);
      const X = t => m.l + ((t - t0) / (t1 - t0 || 1)) * iw;
      const Y = v => m.t + ih - ((f(v) - lo) / (hi - lo)) * ih;
      svg += ticks.map(v => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#e6e4dc"/><text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${esc(fmt.eur(v))}</text>`).join("");
      if (s.low != null && s.high != null) svg += `<rect x="${m.l}" width="${iw}" y="${Y(s.high)}" height="${Math.max(2, Y(s.low) - Y(s.high))}" fill="#12a150" fill-opacity=".09"/>`;
      if (s.fair != null) svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(s.fair)}" y2="${Y(s.fair)}" stroke="#111114" stroke-width="2" stroke-dasharray="6 5"/><text x="${W - m.r}" y="${Y(s.fair) - 6}" text-anchor="end" style="fill:#111114;font-weight:800">Cote ${esc(fmt.eur(s.fair))}</text>`;
      const dateTicks = 4;
      for (let i = 0; i <= dateTicks; i++) { const t = t0 + (t1 - t0) * i / dateTicks; svg += `<text x="${X(t)}" y="${H - 10}" text-anchor="middle">${esc(new Date(t).toLocaleDateString("fr-FR", { day: "numeric", month: "short" }))}</text>`; }
      svg += pts.map((p, i) => dot(X(p.t), Y(p.price), p, i)).join("");
      svg += selfMark(X(s.self.t), Y(s.self.price), l.price);
    } else {
      // Dot plot: x = price, dots stacked (beeswarm) to avoid overlap.
      const X = v => m.l + ((f(v) - lo) / (hi - lo)) * iw;
      const baseY = m.t + ih - 10;
      svg += ticks.map(v => `<line x1="${X(v)}" x2="${X(v)}" y1="${m.t}" y2="${m.t + ih}" stroke="#e6e4dc"/><text x="${X(v)}" y="${H - 10}" text-anchor="middle">${esc(fmt.eur(v))}</text>`).join("");
      if (s.low != null && s.high != null) svg += `<rect x="${X(s.low)}" width="${Math.max(2, X(s.high) - X(s.low))}" y="${m.t}" height="${ih}" fill="#12a150" fill-opacity=".09" rx="6"/>`;
      if (s.fair != null) svg += `<line x1="${X(s.fair)}" x2="${X(s.fair)}" y1="${m.t}" y2="${m.t + ih}" stroke="#111114" stroke-width="2" stroke-dasharray="6 5"/><text x="${clamp(X(s.fair), m.l + 40, W - m.r - 40)}" y="${m.t - 4}" text-anchor="middle" style="fill:#111114;font-weight:800">Cote ${esc(fmt.eur(s.fair))}</text>`;
      const placed = [];
      const sorted = pts.map((p, i) => ({ p, i })).sort((a, b) => b.p.weight - a.p.weight);
      const r = 7, stepY = 15;
      sorted.forEach(({ p, i }) => {
        const x = X(p.price); let y = baseY;
        while (placed.some(q => Math.abs(q.x - x) < r * 2 && Math.abs(q.y - y) < r * 2) && y > m.t + 14) y -= stepY;
        placed.push({ x, y });
        svg += dot(x, y, p, i);
      });
      svg += selfMark(X(l.price), s.fair != null && Math.abs(X(s.fair) - X(l.price)) < 130 ? m.t + 52 : m.t + 26, l.price);
    }
    const nSold = pts.filter(p => p.status === "sold").length;
    return `<div class="chart">
      <div class="chart-wrap">
        <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${timeMode ? "Prix des comparables dans le temps" : "Répartition des prix des comparables"} : ${pts.length} points, cette annonce à ${esc(fmt.eur(l.price))}${s.fair != null ? `, cote ${esc(fmt.eur(s.fair))}` : ""}">${svg}</svg>
      </div>
      <div class="chart-legend">
        <span><i style="background:var(--red)"></i>Cette annonce</span>
        <span><i style="background:var(--ink)"></i>Vendu (${nSold})</span>
        <span><i style="background:#fff;border:2.5px solid var(--ink)"></i>En vente (${pts.length - nSold})</span>
        ${s.fair != null ? `<span><i style="border-radius:2px;height:3px;width:16px;background:var(--ink)"></i>Cote</span><span><i style="border-radius:3px;background:rgba(18,161,80,.2)"></i>Fourchette</span>` : ""}
      </div>
      ${timeMode ? "" : `<p class="chart-note">Tous les comparables ont été relevés le même jour : l'historique dans le temps apparaîtra au fil des passages. En attendant, voici où se situe le prix face au marché${useLog ? " (échelle logarithmique)" : ""}. Taille du point = poids du comparable.</p>`}
    </div>`;

    function dot(x, y, p, i) {
      const r = 4 + 4 * (p.weight || 0.3);
      const label = `${fmt.eur(p.price)} · ${p.status === "sold" ? "vendu" : "en vente"}${p.listing ? ` · ${p.listing.title}` : ""}`;
      return `<circle class="c-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}" fill="${dotColor(p)}" stroke="#111114" stroke-width="2.5" tabindex="0" data-tip="${esc(label)}" data-i="${i}"><title>${esc(label)}</title></circle>`;
    }
    function selfMark(x, y, price) {
      return `<g aria-hidden="true"><line x1="${x}" x2="${x}" y1="${y}" y2="${m.t + ih}" stroke="var(--red)" stroke-width="2"/>
        <rect x="${x - 9}" y="${y - 9}" width="18" height="18" rx="3" fill="var(--red)" stroke="#fff" stroke-width="3" transform="rotate(45 ${x} ${y})"/>
        <text x="${clamp(x, m.l + 50, W - m.r - 50)}" y="${y - 16}" text-anchor="middle" style="fill:var(--red-ink);font-weight:900;font-size:12px">Cette carte ${esc(fmt.eur(price))}</text></g>`;
    }
  }

  function niceTicks(a, b, log) {
    if (log) {
      const out = [];
      for (let e = Math.floor(Math.log10(Math.max(a, 0.01))); e <= Math.ceil(Math.log10(b)); e++) [1, 2, 5].forEach(k => { const v = k * Math.pow(10, e); if (v >= a && v <= b) out.push(v); });
      return out.length > 7 ? out.filter((_, i) => i % 2 === 0) : out;
    }
    const raw = (b - a) / 5, mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    const step = [1, 2, 2.5, 5, 10].map(k => k * mag).find(s => s >= raw) || mag * 10;
    const out = [];
    for (let v = Math.ceil(a / step) * step; v <= b + 1e-9; v += step) out.push(Math.round(v * 100) / 100);
    return out;
  }

  function compsHTML(l) {
    const groups = L.compGroups(l, data);
    if (!groups.length) return `<p class="muted">Aucune annonce comparable trouvée pour l'instant.</p>`;
    return groups.map(g => `<div class="tier">
      <div class="tier__head"><span class="tier__key">${esc(g.tier)}</span><span>${esc(g.label)}</span><span class="tier__w">poids ${esc(String(g.items[0].weight))} · ${g.items.length}</span></div>
      <ul class="comps">${g.items.map(c => {
        const cl = c.listing;
        const inner = `<span class="comp__img">${cl && cl.image ? imgTag(cl.image, "") : placeholder("—")}</span>
          <span class="comp__txt"><span class="comp__title">${esc(cl ? cl.title : c.id)}</span>
            <span class="comp__meta">${cl ? srcTag(cl.source) : ""}<span class="st ${c.status === "sold" ? "st--sold" : ""}">${c.status === "sold" ? "Vendu" : "En vente"}</span>${c.date ? `<span>${esc(fmt.date(c.date))}</span>` : ""}</span></span>
          <span class="comp__price"><b>${fmt.eur(c.price)}</b>${c.adj_price != null && c.adj_price !== c.price ? `<small>ajusté ${fmt.eur(c.adj_price)}</small>` : ""}</span>`;
        if (listingIds.has(c.id)) return `<li><button type="button" class="comp" data-open="${esc(c.id)}" aria-label="Voir l'analyse : ${esc(cl.title)}">${inner}</button></li>`;
        if (cl && cl.url) return `<li><a class="comp" href="${esc(cl.url)}" target="_blank" rel="noopener noreferrer">${inner}<span class="sr-only">(ouvre l'annonce dans un nouvel onglet)</span></a></li>`;
        return `<li><div class="comp">${inner}</div></li>`;
      }).join("")}</ul></div>`).join("");
  }

  function detailHTML(l) {
    const imgs = l.images.length ? l.images : (l.image ? [l.image] : []);
    return `<div class="d-top">
        <div class="d-top__crumb">${srcTag(l.source)}<span>· ${esc(L.STATUS[l.status] || "")}</span></div>
        <button class="d-close" type="button" aria-label="Fermer la fiche">${ICON.x}</button>
      </div>
      <div class="d-grid">
        <div class="d-media">
          <div class="d-stage" id="dStage">${imgs[0] ? imgTag(imgs[0], l.title) : placeholder()}${runBadge(l)}</div>
          ${imgs.length > 1 ? `<div class="d-thumbs" role="group" aria-label="Photos">${imgs.map((src, i) =>
            `<button type="button" class="d-thumb" data-img="${i}" aria-label="Photo ${i + 1} sur ${imgs.length}" aria-current="${i === 0}">${imgTag(src, "")}</button>`).join("")}</div>` : ""}
        </div>
        <div class="d-main">
          <div class="d-head">
            <h2 id="sheetTitle">${esc(l.displayName)}</h2>
            <p>${esc(l.subtitle || "")}</p>
            <p class="d-title">« ${esc(l.title)} »</p>
            <div class="d-flags">${l.runLabel ? `<span class="flag">${esc(l.runLabel)}</span>` : ""}${flagsFor(l)}</div>
          </div>
          ${reportHTML(l)}
          <div class="d-actions">
            <a class="btn btn--primary" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">Voir l'annonce sur ${esc(SRC_LABEL(l.source))} ${ICON.ext}<span class="sr-only">(nouvel onglet)</span></a>
          </div>
          <section class="block"><h3>Prix du marché <small>${l.analysis.comps.length} comparable${l.analysis.comps.length > 1 ? "s" : ""}</small></h3>${chartHTML(l)}</section>
          <section class="block"><h3>Fiche technique</h3><dl class="facts">${factRows(l)}</dl></section>
          <section class="block"><h3>Comparables <small>par niveau de proximité</small></h3>${compsHTML(l)}</section>
          ${l.description ? `<section class="block"><h3>Description du vendeur</h3><div class="desc">${esc(l.description)}</div></section>` : ""}
        </div>
      </div>`;
  }

  function wireDetail(l) {
    $(".d-close", sheet).addEventListener("click", () => closeDetail());
    const imgs = l.images.length ? l.images : [l.image];
    $$(".d-thumb", sheet).forEach(b => b.addEventListener("click", () => {
      const i = +b.dataset.img;
      $$(".d-thumb", sheet).forEach(x => x.setAttribute("aria-current", String(x === b)));
      const stage = $("#dStage");
      const old = stage.querySelector("img, .ph");
      const tmp = document.createElement("div");
      tmp.innerHTML = imgTag(imgs[i], l.title);
      old ? old.replaceWith(tmp.firstChild) : stage.prepend(tmp.firstChild);
    }));
    // Gauge sweep
    const arc = $(".gauge__arc", sheet);
    if (arc) {
      if (L.reducedMotion()) arc.style.strokeDashoffset = arc.dataset.target;
      else requestAnimationFrame(() => requestAnimationFrame(() => { arc.style.strokeDashoffset = arc.dataset.target; }));
    }
    // Chart tooltip
    const wrap = $(".chart-wrap", sheet);
    if (wrap) {
      const tip = document.createElement("div"); tip.className = "chart-tip"; tip.hidden = true; wrap.appendChild(tip);
      const show = e => {
        const c = e.target.closest(".c-dot"); if (!c) return;
        const wr = wrap.getBoundingClientRect(), cr = c.getBoundingClientRect();
        tip.textContent = c.dataset.tip.length > 70 ? c.dataset.tip.slice(0, 68) + "…" : c.dataset.tip;
        tip.hidden = false;
        tip.style.left = clamp(cr.left + cr.width / 2 - wr.left, 90, wr.width - 90) + "px";
        tip.style.top = (cr.top - wr.top) + "px";
      };
      const hide = () => { tip.hidden = true; };
      wrap.addEventListener("mouseover", show); wrap.addEventListener("focusin", show);
      wrap.addEventListener("mouseout", hide); wrap.addEventListener("focusout", hide);
    }
  }
})();
