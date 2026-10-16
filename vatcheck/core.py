"""Core VIES lookup with sqlite cache, retries/backoff and HU enrichment."""
from __future__ import annotations
import asyncio, json, os, re, sqlite3, time
from typing import Any
import httpx

VIES_URL = os.getenv("VIES_URL", "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number")
DEFAULT_REQUESTER = os.getenv("VATCHECK_REQUESTER", "HU33119856")
CACHE_PATH = os.getenv("VATCHECK_CACHE", os.path.join(os.path.dirname(__file__), "..", "cache.sqlite"))
CACHE_TTL_VALID = int(os.getenv("VATCHECK_TTL_VALID", 86400))      # 24h
CACHE_TTL_INVALID = int(os.getenv("VATCHECK_TTL_INVALID", 3600))   # 1h
MAX_RETRIES = int(os.getenv("VATCHECK_RETRIES", 4))

# Rough per-country formats (after country prefix). Used only to reject obvious garbage early.
FORMATS = {
    "AT": r"U\d{8}", "BE": r"[01]\d{9}", "BG": r"\d{9,10}", "CY": r"\d{8}[A-Z]", "CZ": r"\d{8,10}",
    "DE": r"\d{9}", "DK": r"\d{8}", "EE": r"\d{9}", "EL": r"\d{9}", "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}", "FR": r"[A-HJ-NP-Z0-9]{2}\d{9}", "HR": r"\d{11}", "HU": r"\d{8}", "IE": r"\d[A-Z0-9+*]\d{5}[A-Z]{1,2}",
    "IT": r"\d{11}", "LT": r"(\d{9}|\d{12})", "LU": r"\d{8}", "LV": r"\d{11}", "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}", "PL": r"\d{10}", "PT": r"\d{9}", "RO": r"\d{2,10}", "SE": r"\d{12}",
    "SI": r"\d{8}", "SK": r"\d{10}", "XI": r"(\d{9}|\d{12}|GD\d{3}|HA\d{3})",
}
# VIES errors meaning "try again later", not "invalid"
TRANSIENT = {"MS_UNAVAILABLE", "TIMEOUT", "SERVICE_UNAVAILABLE", "MS_MAX_CONCURRENT_REQ",
             "GLOBAL_MAX_CONCURRENT_REQ", "SERVER_BUSY", "MS_MAX_CONCURRENT_REQ_TIME", "GLOBAL_MAX_CONCURRENT_REQ_TIME"}


def normalize(vat: str) -> tuple[str, str]:
    v = re.sub(r"[\s.\-/]", "", (vat or "").upper())
    if len(v) < 4 or not v[:2].isalpha():
        raise ValueError("VAT must start with a 2-letter country code, e.g. HU12345678")
    cc, num = v[:2], v[2:]
    if cc == "GR":
        cc = "EL"
    if cc not in FORMATS:
        raise ValueError(f"Unsupported country code {cc}; VIES covers EU-27 + XI")
    if cc == "HU" and re.fullmatch(r"\d{11}", num):  # domestic HU tax number 12345678-2-42 -> first 8
        num = num[:8]
    if not re.fullmatch(FORMATS[cc], num):
        raise ValueError(f"{cc}{num} does not match the {cc} VAT format")
    return cc, num


def _clean(s: Any) -> Any:
    if isinstance(s, str):
        s = s.strip().replace("\n", ", ")
        return None if s in ("", "---") else s
    return s


# ---------- cache ----------
_db_lock = asyncio.Lock()

def _db() -> sqlite3.Connection:
    c = sqlite3.connect(CACHE_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS vies (key TEXT PRIMARY KEY, ts REAL, data TEXT)")
    return c

def cache_get(key: str) -> dict | None:
    with _db() as c:
        row = c.execute("SELECT ts, data FROM vies WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    data = json.loads(row[1])
    ttl = CACHE_TTL_VALID if data.get("valid") else CACHE_TTL_INVALID
    return data if time.time() - row[0] < ttl else None

def cache_put(key: str, data: dict) -> None:
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO vies VALUES (?,?,?)", (key, time.time(), json.dumps(data)))


# ---------- HU enrichment (free, local, no scraping) ----------
HU_FORMS = [("KORLÁTOLT FELELŐSSÉGŰ TÁRSASÁG", "Kft."), ("ZÁRTKÖRŰEN MŰKÖDŐ RÉSZVÉNYTÁRSASÁG", "Zrt."),
            ("NYILVÁNOSAN MŰKÖDŐ RÉSZVÉNYTÁRSASÁG", "Nyrt."), ("BETÉTI TÁRSASÁG", "Bt."),
            ("KÖZKERESETI TÁRSASÁG", "Kkt."), ("EGYÉNI VÁLLALKOZÓ", "e.v."), ("SZÖVETKEZET", "Szövetkezet")]

def hu_checksum_ok(num8: str) -> bool:
    """Hungarian törzsszám check digit: weights 9,7,3,1,9,7,3 on first 7 digits."""
    w = [9, 7, 3, 1, 9, 7, 3]
    s = sum(int(d) * k for d, k in zip(num8[:7], w))
    return (10 - s % 10) % 10 == int(num8[7])

def hu_enrich(num: str, name: str | None, address: str | None) -> dict:
    out: dict[str, Any] = {"tax_id_base": num, "checksum_valid": hu_checksum_ok(num)}
    if name:
        up = name.upper()
        out["legal_form"] = next((short for long, short in HU_FORMS if long in up), None)
    if address:
        m = re.search(r"\b(\d{4})\b", address)
        if m:
            out["postal_code"] = m.group(1)
            out["county_or_budapest"] = "Budapest" if m.group(1).startswith("1") else None
    out["registry_links"] = {
        "e_cegjegyzek_search": "https://www.e-cegjegyzek.hu/?cegadatlap",
        "nav_taxpayer_search": "https://nav.gov.hu/adatbazisok/adatbleker/afaalanyok",
    }
    out["nav_online_szamla"] = {"status": "not_configured",
        "note": "NAV queryTaxpayer needs a free NAV Online Számla technical user (set NAV_* env vars)."}
    return out


# ---------- VIES ----------
async def _vies_call(client: httpx.AsyncClient, cc: str, num: str, req: str | None) -> dict:
    body = {"countryCode": cc, "vatNumber": num}
    if req:
        rcc, rnum = normalize(req)
        body.update(requesterMemberStateCode=rcc, requesterNumber=rnum)
    delay, last_err = 0.8, None
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = await client.post(VIES_URL, json=body, timeout=20)
            if r.status_code >= 500 or r.status_code == 429:
                last_err = f"HTTP_{r.status_code}"
                try:
                    j = r.json()
                    errs = [e.get("error") for e in j.get("errorWrappers", [])]
                    if errs: last_err = errs[0]
                except Exception:
                    pass
            else:
                j = r.json()
                errs = [e.get("error") for e in j.get("errorWrappers", [])] if "errorWrappers" in j else []
                err = j.get("userError") if j.get("userError") not in (None, "VALID", "INVALID") else (errs[0] if errs else None)
                if err and err in TRANSIENT:
                    last_err = err
                elif err:
                    return {"_error": err, "_attempts": attempt + 1}
                else:
                    j["_attempts"] = attempt + 1
                    return j
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_err = f"NETWORK:{type(e).__name__}"
        if attempt < MAX_RETRIES:
            await asyncio.sleep(delay)
            delay *= 2
    return {"_error": last_err or "UNKNOWN", "_transient": True, "_attempts": MAX_RETRIES + 1}


async def verify(vat: str, requester: str | None = DEFAULT_REQUESTER, use_cache: bool = True,
                 client: httpx.AsyncClient | None = None) -> dict:
    try:
        cc, num = normalize(vat)
    except ValueError as e:
        return {"input": vat, "status": "invalid_format", "valid": False, "error": str(e)}
    key = f"{cc}{num}|{requester or ''}"
    if use_cache and (hit := cache_get(key)):
        return {**hit, "cached": True}
    own = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": "vatcheck/0.1"})
    try:
        j = await _vies_call(client, cc, num, requester)
    finally:
        if own:
            await client.aclose()
    if "_error" in j:
        status = "member_state_unavailable" if j.get("_transient") else "error"
        return {"input": vat, "vat": f"{cc}{num}", "country_code": cc, "status": status, "valid": None,
                "error": j["_error"], "attempts": j["_attempts"],
                "retry_after_seconds": 60 if j.get("_transient") else None}
    res = {
        "input": vat, "vat": f"{cc}{num}", "country_code": cc, "vat_number": num,
        "status": "valid" if j.get("valid") else "invalid", "valid": bool(j.get("valid")),
        "name": _clean(j.get("name")), "address": _clean(j.get("address")),
        "request_date": j.get("requestDate"),
        "consultation_number": _clean(j.get("requestIdentifier")),
        "requester": requester, "source": "VIES (European Commission)", "cached": False,
    }
    if cc == "HU":
        res["hu"] = hu_enrich(num, res["name"], res["address"])
    cache_put(key, res)
    return res


async def verify_batch(vats: list[str], requester: str | None = DEFAULT_REQUESTER, concurrency: int = 4) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(headers={"User-Agent": "vatcheck/0.1"}) as client:
        async def one(v):
            async with sem:
                return await verify(v, requester, client=client)
        return await asyncio.gather(*(one(v) for v in vats))
