"""One-off: build the initial state from the first manual matching run (JSON files in seed/)."""
import json, os, sys, time

from .common import ROOT, encrypt_json, load_env


def main():
    load_env()
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "seed")
    load = lambda name: json.load(open(os.path.join(src, name), encoding="utf-8"))
    state = {"matches": {}, "catnos": {}, "prices": {}, "items": {}, "seen": {}, "was": {}, "fetched": {}}
    now = int(time.time())
    for sid, fn in (("discobole", "discogs_matches3.json"), ("scarecrow", "sc_matches2.json")):
        ms = load(fn)
        for pid, m in ms.items():
            rid = m.get("release_id")
            if rid and "num_for_sale" in m:
                state["prices"][str(rid)] = {"n": m.get("num_for_sale"), "low": m.get("lowest"), "sugg": m.get("sugg"), "ts": now}
            for k in ("num_for_sale", "lowest", "sugg", "sugg_cur", "q_artist", "q_title", "shop_catno", "kind"):
                m.pop(k, None)
            m.setdefault("ts", now)
        state["matches"][sid] = ms
    state["catnos"]["discobole"] = load("db_barcodes.json")
    open(os.path.join(ROOT, "state.enc"), "wb").write(encrypt_json(state, os.environ["APP_PASSWORD"]))
    print({k: len(v) for k, v in state["matches"].items()}, "prices", len(state["prices"]))


if __name__ == "__main__":
    main()
