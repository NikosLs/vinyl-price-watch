"""Find the Discogs release for a shop item: catalogue number, then barcode, then artist/title."""
import re

from .common import norm

COLOURS = ("red white blue yellow clear orange pink green purple splatter gold silver grey gray marble transparent "
           "translucent picture brown violet turquoise coloured colored smoke swirl neon magenta").split()
EUROPE = {"Europe", "Germany", "UK", "Netherlands", "Italy", "France", "UK & Europe", "Spain", "Belgium", "Austria",
          "Sweden", "Scandinavia", "Benelux", "Switzerland"}
VINYL_SIZES = ('7"', '10"', '12"')


def _cn(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tokens(s):
    return {t for t in norm(s).split() if len(t) > 2}


def _labels(it):
    out = [norm(re.sub(r"\brecord(s|ings)?\b", "", l, flags=re.I)) for l in it["labels"]]
    return [l for l in out if len(l) > 1]


def _label_ok(hit, labs):
    hl = [norm(l) for l in hit.get("label", []) if norm(l)]
    return bool(labs) and any(a in b or b in a for a in labs for b in hl)


def _format_ok(hit, fmt):
    f = hit.get("format", [])
    return ((fmt == "LP" and "Vinyl" in f) or (fmt in VINYL_SIZES and fmt in f) or (fmt == "CD" and "CD" in f)
            or (fmt == "Cassette" and "Cassette" in f) or fmt == "Boxset")


def _colours(it):
    cols = [c for c in COLOURS if c in norm(it["variant"])]
    if not cols:
        near = re.findall(r"(\w+(?: \w+)?) (?:colou?red )?vinyl", it["desc"].lower())
        cols = [c for c in COLOURS if any(c in x for x in near)]
    return cols


def _score(hit, it, labs, cols, title, used):
    s, f, fmt = 0, hit.get("format", []), it["format"]
    fj = " ".join(f)
    if fmt == "LP" and ("LP" in f or "Album" in f): s += 3
    if fmt in VINYL_SIZES and fmt in f: s += 4
    if fmt in ("CD", "Cassette") and fmt in f: s += 3
    if fmt == "Boxset" and "Box Set" in f: s += 3
    if _label_ok(hit, labs): s += 5
    try:
        y = int(hit.get("year") or 0)
    except Exception:
        y = 0
    if it["year"]:
        s += 3 if str(y) == it["year"] else 0
    elif not used:  # new stock is almost always the current pressing
        s += 3 if y >= 2022 else 2 if y >= 2015 else 1 if y >= 2005 else 0
    if used:
        c = hit.get("country", "")
        s += 2 if c == "Greece" else 1 if c in EUROPE else 0
    txt = norm(" ".join((x.get("text") or "") for x in hit.get("formats", [])))
    hc = {c for c in COLOURS if c in txt}
    if cols:
        s += 2 * len(hc & set(cols)) - len(hc - set(cols)) - (3 if not hc else 0)
    elif hc and not used:
        s -= 3
    if norm(hit.get("title", "").split(" - ", 1)[-1]) == norm(title): s += 3
    if any(x in fj for x in ("Test Pressing", "Promo", "Acetate", "White Label")): s -= 3
    if any(x in fj for x in ("Club Edition", "Numbered")) and not re.search(r"number|club", it["variant"] + " " + it["desc"], re.I):
        s -= 2
    return s


def match_item(dg, it, catno=""):
    """Return a match dict (empty if nothing plausible was found)."""
    used = it["used"]
    artist = "Various" if it["artist"].upper().startswith("VARIOUS") or it["artist"].upper() == "V/A" else it["artist"].split(", ")[0]
    title = re.sub(r"\s*\([^)]*\)\s*$", "", it["title"]).strip()
    if re.fullmatch(r"(self[ -]?titled|s/t|same)", title, re.I):
        title = artist
    dfmt = {"CD": "CD", "Cassette": "Cassette", "Boxset": ""}.get(it["format"], "Vinyl")
    extra = {"format": dfmt} if dfmt else {}
    labs, cols = _labels(it), _colours(it)
    want = _tokens(artist) | _tokens(title)
    tries = []
    if len(_cn(catno)) >= 3:
        tries.append(("catno", {"catno": catno}))
    bc = it["barcode"]
    if len(bc) >= 10:
        tries.append(("barcode", {"barcode": bc}))
        if bc.startswith("0"):
            tries.append(("barcode", {"barcode": bc.lstrip("0")}))
    tries += [("artist+title", dict(artist=artist, release_title=title, **extra)), ("freetext", dict(q=artist + " " + title, **extra))]
    for how, q in tries:
        hits = (dg.get("database/search", type="release", per_page=50, **q) or {}).get("results") or []
        if how == "catno":
            hits = [h for h in hits if _format_ok(h, it["format"]) and (_cn(h.get("catno")) == _cn(catno) or _cn(catno) in _cn(h.get("catno")))
                    and len(want & _tokens(h.get("title", ""))) / max(1, len(want)) >= 0.34]
        elif how == "barcode":
            hits = [h for h in hits if _format_ok(h, it["format"])]
        elif how == "freetext":
            na = norm(re.sub(r"^the ", "", artist, flags=re.I))
            hits = [h for h in hits if na and na in norm(h.get("title", ""))]
        if not hits:
            continue
        ranked = sorted(((_score(h, it, labs, cols, title, used), -k, h) for k, h in enumerate(hits)), key=lambda x: x[:2], reverse=True)
        s, _, b = ranked[0]
        return {
            "match": how, "score": s, "n_hits": len(hits), "tie": len(ranked) > 1 and ranked[1][0] == s,
            "release_id": b["id"], "d_title": b.get("title"), "d_year": b.get("year"), "d_country": b.get("country"),
            "d_label": (b.get("label") or [""])[0], "d_catno": b.get("catno"), "d_format": ", ".join(b.get("format", [])),
            "d_text": " ".join((x.get("text") or "") for x in b.get("formats", [])).strip(),
            "label_ok": _label_ok(b, labs), "title_ok": norm(b.get("title", "").split(" - ", 1)[-1]) == norm(title),
            "year_ok": bool(it["year"]) and str(b.get("year", "")) == it["year"],
            "have": b.get("community", {}).get("have"), "want": b.get("community", {}).get("want"),
        }
    return {}
