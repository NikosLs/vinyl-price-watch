"""Download a WooCommerce shop's catalogue and turn each product into a normalised item."""
import html, json, re, time

from .common import http_get, strip_html

Q = "[″”\"′’']"  # the various inch / quote marks shops use
MERCH = ("t-shirt", "hoodie", "pins", "patches", "magnets", "slipmats", "tote", "books", "accessories", "clothing")


def fetch_listing(shop):
    """All products of a WooCommerce shop read from its category pages (for shops whose Store API is closed).

    Returns them in the Store API's shape, so parse() handles both kinds of shop."""
    out, page, pages = [], 1, 1
    base = shop["listing"].rstrip("/") + "/"
    while page <= pages:
        body, _ = http_get(base if page == 1 else f"{base}page/{page}/")
        if body is None:
            return None
        h = body.decode("utf-8", "ignore")
        pages = max([pages] + [int(n) for n in re.findall(r"/page/(\d+)/", h)])
        blocks = re.split(r'(?=<div class="[^"]*\btype-product\b)', h)[1:]
        if not blocks:
            return None  # the page layout changed: keep last week's copy rather than an empty shop
        for b in blocks:
            cls = re.match(r'<div class="([^"]*)"', b).group(1).split()
            t = re.search(r'wd-entities-title"[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
            pid = next((c[5:] for c in cls if re.fullmatch(r"post-\d+", c)), "")
            amount = lambda frag: [int(round(float(v.replace(",", "")) * 100)) for v in re.findall(r"<bdi>\s*([\d.,]+)", frag or "")]
            price_html = (re.search(r'<span class="price">(.*?)</span>\s*(?:<div|</div)', b, re.S) or [None, b])[1]
            was = amount((re.search(r"<del.*?</del>", price_html, re.S) or [""])[0])
            now = amount((re.search(r"<ins.*?</ins>", price_html, re.S) or [""])[0]) or amount(price_html)
            if not (t and pid and now):
                continue
            sku = re.search(r'data-product_sku="([^"]*)"', b)
            out.append({
                "id": int(pid), "name": strip_html(t.group(2)), "permalink": html.unescape(t.group(1)),
                "prices": {"price": now[-1] if was else now[0], "regular_price": was[0] if was else now[0]},
                "on_sale": bool(was), "is_in_stock": "outofstock" not in cls, "is_on_backorder": "onbackorder" in cls,
                "sku": html.unescape(sku.group(1)).strip() if sku else "",
                "categories": [{"name": c[12:].replace("-en", "").replace("-", " ").title()} for c in cls if c.startswith("product_cat-")],
            })
        page += 1
        time.sleep(1.5)
    seen, uniq = set(), []
    for x in out:
        if x["id"] not in seen:
            seen.add(x["id"])
            uniq.append(x)
    return uniq


def fetch_products(shop):
    """All products from the shop's public WooCommerce Store API, or None if the shop is unreachable."""
    if shop.get("listing"):
        return fetch_listing(shop)
    out, page, pages = [], 1, 1
    while page <= pages:
        url = f"{shop['base']}/wp-json/wc/store/v1/products?per_page=100&page={page}&orderby=id&order=asc{shop.get('query', '')}"
        body, headers = http_get(url)
        if body is None:
            return None
        try:
            batch = json.loads(body)
        except Exception:
            return None
        if not isinstance(batch, list):
            return None
        out += batch
        pages = int(headers.get("X-WP-TotalPages") or 1)
        page += 1
        time.sleep(1)
    seen, uniq = set(), []
    for x in out:
        if x["id"] not in seen:
            seen.add(x["id"])
            uniq.append(x)
    return uniq


def fetch_catno(url):
    """Catalogue number printed on the product page (Discobole labels it 'Barcode')."""
    body, _ = http_get(url, tries=3)
    if body is None:
        return None
    m = re.search(r'<p class="barcode"[^>]*>(.*?)</p>', body.decode("utf-8", "ignore"), re.S)
    return strip_html(m.group(1)).replace("Barcode:", "").strip() if m else ""


def detect_format(s):
    if re.search(r"box", s, re.I): return "Boxset"
    if re.search(r"\b\d?x?m?lp\b", s, re.I): return "LP"
    if re.search(r"\b7\s*" + Q, s) or "7inch" in s: return '7"'
    if re.search(r"\b10\s*" + Q, s) or "10inch" in s: return '10"'
    if re.search(r"\b12\s*" + Q, s) or "12inch" in s: return '12"'
    if re.search(r"\b\d?x?m?cd\b", s, re.I): return "CD"
    if re.search(r"tape|cas+ette", s, re.I): return "Cassette"
    return ""


def parse(shop, x):
    """Normalise one Store API product. Works from attributes when the shop has them, else from the name."""
    name = re.sub("[​-‏‪-‮﻿]", "", html.unescape(x["name"])).strip()
    parts = [p.strip() for p in re.split(r"\s+[–-]\s+", name)]
    cats = [html.unescape(c["name"]) for c in x.get("categories", [])]
    tags = [html.unescape(t["name"]) for t in x.get("tags", [])]
    at = {a["name"]: [html.unescape(t["name"]) for t in a["terms"]] for a in x.get("attributes", [])}
    cj = " | ".join(cats)
    parens = shop.get("variant") == "parens"  # "Artist – Title (2LP Red)"
    if parens:
        pm = re.search(r"\s*\(([^()]*)\)\s*$", name)
        core = re.split(r"\s+[–-]\s+", name[:pm.start()] if pm else name, maxsplit=1)
        artist, title = (core[0], core[1]) if len(core) == 2 else ("", core[0])
        variant = pm.group(1).strip() if pm else ""
        if pm and not detect_format(variant):  # "Title (LP, Red Vinyl) (RSD)": the format sits one bracket earlier
            pm2 = re.search(r"\s*\(([^()]*)\)\s*$", name[:pm.start()])
            if pm2 and detect_format(pm2.group(1)):
                variant = pm2.group(1).strip() + ", " + variant
    elif len(parts) >= 3:
        artist, title, variant = parts[0], " – ".join(parts[1:-1]), parts[-1]
    elif len(parts) == 2:
        artist, title, variant = parts[0], parts[1], ""
    else:
        artist, title, variant = "", name, ""
    if at.get("Artist"):
        artist = at["Artist"][0]
    desc = strip_html(x.get("short_description")) or strip_html(x.get("description"))
    fmt = detect_format(variant) or detect_format(" ".join(at.get("Format", [])))
    if not fmt and parens:
        fmt = detect_format(name) or "LP"
    if not fmt:
        fmt = "Merch/Other" if any(k in cj.lower() for k in MERCH) else (detect_format(cj) or ("LP" if at.get("Format") else "Merch/Other"))
    bm = re.search(r"(?:UPC|EAN|Barcode)\s*:?\s*([\d][\d \-]{7,18}\d)", desc, re.I)
    lm = re.search(r"Label\s*:\s*(.+?)(?=\s*(?:UPC|EAN|Barcode|https?:|Tracklist|Track Listing|$))", desc, re.I)
    # Some shops put the barcode, or failing that the catalogue number, in the product code.
    sku = (x.get("sku") or "").strip() if shop.get("sku_is_id") else ""
    sku_digits = re.sub(r"[\s\-]", "", sku)
    sku_barcode = sku_digits if re.fullmatch(r"\d{10,14}", sku_digits) else ""
    labels = list(at.get("Label", [])) + [t for t in tags if "record" in t.lower()]
    if lm and len(lm.group(1)) < 70:
        labels += [l.strip(" .,") for l in re.split(r",\s*|/", lm.group(1)) if l.strip(" .,")]
    uniq = {}
    for l in labels:  # drop repeats such as "partisan records" / "Partisan Records", keeping the nicer spelling
        k = l.lower()
        if k not in uniq or (uniq[k].islower() and not l.islower()):
            uniq[k] = l
    labels = list(uniq.values())
    year = (at.get("Release Year") or [""])[0]
    used = shop["kind"] == "used" or bool(re.search(r"used", variant, re.I))
    genre = next((c for c in cats if "|" in c and "Upcoming" not in c), "") or next(
        (c for c in cats if c not in ("New Arrivals", "Store", "Sale") and not c.isupper() and not detect_format(c)), "")
    return {
        "id": str(x["id"]), "name": name, "artist": artist, "title": title, "variant": variant, "format": fmt,
        "price": int(x["prices"]["price"]) / 100, "regular": int(x["prices"]["regular_price"]) / 100,
        "sale": bool(x.get("on_sale")), "stock": bool(x.get("is_in_stock")),
        "back": bool(x.get("is_on_backorder")),
        "pre": ("Upcoming | Pre-order" in cats) or any(c.upper().startswith("PRE-ORDER") for c in cats) or bool(re.search(r"UPCOMING|PRE-ORDER", name, re.I)),
        "used": used, "media": (at.get("Media Condition") or [""])[0], "sleeve": (at.get("Sleeve Condition") or [""])[0],
        "labels": labels, "year": year if re.fullmatch(r"(19|20)\d\d", year) else "",
        "barcode": re.sub(r"\D", "", bm.group(1)) if bm else sku_barcode,
        "catno": "" if sku_barcode else sku, "genre": genre, "desc": desc[:700], "url": x["permalink"],
    }
