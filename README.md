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
