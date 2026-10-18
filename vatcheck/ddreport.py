"""Counterparty due-diligence report: VAT (VIES) + business register + sanctions (via kyb) + EORI + Peppol/e-invoice route + GLEIF LEI.
Deterministic risk flags; public company data only (no personal UBO data). Not a regulated AML/KYC decision."""
from __future__ import annotations
import asyncio, re, time
from datetime import datetime, timezone
import httpx
from rapidfuzz import fuzz
from . import kyb, eori as eori_mod, einvoice_route, sanctions

GLEIF = "https://api.gleif.org/api/v1/lei-records"
DISCLAIMER = ("Automated screening aid built from public sources at the timestamp shown. It is not a regulated AML/KYC decision, "
              "credit rating or legal advice. Sanctions hits are 'possible match, verify' and need human review.")


def registered_as(vat: str | None) -> str | None:
    """National registry number GLEIF stores in entity.registeredAs, derived from the VAT ID where that is reliable."""
    if not vat:
        return None
    cc, num = vat[:2].upper(), vat[2:]
    if cc == "BE" and num.isdigit():
        n = num.zfill(10)
        return f"{n[:4]}.{n[4:7]}.{n[7:]}"  # GLEIF stores Belgian KBO numbers dotted
    if cc == "FR" and len(num) == 11:
        return num[2:]
    if cc in ("CZ", "NO") and num.isdigit():
        return num[:9] if cc == "NO" else num
    if cc == "IT" and num.isdigit():
        return num
    return None


async def gleif_lookup(name: str | None, country: str | None, lei: str | None = None, reg: str | None = None) -> dict:
    params = {"page[size]": 10}
    if lei:
        params["filter[lei]"] = lei
    elif reg:
        params["filter[entity.registeredAs]"] = reg
    elif name:
        params["filter[entity.legalName]"] = name
        if country:
            params["filter[entity.legalAddress.country]"] = country
    else:
        return {"status": "skipped"}
    try:
        async with httpx.AsyncClient(timeout=15) as h:
            r = await h.get(GLEIF, params=params, headers={"Accept": "application/vnd.api+json"})
        data = r.json().get("data") or []
        if reg and country:
            data = [d for d in data if d["attributes"]["entity"]["legalAddress"]["country"] == country]
        if reg and not data and name:
            return await gleif_lookup(name, country)
        if not data and name and not lei and not reg:
            params.pop("filter[entity.legalName]")
            params["filter[fulltext]"] = name
            async with httpx.AsyncClient(timeout=15) as h:
                data = (await h.get(GLEIF, params=params, headers={"Accept": "application/vnd.api+json"})).json().get("data") or []
    except Exception as e:
        return {"status": "error", "error": str(e)}
    best, score = None, 0
    for d in data:
        a = d["attributes"]
        n = a["entity"]["legalName"]["name"]
        s = 100 if (lei or reg) else fuzz.token_sort_ratio(sanctions.norm(name or ""), sanctions.norm(n))
        s += 0.5 if a["entity"].get("status") == "ACTIVE" else 0  # prefer the active record among equal matches
        if s > score:
            best, score = d, s
    if not best or score < 88:
        return {"status": "not_found"}
    a = best["attributes"]
    return {"status": "found", "lei": a["lei"], "legal_name": a["entity"]["legalName"]["name"], "country": a["entity"]["legalAddress"]["country"],
            "entity_status": a["entity"].get("status"), "registration_status": a["registration"].get("status"),
            "next_renewal": a["registration"].get("nextRenewalDate"), "match_score": int(score),
            "matched_by": "lei" if lei else ("registry_number" if reg else "name"),
            "source": f"https://search.gleif.org/#/record/{a['lei']}"}


async def report(company: str, country: str = "", requester: str | None = None, threshold: float = 85) -> dict:
    t0 = time.time()
    item = f"{country.upper()}:{company}" if country and not re.fullmatch(r"[A-Za-z]{2}\d.*", company.strip()) else company
    k = await kyb.check(item, requester, threshold)
    vat = (k.get("vat") or {}).get("vat")
    cc = k.get("country")
    name = k.get("company_name")
    tasks = {"gleif": gleif_lookup(name, cc, reg=registered_as(vat))}
    if vat:
        tasks["route"] = einvoice_route.route(vat, requester)
        tasks["eori"] = eori_mod.validate_one(vat)
    res = dict(zip(tasks, await asyncio.gather(*tasks.values(), return_exceptions=True)))
    route = res.get("route")
    route = None if isinstance(route, Exception) or route is None else route
    e = res.get("eori")
    e = None if isinstance(e, Exception) or e is None else {k2: e.get(k2) for k2 in ("eori", "status", "valid", "name", "city", "country")}
    g = res.get("gleif")
    g = {"status": "error", "error": str(g)} if isinstance(g, Exception) else g

    flags = []
    def flag(sev, code, text):
        flags.append({"severity": sev, "code": code, "text": text})
    lei_active = bool(g and g.get("status") == "found" and g.get("matched_by") == "registry_number" and g.get("entity_status") == "ACTIVE")
    for r in k["reasons"]:
        sev = "high" if r.startswith("FAIL") else "medium"
        if lei_active and "not active in the register" in r and cc not in kyb.REG_COUNTRIES:
            # KYB's name-based GLEIF match hit an old record; the registry-number match says ACTIVE.
            flag("low", "REGISTER_NAME_MATCH_INACTIVE", "A same-name GLEIF record is inactive, but the record for this registry number is ACTIVE")
            continue
        flag(sev, "KYB", r.split(": ", 1)[-1])
    if g and g.get("status") == "found":
        if g.get("entity_status") not in (None, "ACTIVE"):
            flag("high", "LEI_ENTITY_INACTIVE", f"GLEIF entity status is {g['entity_status']}")
        if g.get("registration_status") == "LAPSED":
            flag("low", "LEI_LAPSED", "LEI registration has lapsed (not renewed)")
    if e and e.get("valid") is True:
        pass
    if route:
        m = route.get("mandate") or {}
        if m.get("b2b_mandatory") and not route["peppol"]["registered"] and cc == "BE":
            flag("medium", "NO_PEPPOL_BE", "Belgian B2B e-invoicing is mandatory but the company was not found in the Peppol Directory")
    score = 0
    for f in flags:
        score += {"high": 60, "medium": 20, "low": 5}[f["severity"]]
    score = min(score, 100)
    verdict = "fail" if any(f["severity"] == "high" for f in flags) else ("review" if any(f["severity"] == "medium" for f in flags) else "pass")
    return {"company": name, "country": cc, "input": company, "verdict": verdict, "risk_score": score, "flags": flags,
            "checks": {"vat": k.get("vat"), "registry": k.get("registry"), "sanctions": k.get("sanctions"), "eori": e, "lei": g,
                       "einvoice": None if not route else {"peppol": route["peppol"], "mandate": route["mandate"], "recommendation": route["recommendation"]}},
            "sources": {"vat": "EU VIES", "registry": "Official business registers (FR INSEE/SIRENE, NO Brreg, FI PRH, CZ ARES, PL KRS, EE) or GLEIF",
                        "sanctions": "EU consolidated, UN SC, US OFAC SDN, UK sanctions lists (daily)", "eori": "EC EOS",
                        "lei": "GLEIF API (CC0)", "peppol": "Peppol Directory"},
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ms": int((time.time() - t0) * 1000),
            "maker": "W System (W System Hungary Kft.)", "disclaimer": DISCLAIMER}
