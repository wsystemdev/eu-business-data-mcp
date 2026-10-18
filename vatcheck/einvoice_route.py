"""'Can I e-invoice this company?' lookup: VIES VAT check + Peppol Directory + country mandate table.
France (PPF annuaire via PISTE) is not included yet."""
from __future__ import annotations
import asyncio, json, os, re, sqlite3, time
import httpx

DIR_URL = "https://directory.peppol.eu/search/1.0/json"
CACHE_DB = os.getenv("VATCHECK_CACHE", "cache.sqlite")
TTL = 7 * 86400
_sem = asyncio.Semaphore(1)
_last = [0.0]
INVOICE_DOC = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1"
CREDIT_DOC = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2::CreditNote##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1"

# Curated mandate table. as_of 2026-10-18. Sources: EY e-invoicing tracker (Aug/Oct 2026), VATupdate chronology 2026-10-08, official pages listed.
MANDATES = {
    "BE": {"b2b_mandatory": True, "since": "2026-01-01", "scope": "Domestic B2B between Belgian VAT-registered businesses (penalties since 2026-04-01).",
           "format": "Peppol BIS Billing 3.0 (UBL) via Peppol", "channel": "Peppol", "next": "Near-real-time e-reporting planned 2028-01-01 (draft law).",
           "source": "https://finance.belgium.be/en/enterprises/vat/e-invoicing"},
    "FR": {"b2b_mandatory": True, "since": "2026-09-01", "scope": "All businesses must be able to RECEIVE e-invoices since 2026-09-01; large and mid-size must issue; SMEs/micro issue from 2027-09-01.",
           "format": "UBL, CII or Factur-X (EN 16931) via a certified platform (PA)", "channel": "Plateformes Agreees (PA) + PPF directory",
           "next": "SME/micro issuing obligation 2027-09-01.", "source": "https://www.impots.gouv.fr/facturation-electronique",
           "note": "French directory (annuaire PPF) lookup not included yet."},
    "DE": {"b2b_mandatory": True, "since": "2025-01-01", "scope": "All businesses must be able to RECEIVE EN 16931 e-invoices since 2025-01-01; issuing mandatory from 2027-01-01 (prior-year turnover > EUR 800k) and 2028-01-01 for all.",
           "format": "XRechnung or ZUGFeRD/Factur-X EN 16931 profile (or other EN 16931 formats by agreement)", "channel": "Any (e-mail allowed); Peppol optional",
           "next": "Issuing obligation 2027-01-01 / 2028-01-01.", "source": "https://www.bundesfinanzministerium.de/Content/DE/FAQ/e-rechnung.html"},
    "PL": {"b2b_mandatory": True, "since": "2026-02-01", "scope": "KSeF mandatory for large taxpayers since 2026-02-01, other VAT taxpayers since 2026-04-01; micro taxpayers 2027-01-01.",
           "format": "KSeF FA(3) XML (not UBL/Peppol)", "channel": "KSeF (clearance)", "next": "Micro taxpayers 2027-01-01.", "source": "https://ksef.podatki.gov.pl/"},
    "IT": {"b2b_mandatory": True, "since": "2019-01-01", "scope": "Domestic B2B/B2C via SdI clearance.", "format": "FatturaPA XML", "channel": "SdI (clearance)",
           "source": "https://www.agenziaentrate.gov.it/portale/web/english/e-invoicing"},
    "RO": {"b2b_mandatory": True, "since": "2024-01-01", "scope": "Domestic B2B via RO e-Factura.", "format": "RO_CIUS (UBL/CII, EN 16931)", "channel": "RO e-Factura (clearance)",
           "source": "https://mfinante.gov.ro/web/efactura"},
    "HR": {"b2b_mandatory": True, "since": "2026-01-01", "scope": "Domestic B2B for VAT-registered businesses (Fiscalisation 2.0); non-VAT taxpayers issue from 2027-01-01.",
           "format": "EN 16931 (HR CIUS, UBL)", "channel": "Fiscalisation 2.0 / information intermediaries", "source": "https://www.porezna-uprava.hr/"},
    "GR": {"b2b_mandatory": True, "since": "2026-03-02", "scope": "Phase A (revenue > EUR 1m) since 2026-03-02; Phase B (others) from 2026-11-02, full application 2027-02-01.",
           "format": "EN 16931 via certified providers + myDATA", "channel": "Certified e-invoicing providers", "source": "https://www.aade.gr/"},
    "ES": {"b2b_mandatory": False, "since": None, "scope": "B2B e-invoicing from 2027-10-06 (> EUR 8m turnover) and 2028-10-06 (others); VERI*FACTU software rules from 2027.",
           "format": "UBL, CII, Facturae, EDIFACT (planned)", "channel": "Private platforms + AEAT solution (planned)", "next": "2027-10-06.",
           "source": "https://sede.agenciatributaria.gob.es/"},
    "HU": {"b2b_mandatory": False, "since": None, "scope": "No structured B2B e-invoice mandate; real-time invoice data reporting (RTIR) to NAV Online Szamla applies to invoices.",
           "format": "Any invoice format; data reported to NAV", "channel": "NAV Online Szamla reporting", "source": "https://onlineszamla.nav.gov.hu/"},
}
DEFAULT_MANDATE = {"b2b_mandatory": False, "since": None, "scope": "No domestic B2B e-invoice mandate in force (B2G e-invoicing per Directive 2014/55/EU applies). EU ViDA: cross-border B2B digital reporting from 2030-07-01.",
                   "format": "EN 16931 recommended (UBL / Peppol BIS 3 widely accepted)", "channel": "Peppol common for B2G", "source": "https://taxation-customs.ec.europa.eu/taxation/vat/vat-digital-age-vida_en"}

# Peppol participant schemes to try per country, built from the VAT ID.
def candidates(vat: str) -> list[str]:
    v = re.sub(r"[^A-Z0-9]", "", vat.upper())
    cc, num = v[:2], v[2:]
    out = []
    if cc == "BE":
        out += [f"0208:{num.zfill(10)}", f"9925:{v.lower()}"]
    scheme = {"AT": "9915", "DE": "9930", "DK": "0184", "ES": "9920", "FR": "9957", "GR": "9933", "EL": "9933", "HU": "9910", "IE": "9935",
              "IT": "0211", "LU": "9938", "NL": "9944", "PL": "9945", "PT": "9946", "RO": "9947", "SI": "9949", "SK": "9950", "CZ": "9929",
              "EE": "9931", "HR": "9934", "LT": "9937", "LV": "9939", "MT": "9943", "BG": "9926", "CY": "9928", "SE": None, "FI": None}.get(cc)
    if scheme:
        out.append(f"{scheme}:{v.lower()}")
    if cc == "DK":
        out.append(f"0184:{num}")
    if cc == "SE" and len(num) == 12:
        out.append(f"0007:{num[:10]}")
    if cc == "FI" and len(num) == 8:
        out.append(f"0216:0037{num}")
    if cc == "NO":
        out.append(f"0192:{num[:9]}")
    if cc == "IT":
        out.append(f"0210:{num}")
    return list(dict.fromkeys(out))


def _db():
    c = sqlite3.connect(CACHE_DB)
    c.execute("CREATE TABLE IF NOT EXISTS peppol_dir (pid TEXT PRIMARY KEY, ts REAL, body TEXT)")
    return c


async def directory_lookup(pid: str) -> dict | None:
    c = _db()
    row = c.execute("SELECT ts, body FROM peppol_dir WHERE pid=?", (pid,)).fetchone()
    if row and time.time() - row[0] < TTL:
        return json.loads(row[1])
    async with _sem:  # Peppol Directory allows 2 queries/second
        wait = 0.55 - (time.time() - _last[0])
        if wait > 0:
            await asyncio.sleep(wait)
        _last[0] = time.time()
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "wsystem.dev e-invoice route lookup (+https://wsystem.dev)"}) as h:
            r = await h.get(DIR_URL, params={"participant": f"iso6523-actorid-upis::{pid}"})
    if r.status_code != 200:
        raise RuntimeError(f"Peppol Directory HTTP {r.status_code}")
    d = r.json()
    m = (d.get("matches") or [None])[0]
    res = None
    if m:
        docs = [x["value"] for x in m.get("docTypes") or []]
        ent = (m.get("entities") or [{}])[0]
        res = {"participant_id": f"iso6523-actorid-upis::{m['participantID']['value']}",
               "name": ((ent.get("name") or [{}])[0]).get("name"), "country": ent.get("countryCode"),
               "registered_since": ent.get("regDate"), "supports_peppol_bis3_invoice": INVOICE_DOC in docs,
               "supports_peppol_bis3_creditnote": CREDIT_DOC in docs, "document_types": len(docs),
               "document_type_ids": docs[:30]}
    c.execute("INSERT OR REPLACE INTO peppol_dir VALUES (?,?,?)", (pid, time.time(), json.dumps(res)))
    c.commit()
    return res


def _recommend(country: str, peppol: dict | None) -> dict:
    m = MANDATES.get(country, DEFAULT_MANDATE)
    if peppol and peppol.get("supports_peppol_bis3_invoice"):
        return {"format": "peppol-bis3", "channel": "Peppol", "create_with": "POST /einvoice/create {\"format\":\"peppol-bis3\"}",
                "why": "The company is registered on Peppol for BIS Billing 3.0 invoices."}
    if country == "DE":
        return {"format": "xrechnung", "channel": "E-mail or Peppol", "create_with": "POST /einvoice/create {\"format\":\"xrechnung\"}",
                "why": "German businesses must accept EN 16931 e-invoices; XRechnung is the national CIUS."}
    if country in ("PL", "IT"):
        return {"format": m["format"], "channel": m["channel"], "create_with": None,
                "why": "This country uses its own clearance format; our create endpoint does not produce it yet."}
    return {"format": "en16931", "channel": m.get("channel"), "create_with": "POST /einvoice/create {\"format\":\"en16931\"}",
            "why": "EN 16931 UBL is the EU standard; check with the buyer which channel they accept."}


async def route(identifier: str, requester: str | None = None) -> dict:
    from . import core
    raw = (identifier or "").strip()
    v = re.sub(r"[^A-Za-z0-9:]", "", raw).upper()
    if not v:
        raise ValueError("id is required (EU VAT number, e.g. BE0403170701, or a Peppol id like 0208:0403170701)")
    out = {"query": raw}
    peppol, tried = None, []
    if re.fullmatch(r"\d{4}:[A-Z0-9]+", v):
        pids, country, vat_res = [v.lower()], None, None
    else:
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2,14}", v) or sum(ch.isdigit() for ch in v) < 5:
            raise ValueError("id must be an EU VAT number with country prefix (e.g. DE811193231) or a Peppol participant id (scheme:value)")
        country = "GR" if v[:2] == "EL" else v[:2]
        if country == "FR":
            out["france"] = "French directory (PPF annuaire) lookup is not available yet; Peppol and VIES results only."
        vat_res = await core.verify(v, requester)
        pids = candidates(v)
    for pid in pids:
        tried.append(f"iso6523-actorid-upis::{pid}")
        try:
            peppol = await directory_lookup(pid)
        except Exception as e:
            out["peppol_error"] = str(e)
            break
        if peppol:
            break
    if country is None and peppol:
        country = peppol.get("country")
    out["vat"] = None if vat_res is None else {k: vat_res.get(k) for k in ("vat", "status", "valid", "name", "address", "consultation_number") if k in vat_res}
    out["peppol"] = {"registered": bool(peppol), **(peppol or {}), "ids_checked": tried,
                     "note": "Checked in the public Peppol Directory. A company can be on Peppol without a published business card, so 'false' means 'not found in the directory', not 'cannot receive'."}
    out["country"] = country
    out["mandate"] = {**MANDATES.get(country, DEFAULT_MANDATE), "country": country, "as_of": "2026-10-18"}
    out["recommendation"] = _recommend(country or "", peppol)
    out["sources"] = {"vat": "EU VIES", "peppol": "Peppol Directory (OpenPeppol AISBL) https://directory.peppol.eu", "mandate": out["mandate"].get("source")}
    out["disclaimer"] = "Information aid, not tax advice. Mandate data is curated and dated; confirm with the official source."
    return out
