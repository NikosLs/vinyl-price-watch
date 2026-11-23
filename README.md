# Vinyl Price Watch

Compares record-shop prices with Discogs and flags what is worth buying. Refreshed weekly by a scheduled task (`refresh.bat`) and shown on a password-protected page (GitHub Pages, `docs/`).

## How it works

1. `pipeline/shops.py` downloads each shop's catalogue from its public WooCommerce Store API (`shops.json` lists the shops).
2. `pipeline/match.py` finds the Discogs release for every new record: catalogue number first, then barcode, then artist + title + label + format (+ vinyl colour).
3. `pipeline/run.py` refreshes Discogs prices (lowest listing and suggested price per condition), computes the verdict and writes:
   - `docs/data.enc` – the data the page shows, encrypted with the page password;
   - `state.enc` – matches and price cache, encrypted the same way, so a weekly run only looks up new records.

Verdict: ratio = shop price ÷ Discogs suggested price for the same condition (Near Mint for new records).
`BUY` = ratio ≤ 0.85 and no cheaper copy listed on Discogs; `fair` = within ±15%; `above market` = more than 15% over.

## Adding a shop

Add an entry to `shops.json`:

```json
{"id": "myshop", "name": "My Shop", "base": "https://example.gr", "query": "", "kind": "new", "catno_from_page": false}
```

- `base` is the address under which `/wp-json/wc/store/v1/products` answers (WooCommerce shops).
- `query` optionally narrows the catalogue (e.g. an attribute filter).
- `variant: "parens"` is for shops that name products `Artist – Title (2LP Red)`; without it the name is read as `Artist – Title – LP`.
- `kind` is `used` (compare against the record's own grade) or `new` (compare against Near Mint).

- `listing` is for WooCommerce shops that keep the Store API closed (Mr Vinylios Records): give the address of a category page and the catalogue is read from the shop's own category pages instead. Only that category is downloaded.
- `cscart` is for CS-Cart shops (Zaharias Records): give the address of a listing, filters included. Each product page is opened for the barcode / catalogue number, label, year and condition.
- `variant: "discogs"` is for shops fed from their own Discogs inventory (Le Disque Noir): products are named `Artist – Title – Near Mint (NM or M-) – Vinyl` and the product code is the Discogs listing number, so each record is matched to its exact release.
- `catalog` is for Dirty Noise: a list of filtered catalogue addresses (availability, format, genre). Only those are downloaded; a record found in several is kept once.
- `sku_is_id: true` is for shops whose product code holds the barcode or, failing that, the catalogue number; it is then used for exact matching.

Shops on other platforms need their own download function in `pipeline/shops.py`.

## Running locally

Create `.env` with `DISCOGS_TOKEN=...` and `APP_PASSWORD=...`, then:

```
pip install cryptography openpyxl
python -m pipeline.run --xlsx
```

`--xlsx` also writes `out/vinyl_price_watch.xlsx`.

## Secrets

Both values live in the local `.env`. Changing `APP_PASSWORD` requires re-encrypting `state.enc` (run locally with the old password, then the new one).
