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


def _grade(s):
    """Shop wording for a record's condition -> the Discogs grade name."""
    s = s.lower()
    for key, grade in (("near mint", "Near Mint"), ("nm", "Near Mint"), ("very good plus", "Very Good Plus"), ("vg+", "Very Good Plus"),
                       ("very good", "Very Good"), ("vg", "Very Good"), ("good plus", "Good Plus"), ("g+", "Good Plus"),
                       ("mint", "Mint"), ("good", "Good"), ("fair", "Fair"), ("poor", "Poor")):
        if key in s:
            return grade
    return ""


def fetch_cscart(shop):
    """All products of a CS-Cart shop listing (Zaharias Records): the listing gives name, price and link,
    each product page gives barcode / catalogue number, label, year and condition.

    Returns them in the Store API's shape, so parse() handles every kind of shop."""
    found, page = {}, 1
    sep = "&" if "?" in shop["cscart"] else "?"
    while page <= 200:
        body, _ = http_get(f"{shop['cscart']}{sep}items_per_page=96&page={page}")
        if body is None:
            return None
        blocks = re.split(r'(?=<div class="ty-grid-list__item )', body.decode("utf-8", "ignore"))[1:]
        if not blocks and page == 1:
            return None  # the page layout changed: keep last week's copy rather than an empty shop
        new = 0
        for b in blocks:
            t = re.search(r'<a href="([^"]+)" class="product-title"[^>]*>(.*?)</a>', b, re.S)
            pid = re.search(r'name="product_data\[(\d+)\]\[product_id\]"', b)
            price = re.search(r'id="sec_discounted_price_\d+"[^>]*>([\d.,]+)', b)
            if not (t and pid and price) or pid.group(1) in found:
                continue
            was = re.search(r'id="sec_list_price_\d+"[^>]*>([\d.,]+)', b)
            cents = lambda m: int(round(float(m.group(1).replace(",", "")) * 100))
            found[pid.group(1)] = {
                "id": int(pid.group(1)), "name": strip_html(t.group(2)), "permalink": html.unescape(t.group(1)),
                "prices": {"price": cents(price), "regular_price": cents(was) if was else cents(price)},
                "on_sale": bool(was) and cents(was) > cents(price),
            }
            new += 1
        if not new or len(blocks) < 96:
            break
        page += 1
        time.sleep(1.5)
    failed = []
    for x in found.values():
        body, _ = http_get(x["permalink"], tries=3)
        if body is None:  # one dead product page must not cost the whole shop
            failed.append(str(x["id"]))
            continue
        p = body.decode("utf-8", "ignore")
        f = {strip_html(k).strip(" :"): strip_html(v) for k, v in re.findall(
            r'ty-product-feature__label[^>]*>(.*?)</(?:div|span)>.*?ty-product-feature__value[^>]*>(.*?)</div>', p, re.S)}
        code = re.search(r'id="product_code_\d+"[^>]*>\s*([^<]*)', p)
        used = bool(f.get("HAND")) and "new" not in f["HAND"].lower()
        names = {"Artist": "Artist", "Format": "Format", "Label": "Label", "Year": "Release Year"}
        x.update({
            "sku": html.unescape(code.group(1)).strip() if code else "",
            "is_in_stock": "OutOfStock" not in p, "is_on_backorder": False, "used": used, "vinyl": True,
            "variant": f.get("Details", ""),
            "attributes": [{"name": names[k], "terms": [{"name": v.strip()} for v in (f[k].split(",") if k == "Label" else [f[k]]) if v.strip()]}
                           for k in names if f.get(k)]
                          + ([{"name": "Media Condition", "terms": [{"name": _grade(f.get("Media condition", ""))}]},
                              {"name": "Sleeve Condition", "terms": [{"name": _grade(f.get("Sleeve condition", ""))}]}] if used else []),
            "categories": [{"name": f["Style / Genre"]}] if f.get("Style / Genre") else [],
            "short_description": " ".join(v for v in (f.get("Details"), f.get("Country")) if v),
        })
        time.sleep(1)
    if failed:
        print(f"[{shop['id']}] {len(failed)} product pages did not open: {', '.join(failed[:10])}", flush=True)
    if len(failed) > len(found) / 10:
        return None
    return [x for x in found.values() if str(x["id"]) not in failed]


def fetch_dirtynoise(shop):
    """Dirty Noise: its catalogue pages list artist, title, format, label, genre, barcode and price.
    `catalog` in shops.json is a list of filtered catalogue addresses; a record found in several is kept once.

    Returns the products in the Store API's shape, so parse() handles every kind of shop."""
    found = {}
    for base in shop["catalog"]:
        page, pages = 1, 1
        while page <= pages:
            body, _ = http_get(f"{base}&per_page=100&page={page}")
            if body is None:
                return None
            h = body.decode("utf-8", "ignore")
            total = re.search(r"There are (\d+) products", h)
            blocks = re.split(r'(?=<div class="basis-auto xs:basis-2/3)', h)[1:]
            if not total or (not blocks and int(total.group(1))):
                return None  # the page layout changed: keep last week's copy rather than an empty shop
            pages = -(-int(total.group(1)) // 100)
            for b in blocks:
                pid = re.search(r'name="wishlist_product_id"[^>]*value="(\d+)"', b)
                artist = re.search(r"fa-microphone[^>]*></i>(.*?)</a>", b, re.S)
                t = re.search(r'<a href="([^"]+/product/[^"]+)"[^>]*>\s*<i class="fa-solid fa-record-vinyl[^>]*></i>(.*?)</a>', b, re.S)
                box = re.search(r'<div class="text-red text-lg font-bold mb-4">(.*?)</div>', b, re.S)
                euros = [int(round(float(v.replace(",", "")) * 100)) for v in re.findall(r"€\s*([\d.,]+)", box.group(1) if box else "")]
                if not (pid and t and euros):
                    continue
                field = lambda label: strip_html((re.search(r"<span>" + label + r": </span>(.*?)</div>", b, re.S) or ["", ""])[1])
                title = strip_html(t.group(2))
                vm = re.search(r"\(([^()]*)\)\s*$", title)
                found[pid.group(1)] = {
                    "id": int(pid.group(1)), "name": f"{strip_html(artist.group(1)) if artist else ''} – {title}", "title": title,
                    "variant": ", ".join(v for v in (field("Format").split(" Type")[0], vm.group(1) if vm else "") if v),
                    "permalink": html.unescape(t.group(1)),
                    "prices": {"price": euros[-1], "regular_price": euros[0]}, "on_sale": len(euros) > 1 and euros[0] > euros[-1],
                    "is_in_stock": "Ships in 1-3 days" in b, "is_on_backorder": "days after you place your order" in b,
                    "sku": field("EAN/UPC"), "vinyl": True,
                    "attributes": [{"name": "Artist", "terms": [{"name": strip_html(artist.group(1))}]}] * bool(artist)
                                  + [{"name": "Label", "terms": [{"name": l.strip()} for l in field("Labels").split(",") if l.strip()]}] * bool(field("Labels")),
                    "categories": [{"name": field("Genres")}] if field("Genres") else [],
                    "short_description": field("Type"),
                }
            page += 1
            time.sleep(1.5)
    return list(found.values())


def fetch_products(shop):
    """All products from the shop's public WooCommerce Store API, or None if the shop is unreachable."""
    if shop.get("catalog"):
        return fetch_dirtynoise(shop)
    if shop.get("listing"):
        return fetch_listing(shop)
    if shop.get("cscart"):
        return fetch_cscart(shop)
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
    if shop.get("variant") == "discogs":
        # Shop fed from its own Discogs inventory: "Artist – Title – Near Mint (NM or M-) – Vinyl", product code = Discogs listing number
        gm = re.match(r"(.*?)\s+[–-]\s+(.*)\s+[–-]\s+((?:Mint|Near Mint|Very Good|Good|Fair|Poor|Generic|Not Graded)[^–]*?)\s+[–-]\s+[\w, ]+$", name)
        full = strip_html(x.get("description"))
        dm = re.search(r"Description:\s*(.*?)\s*(?:Condition:|$)", full)
        sm = re.search(r"Condition:\s*(.+)", full)
        if gm:
            artist, title = gm.group(1), gm.group(2)
            at["Media Condition"] = [_grade(gm.group(3))]
        variant = dm.group(1) if dm else ""
        at["Sleeve Condition"] = [_grade(sm.group(1))] if sm else [""]
        um = re.match(r"(.*?)\s+[–-]\s+(.*)\s+[–-]\s+\(([\w+-]+)/([\w+-]+)\)\s+[–-]\s+Vinyl", name)
        if um and not gm:  # records the shop cannot list on Discogs: "Artist – Title – (NM/VG+) – Vinyl"
            short = {"M": "Mint", "NM": "Near Mint", "M-": "Near Mint", "VG+": "Very Good Plus", "VG": "Very Good", "G+": "Good Plus", "G": "Good"}
            artist, title = um.group(1), um.group(2)
            variant = re.split(r"\s*Media Condition", variant)[0]
            at["Media Condition"], at["Sleeve Condition"] = [short.get(um.group(3).upper(), "")], [short.get(um.group(4).upper(), "")]
    if at.get("Artist"):
        artist = at["Artist"][0]
    variant = x.get("variant") or variant
    title = x.get("title") or title
    desc = strip_html(x.get("short_description")) or strip_html(x.get("description"))
    dd = {}
    if shop.get("discogs_desc"):
        # The description is the record's Discogs header: "Label: A, B – CATNO Format: Vinyl, LP, Blue Country: .. Released: .. Genre: .. Style: .."
        full = re.sub("[​-‏‪-‮﻿]", "", strip_html(x.get("description")))
        stop = r"\s*(?:Label:|Series:|Format:|Country:|Released:|Genre:|Style:|Tracklist|Notes|$)"
        dd = {k: (re.search(k + r":\s*(.*?)" + stop, full) or ["", ""])[1] for k in ("Label", "Format", "Released", "Style", "Genre")}
        pairs = re.findall(r"(.+?)\s+[–-]\s+(.+?)(?:\s*,\s+|$)", dd["Label"])
        dd["labels"] = [re.sub(r"\s*\(\d+\)$", "", l).strip() for names, _ in pairs for l in names.split(",") if l.strip()]
        dd["catno"] = next((c.strip() for _, c in pairs if c.strip().lower() != "none"), "")
        dd["year"] = (re.findall(r"\b((?:19|20)\d\d)\b", dd["Released"]) or [""])[-1]
        title = re.sub(r"\s+\d?x?LP$", "", title, flags=re.I)
        variant = ", ".join(v for v in (variant, dd["Format"]) if v)
        if dd["labels"]:
            at["Label"] = dd["labels"]
        if dd["year"]:
            at["Release Year"] = [dd["year"]]
        if dd["Label"]:
            desc = full  # keep the cleaned text; the label line is already read, see `lm` below
    fmt = detect_format(variant) or detect_format(" ".join(at.get("Format", [])))
    if not fmt and (parens or x.get("vinyl") or shop.get("discogs_desc")):
        fmt = detect_format(name) or "LP"
    if not fmt:
        fmt = "Merch/Other" if any(k in cj.lower() for k in MERCH) else (detect_format(cj) or ("LP" if at.get("Format") else "Merch/Other"))
    bm = re.search(r"(?:UPC|EAN|Barcode)\s*:?\s*([\d][\d \-]{7,18}\d)", desc, re.I)
    lm = re.search(r"Label\s*:\s*(.+?)(?=\s*(?:UPC|EAN|Barcode|https?:|Tracklist|Track Listing|$))", desc, re.I)
    # Some shops put the barcode, or failing that the catalogue number, in the product code.
    sku = (x.get("sku") or "").strip() if shop.get("sku_is_id") else ""
    if dd:  # product code = supplier's three letters + barcode ("MLK075678576379"), else a catalogue number
        sku = re.sub(r"^[A-Za-z]{2,4}\s*(?=[\d\s\-]{10,}$)", "", sku)
        lm = None
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
    used = shop["kind"] == "used" or bool(x.get("used")) or bool(re.search(r"used", variant, re.I))
    genre = next((c for c in cats if "|" in c and "Upcoming" not in c), "") or next(
        (c for c in cats if c not in ("New Arrivals", "Store", "Sale") and not c.isupper() and not detect_format(c)), "")
    # a description without the "Tracklist" heading runs straight into "A1 Song ..": cut there
    genre = re.split(r"\s+(?:[A-HΑ-Θ]\d{0,2}|\d{1,2})[.\s]", dd.get("Style") or dd.get("Genre") or genre)[0][:60]
    return {
        "id": str(x["id"]), "name": name, "artist": artist, "title": title, "variant": variant, "format": fmt,
        "price": int(x["prices"]["price"]) / 100, "regular": int(x["prices"]["regular_price"]) / 100,
        "sale": bool(x.get("on_sale")), "stock": bool(x.get("is_in_stock")),
        "back": bool(x.get("is_on_backorder")),
        "pre": ("Upcoming | Pre-order" in cats) or any(c.upper().startswith("PRE-ORDER") for c in cats) or bool(re.search(r"UPCOMING|PRE-ORDER", name, re.I)),
        "used": used, "media": (at.get("Media Condition") or [""])[0], "sleeve": (at.get("Sleeve Condition") or [""])[0],
        "labels": labels, "year": year if re.fullmatch(r"(19|20)\d\d", year) else "",
        "barcode": re.sub(r"\D", "", bm.group(1)) if bm else sku_barcode,
        "catno": dd.get("catno") or ("" if sku_barcode else sku), "listing": (x.get("sku") or "").strip() if shop.get("variant") == "discogs" else "",
        "genre": genre, "desc": desc[:700], "url": x["permalink"],
    }
