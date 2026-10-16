"""One-call KYB supplier/customer check: EU VAT (VIES) + official business register + sanctions screening."""
import asyncio, re
from rapidfuzz import fuzz
from . import core as vies, company as registry, sanctions

REG_COUNTRIES = {"FR", "NO", "FI", "CZ", "PL", "EE"}


def _registry_id_from_vat(cc, num):
    if cc == "FR" and len(num) == 11: return num[2:]
    if cc == "CZ" and len(num) == 8: return num
    if cc == "FI" and len(num) == 8: return f"{num[:7]}-{num[7]}"
    return None


def parse_input(item):
    """Accept 'FR40303265045', 'NO:equinor', or {name, vatNumber, country, registryId}."""
    if isinstance(item, dict):
        return {"name": (item.get("name") or "").strip() or None, "vat": (item.get("vatNumber") or item.get("vat") or "").strip() or None,
                "country": (item.get("country") or "").strip().upper() or None, "registry_id": (str(item.get("registryId") or "")).strip() or None}
    s = str(item).strip()
    m = re.fullmatch(r"([A-Za-z]{2})\s*:\s*(.+)", s)
    if m:
        cc, rest = m.group(1).upper(), m.group(2).strip()
        return {"name": None if re.search(r"\d{5}", rest) else rest, "vat": None, "country": cc,
                "registry_id": rest if re.search(r"\d{5}", rest) else None}
    compact = re.sub(r"[\s.\-]", "", s.upper())
    if re.fullmatch(r"[A-Z]{2}[0-9A-Z+*]{2,13}", compact) and sum(ch.isdigit() for ch in compact) >= 5:
        return {"name": None, "vat": compact, "country": None, "registry_id": None}
    return {"name": s, "vat": None, "country": None, "registry_id": None}


async def check(item, requester=None, threshold=85, client=None):
    p = parse_input(item)
    out = {"input": item if isinstance(item, str) else p, "company_name": p["name"], "country": p["country"],
           "verdict": None, "reasons": [], "vat": None, "registry": None, "sanctions": None}
    reasons = out["reasons"]
    # 1) VAT via VIES
    vat_name = None
    if p["vat"]:
        v = await vies.verify(p["vat"], requester or None)
        out["vat"] = {k: v.get(k) for k in ("vat", "status", "valid", "name", "address", "consultation_number", "request_date", "error")}
        out["country"] = out["country"] or (v.get("country_code") or p["vat"][:2]).replace("EL", "GR")
        vat_name = v.get("name") if v.get("name") not in (None, "---") else None
        if v.get("status") == "invalid": reasons.append("FAIL: VAT number is not valid in VIES")
        elif v.get("status") == "invalid_format": reasons.append("FAIL: VAT number has an invalid format")
        elif v.get("status") != "valid": reasons.append("REVIEW: VIES could not confirm the VAT number right now (member state unavailable)")
        if not p["registry_id"]:
            try: vcc, vnum = vies.normalize(p["vat"])
            except ValueError: vcc, vnum = None, None
            if vcc:
                p["registry_id"] = _registry_id_from_vat(vcc, vnum)
                out["country"] = out["country"] or vcc.replace("EL", "GR")
    cc = out["country"]
    name = p["name"] or vat_name
    out["company_name"] = name
    # 2) Registry
    q = p["registry_id"] or name
    if q:
        r = await registry.lookup(q, cc if cc in REG_COUNTRIES or not cc else cc, 3, client)
        best = None
        if r["results"]:
            best = r["results"][0]
            if name and not p["registry_id"]:
                best = max(r["results"], key=lambda x: fuzz.token_set_ratio(sanctions.norm(name), sanctions.norm(x["name"] or "")))
        out["registry"] = {"status": r["status"], "error": r["error"], "match": best}
        if best:
            out["company_name"] = name = name or best["name"]
            if best.get("active") is False: reasons.append(f"FAIL: company is not active in the register ({best.get('status')})")
            if vat_name and best.get("name"):
                sim = fuzz.token_set_ratio(sanctions.norm(vat_name), sanctions.norm(best["name"]))
                if sim < 70: reasons.append(f"REVIEW: VIES name '{vat_name}' differs from register name '{best['name']}'")
        elif r["status"] == "error": reasons.append("REVIEW: business register temporarily unavailable")
        else: reasons.append("REVIEW: company not found in the business register" + ("" if cc in REG_COUNTRIES else " (GLEIF LEI index used for this country)"))
    # 3) Sanctions
    if name:
        s = await asyncio.to_thread(sanctions.get_screener)
        names = {name} | ({vat_name} if vat_name else set())
        hits = []
        for n in names: hits += s.screen(n, min(threshold, 80), limit=5)
        hits = sorted({(h["list"], h["list_id"]): h for h in hits}.values(), key=lambda h: -h["score"])[:5]
        top = hits[0]["score"] if hits else 0
        out["sanctions"] = {"status": "potential_match" if top >= threshold else ("weak_match" if hits else "no_match"),
                            "top_score": top, "matches": hits, "lists_date": s.index["built_at"]}
        if top >= max(threshold, 90): reasons.append(f"FAIL: strong sanctions match ({hits[0]['list']}: {hits[0]['sanctioned_name']}, score {top})")
        elif top >= threshold: reasons.append(f"REVIEW: possible sanctions match ({hits[0]['list']}: {hits[0]['sanctioned_name']}, score {top})")
    else:
        reasons.append("REVIEW: no company name available for sanctions screening")
    out["verdict"] = "fail" if any(x.startswith("FAIL") for x in reasons) else ("review" if reasons else "pass")
    return out


async def check_many(items, requester=None, threshold=85, concurrency=4):
    sem = asyncio.Semaphore(concurrency)
    async def one(i):
        async with sem: return await check(i, requester, threshold)
    return await asyncio.gather(*[one(i) for i in items])
