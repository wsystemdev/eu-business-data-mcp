"""Fallbacks used when VIES cannot answer for a member state (MS_UNAVAILABLE / timeout).

Order: 1) the member state's free, public, official national source (where one exists and allows
automated queries without credentials), 2) the last successful VIES answer we cached, labelled with its date.
A fallback is NEVER a VIES confirmation: there is no VIES consultation number, and the national registers
confirm the *domestic* VAT/company status, which usually but not always matches the EU (intra-community) status.
"""
from __future__ import annotations
import datetime as dt, json, re, sqlite3, time
import httpx

UA = {"User-Agent": "vatcheck/0.2 (+https://wsystem.dev)", "Accept": "application/json"}

# Research summary (2026-10). Only countries marked "implemented" are queried automatically.
NATIONAL_SOURCES = {
    "PL": {"status": "implemented", "source": "Biała lista podatników VAT (Ministry of Finance / KAS), wl-api.mf.gov.pl",
           "note": "Free, no key. Limit ~100 searches/day per IP. Confirms domestic VAT status (Czynny/Zwolniony), not VAT-UE."},
    "CZ": {"status": "implemented", "source": "ARES (Ministry of Finance), ares.gov.cz REST",
           "note": "Free, no key. Returns DIČ and whether the VAT (DPH) register record is active."},
    "FI": {"status": "implemented", "source": "YTJ open data API (PRH / Finnish Tax Administration), avoindata.prh.fi",
           "note": "Free, no key. Shows whether the business is in the VAT register (arvonlisäverorekisteri)."},
    "FR": {"status": "implemented", "source": "Recherche d'entreprises API (DINUM, SIRENE data), recherche-entreprises.api.gouv.fr",
           "note": "Free, no key. Confirms the SIREN exists and is active and that the VAT key matches; France has no free public VAT-registration API."},
    "DE": {"status": "not_possible", "note": "BZSt eVatR is free but only accepts requests from a German VAT number (USt-IdNr) as requester."},
    "ES": {"status": "not_possible", "note": "AEAT VAT/NIF census check requires an electronic certificate."},
    "IT": {"status": "not_possible", "note": "Agenzia delle Entrate 'Verifica partita IVA' is a captcha-protected web form; no free public API."},
    "NL": {"status": "not_possible", "note": "No free public VAT check; KVK API is paid/keyed."},
    "BE": {"status": "not_possible", "note": "KBO/BCE Public Search is a web UI; the open-data dump needs registration and is not a live VAT check."},
    "HU": {"status": "not_possible", "note": "NAV Online Számla queryTaxpayer needs a NAV technical user (credentials)."},
    "DK": {"status": "not_possible", "note": "CVR system-to-system access requires credentials."},
    "SK": {"status": "not_possible", "note": "Finančná správa open data is keyed / list-based; not wired."},
}


def _today():
    return dt.date.today().isoformat()


def _base(cc, num, source, valid, legal_status, name=None, address=None, source_url=None, **extra):
    return {"vat": f"{cc}{num}", "country_code": cc, "vat_number": num,
            "status": "valid" if valid else ("invalid" if valid is False else "unknown"), "valid": valid,
            "name": name, "address": address, "request_date": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            "consultation_number": None, "fallback": True, "fallback_type": "national_source",
            "source": source, "source_url": source_url, "legal_status": legal_status,
            "fallback_note": "VIES was unavailable for this member state. This answer comes from the national source "
                             "named in `source`; it is not a VIES confirmation and has no consultation number. "
                             "Re-check in VIES later for audit proof.", **extra}


async def _pl(c, cc, num):
    r = await c.get(f"https://wl-api.mf.gov.pl/api/search/nip/{num}", params={"date": _today()}, timeout=15)
    j = r.json()
    if r.status_code != 200:
        raise RuntimeError(j.get("message") or f"HTTP {r.status_code}")
    s = (j.get("result") or {}).get("subject")
    if not s:
        return _base(cc, num, NATIONAL_SOURCES["PL"]["source"], False, "Not in the Biała lista (not registered for VAT in Poland)")
    st = s.get("statusVat")
    return _base(cc, num, NATIONAL_SOURCES["PL"]["source"], st == "Czynny",
                 f"Polish VAT status: {st} (Czynny = active VAT payer, Zwolniony = exempt)",
                 s.get("name"), s.get("workingAddress") or s.get("residenceAddress"),
                 "https://www.podatki.gov.pl/wykaz-podatnikow-vat-wyszukiwarka", national_status_raw=st,
                 national_request_id=(j.get("result") or {}).get("requestId"))


async def _cz(c, cc, num):
    if len(num) != 8:  # individuals (birth-number DIČ) are not in ARES by DIČ
        raise RuntimeError("ARES fallback only covers 8-digit (IČO-based) CZ VAT numbers")
    r = await c.get(f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{num}", timeout=15)
    if r.status_code == 404:
        return _base(cc, num, NATIONAL_SOURCES["CZ"]["source"], False, "IČO not found in ARES")
    r.raise_for_status()
    j = r.json()
    dph = (j.get("seznamRegistraci") or {}).get("stavZdrojeDph")
    ok = dph == "AKTIVNI" and (j.get("dic") or "").upper() == f"CZ{num}"
    return _base(cc, num, NATIONAL_SOURCES["CZ"]["source"], ok,
                 f"Czech VAT (DPH) register record: {dph or 'none'}", j.get("obchodniJmeno"),
                 (j.get("sidlo") or {}).get("textovaAdresa"), f"https://ares.gov.cz/ekonomicke-subjekty?ico={num}",
                 national_status_raw=dph)


async def _fi(c, cc, num):
    bid = f"{num[:7]}-{num[7]}"
    r = await c.get("https://avoindata.prh.fi/opendata-ytj-api/v3/companies", params={"businessId": bid}, timeout=15)
    r.raise_for_status()
    cs = r.json().get("companies") or []
    if not cs:
        return _base(cc, num, NATIONAL_SOURCES["FI"]["source"], False, "Business ID not found in YTJ")
    co = cs[0]
    vat = [e for e in co.get("registeredEntries") or [] if str(e.get("register")) == "6" and not e.get("endDate")]
    name = next((n["name"] for n in co.get("names") or [] if str(n.get("type")) == "1" and not n.get("endDate")), None)
    a = next(iter(co.get("addresses") or []), {}) or {}
    addr = ", ".join(x for x in [a.get("street"), a.get("buildingNumber"), a.get("postCode"),
                                 next((p.get("city") for p in a.get("postOffices") or []), None)] if x) or None
    return _base(cc, num, NATIONAL_SOURCES["FI"]["source"], bool(vat),
                 "In the Finnish VAT register since " + vat[0].get("registrationDate", "?") if vat else "Not in the Finnish VAT register",
                 name, addr, f"https://tietopalvelu.ytj.fi/yritys/{bid}")


def fr_key_ok(num: str) -> bool:
    if not num[:2].isdigit():
        return True  # new-style alphanumeric keys: cannot verify locally
    return int(num[:2]) == (12 + 3 * (int(num[2:]) % 97)) % 97


async def _fr(c, cc, num):
    siren = num[2:]
    if not fr_key_ok(num):
        return _base(cc, num, "Local check (FR VAT key algorithm)", False, "VAT key does not match the SIREN")
    r = await c.get("https://recherche-entreprises.api.gouv.fr/search", params={"q": siren, "per_page": 1}, timeout=15)
    r.raise_for_status()
    res = [x for x in r.json().get("results") or [] if x.get("siren") == siren]
    if not res:
        return _base(cc, num, NATIONAL_SOURCES["FR"]["source"], False, "SIREN not found in SIRENE")
    x = res[0]
    active = x.get("etat_administratif") == "A"
    return _base(cc, num, NATIONAL_SOURCES["FR"]["source"], True if active else False,
                 ("Company active in SIRENE and VAT key matches SIREN (VAT registration itself not confirmable from a free French source)"
                  if active else "Company ceased (cessée) in SIRENE"),
                 x.get("nom_complet"), (x.get("siege") or {}).get("adresse"),
                 f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}", national_status_raw=x.get("etat_administratif"))


HANDLERS = {"PL": _pl, "CZ": _cz, "FI": _fi, "FR": _fr}


async def national(cc: str, num: str, client: httpx.AsyncClient | None = None) -> dict | None:
    h = HANDLERS.get(cc)
    if not h:
        return None
    own = client is None
    c = client or httpx.AsyncClient(headers=UA)
    try:
        return await h(c, cc, num)
    except Exception as e:  # national source also failing -> let caller try the cache
        return {"_national_error": f"{type(e).__name__}: {e}"[:200]}
    finally:
        if own:
            await c.aclose()


def last_vies(db_path: str, cc: str, num: str) -> dict | None:
    """Most recent successful VIES answer for this VAT number (any requester, any age)."""
    try:
        with sqlite3.connect(db_path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS vies_last (vat TEXT PRIMARY KEY, ts REAL, data TEXT)")
            row = c.execute("SELECT ts, data FROM vies_last WHERE vat=?", (f"{cc}{num}",)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    d = json.loads(row[1])
    checked = dt.datetime.fromtimestamp(row[0], dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    return {**d, "fallback": True, "fallback_type": "last_vies_result", "cached": True,
            "source": f"VIES (European Commission) — cached result from {checked[:10]}",
            "last_vies_check": checked, "consultation_number": None,
            "original_consultation_number": d.get("consultation_number"),
            "legal_status": f"Valid in VIES on {checked[:10]}" if d.get("valid") else f"Invalid in VIES on {checked[:10]}",
            "fallback_note": f"VIES was unavailable now. This is the last successful VIES answer, from {checked}. "
                             "It is not a current confirmation; the original consultation number (if any) belongs to that earlier check."}


def remember_vies(db_path: str, res: dict) -> None:
    try:
        with sqlite3.connect(db_path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS vies_last (vat TEXT PRIMARY KEY, ts REAL, data TEXT)")
            c.execute("INSERT OR REPLACE INTO vies_last VALUES (?,?,?)", (res["vat"], time.time(), json.dumps(res)))
    except sqlite3.Error:
        pass
