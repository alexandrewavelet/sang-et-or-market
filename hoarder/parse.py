"""Extract structured card attributes from free-text listing titles/descriptions."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict


def norm(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace; keeps '/', '#' and digits."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("ł", "l").replace("ø", "o")
    text = re.sub(r"[‐-―–—_]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


# Order matters: the first match wins, so put specific products before generic ones.
PRODUCTS = [
    ("Panini Select", r"\bselect\b"),
    ("Panini Prizm", r"\bprizm\b"),
    ("Panini Donruss", r"\bdonruss\b"),
    ("Panini Mosaic", r"\bmosaic\b"),
    ("Panini Obsidian", r"\bobsidian\b"),
    ("Panini Immaculate", r"\bimmaculate\b"),
    ("Panini Chronicles", r"\bchronicles\b"),
    ("Panini Revolution", r"\brevolution\b"),
    ("Panini Score", r"\bscore\b"),
    ("Panini Instant", r"\bpanini instant\b"),
    ("Topps Chrome", r"\btopps\b.*\bchrome\b|\bchrome\b.*\btopps\b"),
    ("Topps Finest", r"\bfinest\b"),
    ("Topps Merlin", r"\bmerlin\b"),
    ("Topps Match Attax", r"\bmatch attax\b"),
    ("Topps", r"\btopps\b"),
    ("Adrenalyn XL", r"\badrenalyn\b|\badrenalyne\b|\badrenaline xl\b"),
    ("Panini Foot (stickers)", r"\bunfp\b|\bfoot ?\d{2,4}\b|\bsuperfoot\b|\bvignette\b|\bsticker\b|\bautocollant\b"),
    ("Panini", r"\bpanini\b"),
]

PARALLELS = [
    "gold vinyl", "black gold", "tie-dye", "tie dye", "mezzanine", "zebra", "tiger", "camo", "cracked ice",
    "disco", "shimmer", "mojo", "wave", "neon", "pandora", "scope", "die-cut", "die cut", "holo",
    "superfractor", "refractor", "laser", "prizm silver", "sapphire", "x-fractor", "gold", "red", "blue", "green", "orange", "purple",
    "pink", "black", "silver", "white", "bronze", "teal", "aqua", "yellow", "rouge", "bleu", "vert", "violet",
    "noir", "argent", "or",
]

SPECIALS = {
    "crack": r"\bcrack\b", "momentum": r"\bmomentum\b", "top master": r"\btop master\b",
    "invincible": r"\binvincible\b", "edition limitee": r"\bedition limitee\b|\blimited edition\b",
    "fans favourite": r"\bfans? favou?rite\b", "power duo": r"\bpower duo\b", "titan": r"\btitans?\b",
    "next up": r"\bnext ?up\b", "equalizers": r"\bequali[sz]ers?\b", "artistic impressions": r"\bartistic impressions?\b",
    "insert": r"\binsert\b", "rated rookie": r"\brated rookies?\b", "topps now": r"\btopps now\b",
    "kaboom": r"\bkaboom\b", "downtown": r"\bdowntown\b", "color blast": r"\bcolor blast\b",
}

LOT_RE = re.compile(
    r"#\s?\d+\s?(?:a|to|-)\s?#?\d+\b|\bau choix\b|\ba choisir\b|\bto choose\b|\bchoose your\b|\bpick your\b|\bpick a card\b|\bvariation\b|\blots?\b|\b\d{1,3} ?(cartes|cards|vignettes|stickers|images)\b|\bset complet\b|\bbundle\b|\bclasseur\b|\bcollection compl"
)
STICKER_RE = re.compile(r"\bstickers?\b|\bvignettes?\b|\bautocollants?\b|\bimages? panini\b|\bpanini foot \d{2,4}\b|\bsuperfoot\b")
AUTO_RE = re.compile(r"\bauto(graph)?e?s?\b|\bsigned\b|\bsignee?s?\b|\bsigne\b|\bdedicace\b|\bdedicacee\b|\bsignature\b|\bsignez\b")
PATCH_RE = re.compile(r"\bpatch\b|\brelic\b|\bmemorabilia\b|\bjersey\b|\bmaillot porte\b|\bswatch(?:es)?\b|\bpatchs?\b")
ROOKIE_RE = re.compile(r"\brc\b(?! lens)|\brookie\b")
GRADED_RE = re.compile(r"\b(psa|bgs|cgc|sgc|pca)\s?\d{1,2}(\.5)?\b")

NUM_WORDS_STRICT_RE = re.compile(
    r"\b(?:numerote?e?s?|limitee?s?|tirage|numbered|serial(?:ised|ized)?)\s*(?:sur|a|de|to|/|:|à)?\s*/?\s*(\d{1,4})\b"
)
SLASH_RE = re.compile(r"(?<![\w/])(\d{1,4})?\s?/\s?(\d{1,4})(?![\w/])")
ONE_OF_ONE_RE = re.compile(r"\b1\s?/\s?1\b|\bone of one\b|\b1 of 1\b|\bunique 1/1\b")

SEASON_RE = re.compile(r"\b(19[6-9]\d|20[0-3]\d)\s?[-/]\s?((?:19|20)?\d{2})\b")
SHORT_SEASON_RE = re.compile(r"(?<![\d/])(\d{2})\s?[-/]\s?(\d{2})(?![\d/])")
YEAR_RE = re.compile(r"\b(19[6-9]\d|20[0-3]\d)\b")


def _is_season(a: int, b: int) -> bool:
    if 1960 <= a <= 2039:
        return b in (a + 1, (a + 1) % 100)
    return 0 <= a <= 99 and b == (a + 1) % 100 and (a >= 60 or a <= 39)


def extract_numbering(text: str) -> tuple[int | None, int | None]:
    """Return (print_run, serial) e.g. '12/25' -> (25, 12), '/4' -> (4, None)."""
    t = norm(text)
    if ONE_OF_ONE_RE.search(t):
        return 1, 1
    candidates: list[tuple[int, int | None]] = []
    for m in SLASH_RE.finditer(t):
        a, b = m.group(1), int(m.group(2))
        if a is not None and _is_season(int(a), b):
            continue
        if b < 1 or b > 2500:
            continue
        before = t[max(0, m.start() - 4):m.start()]
        if a is None and re.search(r"\d$", before.strip()):
            continue
        if a is not None:
            ai = int(a)
            if ai == 0 or ai > b:
                continue
            candidates.append((b, ai))
        else:
            candidates.append((b, None))
    if candidates:
        # Prefer the explicit serial "x/y", then the smallest print run (most specific).
        with_serial = [c for c in candidates if c[1] is not None]
        return min(with_serial or candidates, key=lambda c: c[0])
    m = NUM_WORDS_STRICT_RE.search(t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 2500 and not (1960 <= n <= 2039):
            return n, None
    return None, None


NUMBERED_WORD_RE = re.compile(r"\bnumerote?e?s?\b|\bnum\b|\bnumbered\b|\bserial(?:ised|ized)? numbered\b|\b#?/\s?\?\b")


def numbering_band(run: int | None, numbered_unknown: bool = False) -> str | None:
    if run is None:
        return "numérotée (tirage ?)" if numbered_unknown else None
    for limit, label in [(1, "1/1"), (5, "/2-5"), (10, "/6-10"), (25, "/11-25"), (50, "/26-50"),
                         (99, "/51-99"), (199, "/100-199"), (499, "/200-499")]:
        if run <= limit:
            return label
    return "/500+"


def extract_season(text: str) -> str | None:
    t = norm(text)
    m = SEASON_RE.search(t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if _is_season(a, b):
            return f"{a}-{str(a + 1)[2:]}"
    for m in SHORT_SEASON_RE.finditer(t):
        a, b = int(m.group(1)), int(m.group(2))
        if _is_season(a, b):
            full = 2000 + a if a <= 39 else 1900 + a
            return f"{full}-{str(full + 1)[2:]}"
    m = YEAR_RE.search(t)
    if m:
        y = int(m.group(1))
        # Panini Ligue 1 products are named after the year the season ends ("Foot 2027" = 2026-27).
        return f"{y - 1}-{str(y)[2:]}"
    return None


def era_bucket(season: str | None) -> str | None:
    """Coarse period used to keep 1990s stickers away from 2020s chrome when comparing."""
    if not season:
        return None
    y = int(season[:4])
    return "≤1999" if y < 2000 else "2000-09" if y < 2010 else "2010-19" if y < 2020 else "2020+"


def extract_product(text: str) -> str | None:
    t = norm(text)
    for name, pattern in PRODUCTS:
        if re.search(pattern, t):
            return name
    return None


def extract_parallel(text: str) -> str | None:
    t = norm(text)
    for p in PARALLELS:
        if re.search(rf"\b{re.escape(p)}\b", t):
            return p.replace("tie dye", "tie-dye").replace("die cut", "die-cut")
    return None


def extract_special(text: str) -> str | None:
    t = norm(text)
    for name, pattern in SPECIALS.items():
        if re.search(pattern, t):
            return name
    return None


class PlayerMatcher:
    def __init__(self, players: list[dict]):
        self.entries: list[tuple[re.Pattern, dict, int]] = []
        for p in players:
            keys = {norm(p["name"])}
            parts = norm(p["name"]).replace("-", " ").split()
            keys.add(" ".join(parts))
            if len(parts) >= 2:
                keys.add(" ".join(parts[1:]))  # surname(s)
            for a in p.get("aliases", []):
                keys.add(norm(a))
            for k in keys:
                if len(k) < 4:
                    continue
                pat = re.compile(r"(?<![a-z])" + re.escape(k).replace(r"\ ", r"[\s-]+") + r"(?![a-z])")
                self.entries.append((pat, p, len(k)))
        # Bare surname (last token) only when no other player shares it ("Leca" yes, "Fofana" no).
        last_tokens: dict[str, list[dict]] = {}
        for p in players:
            last = norm(p["name"]).replace("-", " ").split()[-1]
            last_tokens.setdefault(last, []).append(p)
        for last, owners in last_tokens.items():
            if len(owners) == 1 and len(last) >= 4:
                pat = re.compile(r"(?<![a-z])" + re.escape(last) + r"(?![a-z])")
                self.entries.append((pat, owners[0], len(last)))
        self.entries.sort(key=lambda e: -e[2])

    def match(self, text: str) -> dict | None:
        t = norm(text)
        for pat, player, _ in self.entries:
            if pat.search(t):
                return player
        return None


NON_CARD_RE = re.compile(
    r"\bflocage\b|\bfanion\b|\bpennant\b|\bmaillot\b(?! porte)|\bjersey\b(?! card| patch| relic|/)|\becharpe\b|\bscarf\b|"
    r"\bcamera\b|\bappareil\b|\bhood\b|\bposter\b|\baffiche\b|\bprogramme\b|\bbillet\b|\bticket\b|\bmug\b|"
    r"\bgobelet\b|\bpin'?s\b|\bphoto\b(?! card)|\bfigurine\b|\bmagnet\b|\bmontre\b|\bsunglasses\b|\bskateboarding\b")
CLUB_FALSE_POSITIVES = re.compile(r"jeremain lens|behind the lens|x[\s-]?lens|\blens ?(?:flare|cap|kit)|\bobjectif\b|\blentilles?\b|\bcamera\b|\bphoto lens")


OTHER_CLUBS_RE = re.compile(r"\b(" + "|".join([
    "psg", "paris sg", "paris saint", "om", "marseille", "olympique lyonnais", "lyon", "losc", "lille", "monaco", "ogc nice",
    "rennes", "nantes", "strasbourg", "metz", "lorient", "brest", "toulouse", "montpellier", "reims", "auxerre", "angers",
    "le havre", "saint-etienne", "saint etienne", "asse", "bordeaux", "juventus", "juve", "leipzig", "bayern", "dortmund",
    "leverkusen", "real madrid", "barcelona", "barca", "atletico", "milan", "inter", "napoli", "roma", "chelsea",
    "arsenal", "liverpool", "manchester", "tottenham", "west ham", "brentford", "newcastle", "benfica", "porto",
    "sporting", "celtic", "anderlecht", "ajax", "galatasaray", "fenerbahce"]) + r")\b")


def mentions_club(text: str, keywords: list[str]) -> bool:
    t = CLUB_FALSE_POSITIVES.sub(" ", norm(text))
    return any(re.search(rf"(?<![a-z]){re.escape(norm(k))}(?![a-z])", t) for k in keywords)


@dataclass
class CardInfo:
    player: str | None = None
    player_era: str | None = None
    product: str | None = None
    season: str | None = None
    era_bucket: str | None = None
    print_run: int | None = None
    serial: int | None = None
    band: str | None = None
    parallel: str | None = None
    special: str | None = None
    is_lot: bool = False
    is_sticker: bool = False
    is_auction: bool = False
    is_auto: bool = False
    is_patch: bool = False
    is_rookie: bool = False
    graded: str | None = None
    club_context: bool = False
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def analyse_text(title: str, description: str, matcher: PlayerMatcher, club_keywords: list[str]) -> CardInfo:
    full = f"{title}\n{description or ''}"
    t_title = norm(title)
    t_full = norm(full)
    info = CardInfo()
    player = matcher.match(title) or matcher.match(description or "")
    if player:
        info.player, info.player_era = player["name"], player.get("era")
    info.product = extract_product(title) or extract_product(description or "")
    info.season = extract_season(title) or extract_season(description or "")
    run, serial = extract_numbering(title)
    if run is None:
        run, serial = extract_numbering(description or "")
    info.print_run, info.serial = run, serial
    info.band = numbering_band(run, bool(NUMBERED_WORD_RE.search(t_full)))
    info.era_bucket = era_bucket(info.season)
    info.parallel = extract_parallel(title)
    info.special = extract_special(full)
    info.is_lot = bool(LOT_RE.search(t_title))
    info.is_auction = "[enchère en cours]" in (description or "")
    info.is_sticker = info.product == "Panini Foot (stickers)" or bool(STICKER_RE.search(t_title))
    info.is_auto = bool(AUTO_RE.search(t_title)) or bool(re.search(r"\bautographe\b|\bdedicacee\b", t_full))
    info.is_patch = bool(PATCH_RE.search(t_full))
    info.is_rookie = bool(ROOKIE_RE.search(t_title.replace("rc lens", "")))
    g = GRADED_RE.search(t_full)
    info.graded = g.group(0).upper() if g else None
    # In scope when the title says Lens, or the description does for a known Lens player whose title names no
    # other club (descriptions are full of keyword spam: "#psg #om #lens ...").
    in_title = mentions_club(title, club_keywords)
    other_club = bool(OTHER_CLUBS_RE.search(t_title))
    info.club_context = (in_title or (bool(player) and not other_club and mentions_club(description or "", club_keywords))) \
        and not NON_CARD_RE.search(t_title)
    for flag, label in [(info.is_auto, "Auto"), (info.is_patch, "Patch"), (info.is_rookie, "RC"),
                        (info.graded, info.graded), (info.print_run == 1, "1/1")]:
        if flag:
            info.tags.append(label)
    return info
