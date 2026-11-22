"""Weekly refresh: download every shop, match new records on Discogs, refresh prices, publish encrypted data.

Usage:  python -m pipeline.run [--xlsx] [--no-fetch] [--rebuild]
Needs DISCOGS_TOKEN and APP_PASSWORD (environment or a local .env file).
"""
import datetime, json, os, re, sys, time

from .common import ROOT, Discogs, decrypt_json, encrypt_json, load_env, norm
from .match import COLOURS, match_item
from .shops import fetch_catno, fetch_products, parse

STATE = os.path.join(ROOT, "state.enc")
DATA = os.path.join(ROOT, "docs", "data.enc")
PRICE_MAX_AGE = 6 * 86400          # refresh Discogs prices older than this
RETRY_UNMATCHED = 28 * 86400       # retry records Discogs did not know after this long
TIME_BUDGET = float(os.environ.get("TIME_BUDGET_MIN", "300")) * 60
GRADE = {"Mint": "Mint (M)", "Near Mint": "Near Mint (NM or M-)", "Very Good Plus": "Very Good Plus (VG+)",
         "Very Good": "Very Good (VG)", "Good Plus": "Good Plus (G+)", "Good": "Good (G)", "Fair": "Fair (F)", "Poor": "Poor (P)"}


FAR = {"Argentina", "Brazil", "Mexico", "Japan", "Russia", "Ukraine", "South Korea", "Taiwan", "China", "Indonesia",
       "Malaysia", "Thailand", "Colombia", "Chile", "Venezuela", "Peru", "South Africa", "India"}


def confidence(m, it):
    """How sure we are that the Discogs release is the very edition the shop sells."""
    if not m.get("release_id"):
        return ""
    if m["match"] in ("catno", "barcode"):
        conf = "exact" if not m.get("tie") else "high"
    elif m["match"] == "artist+title":
        ok = (m.get("title_ok") or m.get("year_ok")) and (m.get("label_ok") or not it["labels"])
        conf = "high" if ok else "medium"
    else:
        return "low"
    if not it["used"]:
        # New stock: these signs point to a different edition sharing the title or the barcode.
        want = {c for c in COLOURS if c in norm(it["variant"])}
        got = {c for c in COLOURS if c in norm(m.get("d_text") or "")}
        if bool(want) != bool(got) or (want and not want & got):
            conf = "medium"   # plain record against a coloured pressing, or the reverse
        special = re.findall(r"limited|deluxe|special|numbered|signed|all media|box set|club edition|mispress|misprint|anniversary|picture",
                             ((m.get("d_format") or "") + " " + (m.get("d_text") or "")).lower())
        mine = (it["name"] + " " + it["desc"]).lower()
        if m["match"] == "artist+title" and not (want & got) and any(w.split()[0] not in mine for w in special):
            conf = "medium"   # Discogs entry is a limited / deluxe / boxed edition the shop does not mention
        if m.get("d_country") in FAR:
            conf = "medium"   # licensed pressing from another continent
        try:
            year = int(m.get("d_year") or 0)
        except Exception:
            year = 0
        if m["match"] == "artist+title" and year and year < datetime.date.today().year - 15:
            conf = "low"      # a shop selling new copies almost never has the decades-old original
        elif year and year < datetime.date.today().year - 15 and conf in ("exact", "high"):
            conf = "medium"   # reissues often keep the original's barcode / catalogue number: the price is the old pressing's
    return conf


def flag(it, m, p, conf):
    """Return (flag, discogs_reference_price, ratio)."""
    if it["format"] == "Merch/Other":
        return "not music", None, None
    if not m.get("release_id"):
        return "no Discogs match", None, None
    if "Unofficial" in (m.get("d_format") or ""):
        return "no Discogs price", None, None   # bootlegs cannot be sold on Discogs, so its prices mean nothing
    grade = GRADE.get(it["media"], "Very Good Plus (VG+)") if it["used"] else "Near Mint (NM or M-)"
    ref = ((p or {}).get("sugg") or {}).get(grade)
    if not ref:
        return "no Discogs price", None, None
    ratio, low = round(it["price"] / ref, 2), (p or {}).get("low")
    if ratio <= 0.85 and (conf not in ("exact", "high") or ratio <= 0.4):
        return "check match", ref, ratio        # too good to be true, or not surely the same edition
    # With plenty of copies listed, the cheapest listing is the real market price: the shop has to beat it.
    n = (p or {}).get("n") or 0
    if ratio <= 0.85 and (not low or it["price"] <= low * (0.9 if n >= 10 else 1.1)):
        return "BUY", ref, ratio
    if ratio <= 0.85:
        return "below average, but as cheap on Discogs", ref, ratio
    return ("fair" if ratio <= 1.15 else "above market"), ref, ratio


def build_rows(state, shops):
    rows = []
    for shop in shops:
        sid = shop["id"]
        for it in state["items"].get(sid, []):
            m = state["matches"].get(sid, {}).get(it["id"]) or {}
            p = state["prices"].get(str(m.get("release_id"))) if m.get("release_id") else None
            conf = confidence(m, it)
            fl, ref, ratio = flag(it, m, p, conf)
            rows.append({
                "shop": shop["name"], "flag": fl, "ratio": ratio, "artist": it["artist"], "title": it["title"], "variant": it["variant"],
                "format": it["format"], "cond": (it["media"] or "Used") if it["used"] else "New", "sleeve": it["sleeve"],
                "price": it["price"], "was": state["was"].get(sid, {}).get(it["id"]), "dg": ref,
                "low": (p or {}).get("low"), "nfs": (p or {}).get("n"), "want": m.get("want"), "have": m.get("have"),
                "conf": conf, "how": m.get("match", ""), "stock": it["stock"], "back": it.get("back", False), "pre": it["pre"], "sale": it["sale"],
                "genre": it["genre"], "label": ", ".join(it["labels"][:2]), "seen": state["seen"].get(sid, {}).get(it["id"], ""),
                "dtitle": m.get("d_title", ""), "dyear": m.get("d_year", ""), "dcountry": m.get("d_country", ""),
                "dlabel": m.get("d_label", ""), "dcat": m.get("d_catno", ""),
                "dfmt": ((m.get("d_format") or "") + " " + (m.get("d_text") or "")).strip(),
                "rid": m.get("release_id"), "url": it["url"],
            })
    return rows


def write_xlsx(rows, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    fill = {"BUY": "C6EFCE", "fair": "FFF2CC", "above market": "F8CBAD", "below average, but as cheap on Discogs": "DDEBF7", "check match": "E7E6E6"}
    cols = [("shop", "Shop"), ("flag", "Flag"), ("ratio", "Ratio shop / Discogs"), ("artist", "Artist"), ("title", "Title"),
            ("variant", "Variant"), ("format", "Format"), ("cond", "Condition"), ("price", "Shop price"), ("dg", "Discogs avg price"),
            ("low", "Discogs lowest listed"), ("nfs", "Copies for sale"), ("want", "Want"), ("have", "Have"), ("conf", "Match confidence"),
            ("how", "Matched by"), ("stock", "In stock"), ("pre", "Pre-order"), ("genre", "Genre"), ("label", "Label"),
            ("dtitle", "Discogs title"), ("dyear", "Discogs year"), ("dcountry", "Discogs country"), ("dcat", "Discogs cat. no."),
            ("dfmt", "Discogs format"), ("durl", "Discogs link"), ("url", "Shop link")]
    wb = Workbook()
    wb.remove(wb.active)
    shops = list(dict.fromkeys(r["shop"] for r in rows))
    for title, part in [("Best buys", [r for r in rows if r["flag"] == "BUY"])] + [(sh, [r for r in rows if r["shop"] == sh]) for sh in shops]:
        _sheet(wb.create_sheet(title[:31]), part, cols, fill)
    wb.save(path)


def _sheet(ws, rows, cols, fill):
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    ws.append([c[1] for c in cols])
    order = {"BUY": 0, "check match": 1, "below average, but as cheap on Discogs": 2, "fair": 3, "above market": 4}
    for r in sorted(rows, key=lambda r: (order.get(r["flag"], 9), r["ratio"] if r["ratio"] is not None else 99)):
        r = dict(r, durl=f"https://www.discogs.com/release/{r['rid']}" if r["rid"] else "")
        ws.append([("yes" if r[k] else "no") if isinstance(r[k], bool) else r[k] for k, _ in cols])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="305496")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for i, (k, title) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = {"artist": 28, "title": 38, "dtitle": 38, "dfmt": 36, "flag": 22}.get(k, 14)
        for row in range(2, ws.max_row + 1):
            c = ws.cell(row, i)
            if k in ("url", "durl") and c.value:
                c.hyperlink = c.value
                c.font = Font(color="0563C1", underline="single")
            elif k in ("price", "dg", "low"):
                c.number_format = '#,##0.00 "€"'
            elif k == "ratio" and c.value is not None:
                v = c.value
                c.fill = PatternFill("solid", fgColor="63BE7B" if v <= 0.7 else "C6EFCE" if v <= 0.85 else "FFF2CC" if v <= 1.15 else "F8CBAD" if v <= 1.5 else "F4877A")
            elif k == "flag" and c.value in fill:
                c.fill = PatternFill("solid", fgColor=fill[c.value])


def main():
    load_env()
    t0 = time.time()
    token, password = os.environ["DISCOGS_TOKEN"], os.environ["APP_PASSWORD"]
    shops = json.load(open(os.path.join(ROOT, "shops.json"), encoding="utf-8"))
    state = decrypt_json(open(STATE, "rb").read(), password) if os.path.exists(STATE) else {}
    for k in ("matches", "catnos", "prices", "items", "seen", "was", "fetched"):
        state.setdefault(k, {})
    rebuild = "--rebuild" in sys.argv   # only recompute verdicts from what is stored: no downloads, no Discogs calls
    dg = Discogs(token)
    today = datetime.date.today().isoformat()
    out_of_time = lambda: time.time() - t0 > TIME_BUDGET
    stale = []
    save = lambda: None if rebuild else open(STATE, "wb").write(encrypt_json(state, password))  # checkpoint: an interrupted run loses nothing

    for shop in ([] if rebuild else shops):
        sid = shop["id"]
        for k in ("matches", "catnos", "seen", "was"):
            state[k].setdefault(sid, {})
        if "--no-fetch" not in sys.argv:
            raw = fetch_products(shop)
            if not raw:
                print(f"[{sid}] could not download the catalogue; keeping last week's copy", flush=True)
                stale.append(sid)
            else:
                items = [it for it in (parse(shop, x) for x in raw) if it["price"] > 0]
                old = {it["id"]: it["price"] for it in state["items"].get(sid, [])}
                first_run = not state["seen"][sid]
                for it in items:
                    state["seen"][sid].setdefault(it["id"], "" if first_run else today)
                    if it["id"] in old and old[it["id"]] != it["price"]:
                        state["was"][sid][it["id"]] = old[it["id"]]
                state["items"][sid] = items
                state["fetched"][sid] = today
                print(f"[{sid}] {len(items)} products", flush=True)
        new = 0
        for it in sorted(state["items"].get(sid, []), key=lambda it: not it["stock"]):  # records you can buy first
            if it["format"] == "Merch/Other" or out_of_time():
                continue
            m = state["matches"][sid].get(it["id"])
            if m is not None and m.get("name") is None:
                m["name"] = it["name"]  # seeded match: remember what it was matched against
            fresh = m is not None and m.get("name") == it["name"] and (m.get("release_id") or time.time() - m.get("ts", 0) < RETRY_UNMATCHED)
            if fresh:
                continue
            if shop.get("catno_from_page") and it["id"] not in state["catnos"][sid]:
                cat = fetch_catno(it["url"])
                if cat is not None:
                    state["catnos"][sid][it["id"]] = cat
                time.sleep(0.8)
            m = match_item(dg, it, state["catnos"][sid].get(it["id"], "") or it.get("catno", ""))
            m["name"], m["ts"] = it["name"], int(time.time())
            state["matches"][sid][it["id"]] = m
            new += 1
            if new % 25 == 0:
                print(f"[{sid}] matched {new} new records", flush=True)
            if new % 100 == 0:
                save()
        live = {it["id"] for it in state["items"].get(sid, [])}
        if sid not in stale and live:  # forget products the shop no longer lists
            for k in ("matches", "catnos", "was"):
                state[k][sid] = {pid: v for pid, v in state[k][sid].items() if pid in live}
        print(f"[{sid}] {new} newly matched", flush=True)

    save()
    used = {str(m["release_id"]) for s in state["matches"].values() for m in s.values() if m.get("release_id")}
    state["prices"] = {rid: p for rid, p in state["prices"].items() if rid in used}
    todo = [] if rebuild else sorted((rid for rid in used if time.time() - state["prices"].get(rid, {}).get("ts", 0) > PRICE_MAX_AGE),
                  key=lambda rid: state["prices"].get(rid, {}).get("ts", 0))
    done = 0
    for rid in todo:
        if out_of_time():
            break
        state["prices"][rid] = dg.prices(rid)
        done += 1
        if done % 100 == 0:
            print(f"prices refreshed {done}/{len(todo)}", flush=True)
            save()
    print(f"prices refreshed {done}/{len(todo)}; Discogs calls {dg.calls}", flush=True)

    rows = build_rows(state, shops)
    data = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes"),
            "shops": [{"name": s["name"], "url": s["base"], "fetched": state["fetched"].get(s["id"], ""), "stale": s["id"] in stale} for s in shops],
            "rows": rows}
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    open(DATA, "wb").write(encrypt_json(data, password))
    save()
    if "--xlsx" in sys.argv:
        os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
        write_xlsx(rows, os.path.join(ROOT, "out", "vinyl_price_watch.xlsx"))
    counts = {}
    for r in rows:
        counts[r["flag"]] = counts.get(r["flag"], 0) + 1
    print(len(rows), "rows", counts, flush=True)


if __name__ == "__main__":
    main()
