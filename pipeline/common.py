"""Shared helpers: HTTP, Discogs client, text normalisation, encryption."""
import gzip, html, json, os, re, time, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def load_env():
    """Read KEY=VALUE lines from a local .env file without overriding real environment variables."""
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def http_get(url, headers=None, tries=4, timeout=40):
    """Return (body_bytes, response_headers) or (None, None) after repeated failures."""
    h = {"User-Agent": BROWSER_UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"}
    h.update(headers or {})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return r.read(), r.headers
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404) and attempt >= 1:
                return None, None
            time.sleep(3 + 3 * attempt)
        except Exception:
            time.sleep(3 + 3 * attempt)
    return None, None


class Discogs:
    def __init__(self, token, gap=1.05):
        self.h = {"User-Agent": "VinylPriceWatch/1.0", "Authorization": "Discogs token=" + token}
        self.gap, self.last, self.calls = gap, 0.0, 0

    def get(self, path, **q):
        url = "https://api.discogs.com/" + path + ("?" + urllib.parse.urlencode(q) if q else "")
        for attempt in range(6):
            wait = self.gap - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.calls += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=self.h), timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(30)
                    continue
                if e.code in (401, 403, 404):
                    return None
                time.sleep(5)
            except Exception:
                time.sleep(5)
        return None

    def prices(self, release_id):
        """Lowest listing, copies for sale and suggested price per condition for one release."""
        s = self.get(f"marketplace/stats/{release_id}", curr_abbr="EUR") or {}
        g = self.get(f"marketplace/price_suggestions/{release_id}") or {}
        return {
            "n": s.get("num_for_sale"),
            "low": (s.get("lowest_price") or {}).get("value"),
            "sugg": {k: round(v["value"], 2) for k, v in g.items()} or None,
            "ts": int(time.time()),
        }


def norm(s):
    return re.sub(r"[^\w]+", " ", html.unescape(s or "").lower()).strip()


def strip_html(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# --- encryption: salt(16) | iv(12) | AES-256-GCM(gzip(json)); key = PBKDF2-SHA256(password, salt, 200k) ---
ITER = 200_000


def _key(password, salt):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(password.encode("utf-8"))


def encrypt_json(obj, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = os.urandom(16), os.urandom(12)
    data = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return salt + iv + AESGCM(_key(password, salt)).encrypt(iv, data, None)


def decrypt_json(blob, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv, ct = blob[:16], blob[16:28], blob[28:]
    return json.loads(gzip.decompress(AESGCM(_key(password, salt)).decrypt(iv, ct, None)).decode("utf-8"))
