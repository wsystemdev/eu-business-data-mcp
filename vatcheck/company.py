"""European company registry lookup via official, free, open government APIs.

Sources (all public, no key): FR recherche-entreprises.api.gouv.fr (INSEE Sirene/RNE),
NO data.brreg.no (Bronnoysund), FI avoindata.prh.fi (PRH/YTJ), CZ ares.gov.cz (ARES),
PL api-krs.ms.gov.pl (KRS, by number), EE ariregister.rik.ee (autocomplete),
GLEIF api.gleif.org (LEI, all countries).
"""
import asyncio, re
import httpx

UA = {"User-Agent": "company-registry-lookup/1.0 (+https://apify.com)", "Accept": "application/json"}
SUPPORTED = ["FR", "NO", "FI", "CZ", "PL", "EE", "LEI"]


def _rec(country, rid, name, **kw):
    r = {"country": country, "registry_id": rid, "name": name, "legal_form": None, "status": None,
         "active": None, "address": None, "postal_code": None, "city": None, "registration_date": None,
         "vat_id": None, "activity_code": None, "activity": None, "website": None, "employees": None,
         "lei": None, "source": None, "source_url": None, "status_normalized": "unknown", "status_raw": None}
    r.update({k: v for k, v in kw.items()})
    return r


async def _get(c, url, **kw):
    for i in range(3):
        try:
            r = await c.get(url, **kw)
            if r.status_code in (429, 502, 503, 504):
                await asyncio.sleep(1.5 * (i + 1)); continue
            return r
        except httpx.TransportError:
            if i == 2: raise
            await asyncio.sleep(1.5 * (i + 1))
    return r


STATUS_VALUES = ("active", "in_liquidation", "bankrupt", "dissolved", "unknown")


# ---------- status normalization (pure functions, one per source) ----------
def norm_fr(etat):
    return {"A": "active", "C": "dissolved"}.get(etat, "unknown")


def norm_no(x):
    if x.get("slettedato"): return "dissolved"
    if x.get("konkurs"): return "bankrupt"
    if x.get("underAvvikling") or x.get("underTvangsavviklingEllerTvangsopplosning"): return "in_liquidation"
    return "active" if x.get("organisasjonsnummer") else "unknown"


def _fi_open_situations(x):
    return [s.get("type") for s in x.get("companySituations") or [] if not s.get("endDate")]


def norm_fi(x):
    # PRH situation codes: KONK=bankrupt, SELTILA=liquidation, SANE=re-organisation (company still trading)
    if x.get("endDate"): return "dissolved"
    sit = _fi_open_situations(x)
    if "KONK" in sit: return "bankrupt"
    if "SELTILA" in sit: return "in_liquidation"
    if x.get("tradeRegisterStatus") in ("2", "4") and x.get("tradeRegisterStatus") is not None and not sit:
        return "dissolved"  # removed from register / ceased
    return "active" if (x.get("businessId") or {}).get("value") else "unknown"


def _cz_source_state(x):
    reg = x.get("seznamRegistraci") or {}
    vr = reg.get("stavZdrojeVr")
    return vr if vr and vr != "NEEXISTUJICI" else reg.get("stavZdrojeRos")


def norm_cz(x):
    reg = x.get("seznamRegistraci") or {}
    if x.get("datumZaniku") or _cz_source_state(x) == "ZANIKLY": return "dissolved"
    if reg.get("stavZdrojeIr") == "AKTIVNI": return "bankrupt"  # listed in insolvency register (ISIR)
    if re.search(r"\bv\s+likvidaci\b", x.get("obchodniJmeno") or "", re.I): return "in_liquidation"
    return "active" if _cz_source_state(x) == "AKTIVNI" else "unknown"


def _nonempty(v):
    if isinstance(v, dict): return any(_nonempty(x) for x in v.values())
    if isinstance(v, list): return any(_nonempty(x) for x in v)
    return v not in (None, "", False)


def pl_flags(odpis):
    """Return (normalized, raw flags) from a KRS OdpisAktualny 'odpis' object."""
    d = odpis.get("dane") or {}; d6 = d.get("dzial6") or {}; hdr = odpis.get("naglowekA") or {}
    name = ((d.get("dzial1") or {}).get("danePodmiotu") or {}).get("nazwa") or ""
    deleted = next((v for k, v in hdr.items() if "wykresl" in k.lower() and v), None)
    bankrupt = any(_nonempty(p.get("informacjaOOgloszeniuUpadlosci")) and not _nonempty(p.get("opisZakonczeniaProcesuUpadlosci"))
                   for p in d6.get("postepowanieUpadlosciowe") or [] if isinstance(p, dict))
    liq = any(_nonempty(v) for k, v in d6.items() if k.lower().startswith("likwidac")) or bool(re.search(r"W LIKWIDACJI", name, re.I))
    if not bankrupt and re.search(r"W UPAD[ŁL]O[ŚS]CI", name, re.I): bankrupt = True
    raw = {"bankruptcy": bankrupt, "liquidation": liq, "deleted": deleted}
    st = "dissolved" if deleted else "bankrupt" if bankrupt else "in_liquidation" if liq else "active"
    return st, raw


def norm_ee(code):
    return {"R": "active", "L": "in_liquidation", "N": "bankrupt", "K": "dissolved"}.get(code, "unknown")


def norm_gleif(entity_status):
    return {"ACTIVE": "active", "INACTIVE": "dissolved"}.get(entity_status, "unknown")


# ---------- France ----------
def _fr(x):
    s = x.get("siege") or {}
    etat = x.get("etat_administratif")
    return _rec("FR", x.get("siren"), x.get("nom_complet") or x.get("nom_raison_sociale"),
                legal_form=x.get("nature_juridique"), status=etat, active=(etat == "A") if etat else None,
                address=s.get("adresse"), postal_code=s.get("code_postal"), city=s.get("libelle_commune"),
                registration_date=x.get("date_creation"), activity_code=x.get("activite_principale"),
                employees=x.get("tranche_effectif_salarie"),
                status_normalized=norm_fr(etat), status_raw={"etat_administratif": etat, "date_fermeture": x.get("date_fermeture")},
                vat_id=_fr_vat(x.get("siren")), source="INSEE Sirene / recherche-entreprises.api.gouv.fr",
                source_url=f"https://annuaire-entreprises.data.gouv.fr/entreprise/{x.get('siren')}")


def _fr_vat(siren):
    if not siren or not siren.isdigit(): return None
    return f"FR{(12 + 3 * (int(siren) % 97)) % 97:02d}{siren}"


async def fr(c, q, limit):
    r = await _get(c, "https://recherche-entreprises.api.gouv.fr/search", params={"q": q, "per_page": min(limit, 25)})
    r.raise_for_status()
    return [_fr(x) for x in r.json().get("results", [])][:limit]


# ---------- Norway ----------
def _no(x):
    a = x.get("forretningsadresse") or x.get("postadresse") or {}
    n1 = x.get("naeringskode1") or {}
    gone = x.get("konkurs") or x.get("underAvvikling") or x.get("slettedato")
    return _rec("NO", x.get("organisasjonsnummer"), x.get("navn"),
                legal_form=(x.get("organisasjonsform") or {}).get("beskrivelse"),
                status="deleted/bankrupt/dissolving" if gone else "active", active=not gone,
                address=", ".join(a.get("adresse") or []) or None, postal_code=a.get("postnummer"), city=a.get("poststed"),
                registration_date=x.get("registreringsdatoEnhetsregisteret") or x.get("stiftelsesdato"),
                vat_id=f"NO{x.get('organisasjonsnummer')}MVA" if x.get("registrertIMvaregisteret") else None,
                activity_code=n1.get("kode"), activity=n1.get("beskrivelse"), website=x.get("hjemmeside"),
                employees=x.get("antallAnsatte"), status_normalized=norm_no(x),
                status_raw={k: x.get(k) for k in ("konkurs", "konkursdato", "underAvvikling", "underAvviklingDato",
                            "underTvangsavviklingEllerTvangsopplosning", "tvangsavvikletPgaManglendeSlettingDato", "slettedato") if k in x},
                source="Brønnøysund Register Centre (data.brreg.no)",
                source_url=f"https://virksomhet.brreg.no/oppslag/enheter/{x.get('organisasjonsnummer')}")


async def no(c, q, limit):
    qd = re.sub(r"\D", "", q)
    if len(qd) == 9 and len(q.replace(" ", "")) <= 12:
        r = await _get(c, f"https://data.brreg.no/enhetsregisteret/api/enheter/{qd}")
        return [_no(r.json())] if r.status_code == 200 else []
    r = await _get(c, "https://data.brreg.no/enhetsregisteret/api/enheter", params={"navn": q, "size": min(limit, 100)})
    r.raise_for_status()
    return [_no(x) for x in r.json().get("_embedded", {}).get("enheter", [])][:limit]


# ---------- Finland ----------
def _en(descs):
    for d in descs or []:
        if d.get("languageCode") == "3": return d.get("description")
    return (descs or [{}])[0].get("description") if descs else None


def _fi(x):
    names = [n for n in x.get("names", []) if n.get("type") == "1" and not n.get("endDate")] or x.get("names", [{}])
    addr = next((a for a in x.get("addresses", []) if a.get("type") == 1), (x.get("addresses") or [{}])[0])
    pof = (addr.get("postOffices") or [{}])[0]
    street = " ".join(str(v) for v in [addr.get("street"), addr.get("buildingNumber"), addr.get("entrance"), addr.get("apartmentNumber")] if v)
    bid = (x.get("businessId") or {}).get("value")
    vat = any(e.get("type") == "80" and not e.get("endDate") for e in x.get("registeredEntries", []))
    st = x.get("tradeRegisterStatus") or x.get("status")
    situ = x.get("companySituations") or []
    mbl = x.get("mainBusinessLine") or {}
    return _rec("FI", bid, names[0].get("name"),
                legal_form=_en((x.get("companyForms") or [{}])[0].get("descriptions")),
                status=(_en(situ[0].get("descriptions")) or {"KONK": "Bankrupt", "SELTILA": "Liquidation", "SANE": "Company re-organisation"}.get(situ[0].get("type"))) if situ else ("registered" if not x.get("endDate") else "ended"),
                active=not situ and not x.get("endDate"),
                address=street or addr.get("postOfficeBox"), postal_code=addr.get("postCode"), city=pof.get("city"),
                registration_date=(x.get("businessId") or {}).get("registrationDate"),
                vat_id=("FI" + bid.replace("-", "")) if vat and bid else None,
                activity_code=mbl.get("type"), activity=_en(mbl.get("descriptions")),
                website=(x.get("website") or {}).get("url"), status_normalized=norm_fi(x),
                status_raw={"companySituations": [s.get("type") for s in situ], "open_situations": _fi_open_situations(x),
                            "reorganisation": "SANE" in _fi_open_situations(x), "endDate": x.get("endDate"),
                            "tradeRegisterStatus": st, "businessIdStatus": x.get("status")},
                source="Finnish Patent and Registration Office (PRH/YTJ)",
                source_url=f"https://tietopalvelu.ytj.fi/yritys/{bid}")


async def fi(c, q, limit):
    p = {"businessId": q.strip()} if re.fullmatch(r"\d{7}-\d", q.strip()) else {"name": q}
    r = await _get(c, "https://avoindata.prh.fi/opendata-ytj-api/v3/companies", params=p)
    if r.status_code == 404: return []
    r.raise_for_status()
    return [_fi(x) for x in r.json().get("companies", [])][:limit]


# ---------- Czechia ----------
def _cz(x):
    s = x.get("sidlo") or {}
    zan = x.get("datumZaniku")
    return _rec("CZ", x.get("ico"), x.get("obchodniJmeno"), legal_form=x.get("pravniForma"),
                status="dissolved" if zan else "active", active=not zan, address=s.get("textovaAdresa"),
                postal_code=str(s["psc"]) if s.get("psc") else None, city=s.get("nazevObce"),
                registration_date=x.get("datumVzniku"), vat_id=x.get("dic"),
                activity_code=(x.get("czNace2008") or x.get("czNace") or [None])[0], status_normalized=norm_cz(x),
                status_raw={"datumZaniku": zan, "stavZdrojeVr": (x.get("seznamRegistraci") or {}).get("stavZdrojeVr"),
                            "stavZdrojeRos": (x.get("seznamRegistraci") or {}).get("stavZdrojeRos"),
                            "stavZdrojeIr": (x.get("seznamRegistraci") or {}).get("stavZdrojeIr")},
                source="ARES – Czech Ministry of Finance (ares.gov.cz)",
                source_url=f"https://ares.gov.cz/ekonomicke-subjekty?ico={x.get('ico')}")


async def cz(c, q, limit):
    base = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty"
    qd = re.sub(r"\D", "", q)
    if qd and len(qd) <= 8 and len(q.strip()) <= 10:
        r = await _get(c, f"{base}/{qd.zfill(8)}")
        return [_cz(r.json())] if r.status_code == 200 else []
    r = await c.post(f"{base}/vyhledat", json={"obchodniJmeno": q, "pocet": min(limit, 100)})
    if r.status_code in (400, 404): return []
    r.raise_for_status()
    return [_cz(x) for x in r.json().get("ekonomickeSubjekty", [])][:limit]


# ---------- Poland (KRS number) ----------
async def pl(c, q, limit):
    krs = re.sub(r"\D", "", q).zfill(10)
    if not re.sub(r"\D", "", q):
        raise ValueError("Poland: provide a 10-digit KRS number (the official KRS API has no name search)")
    for rej in ("P", "S"):
        r = await _get(c, f"https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{krs}", params={"rejestr": rej, "format": "json"})
        if r.status_code == 200:
            o = r.json()["odpis"]; d = o["dane"]["dzial1"]; dp = d.get("danePodmiotu", {}); a = (d.get("siedzibaIAdres") or {}).get("adres", {})
            ids = dp.get("identyfikatory", {}); pst, praw = pl_flags(o)
            return [_rec("PL", krs, dp.get("nazwa"), legal_form=dp.get("formaPrawna"), status="registered", active=True,
                         address=" ".join(v for v in [a.get("ulica"), a.get("nrDomu"), a.get("nrLokalu")] if v) or None,
                         postal_code=a.get("kodPocztowy"), city=a.get("miejscowosc"),
                         registration_date=o.get("naglowekA", {}).get("dataRejestracjiWKRS"),
                         vat_id=("PL" + ids["nip"]) if ids.get("nip") else None,
                         website=(d.get("siedzibaIAdres") or {}).get("adresStronyInternetowej"),
                         source="KRS – Polish National Court Register (api-krs.ms.gov.pl)",
                         source_url=f"https://wyszukiwarka-krs.ms.gov.pl/details?krs={krs}", regon=ids.get("regon"), status_normalized=pst, status_raw=praw)]
    return []


# ---------- Estonia ----------
async def ee(c, q, limit):
    r = await _get(c, "https://ariregister.rik.ee/est/api/autocomplete", params={"q": q})
    r.raise_for_status()
    st = {"R": "registered", "L": "in liquidation", "N": "bankrupt", "K": "deleted"}
    return [_rec("EE", str(x.get("reg_code")), x.get("name"), status=st.get(x.get("status"), x.get("status")),
                 active=x.get("status") == "R", status_normalized=norm_ee(x.get("status")), status_raw=x.get("status"), address=x.get("legal_address"), postal_code=x.get("zip_code"),
                 source="Estonian e-Business Register (ariregister.rik.ee)", source_url=x.get("url"))
            for x in r.json().get("data", [])][:limit]


# ---------- GLEIF (global) ----------
def _lei(x):
    a = x["attributes"]; e = a["entity"]; la = e.get("legalAddress") or {}
    return _rec(la.get("country"), e.get("registeredAs"), (e.get("legalName") or {}).get("name"),
                legal_form=(e.get("legalForm") or {}).get("id"), status=e.get("status"), active=e.get("status") == "ACTIVE",
                address=", ".join(la.get("addressLines") or []) or None, postal_code=la.get("postalCode"), city=la.get("city"),
                registration_date=(a.get("registration") or {}).get("initialRegistrationDate"), lei=a.get("lei"), status_normalized=norm_gleif(e.get("status")),
                status_raw={"entity_status": e.get("status"), "registration_status": (a.get("registration") or {}).get("status"),
                            "expiration_reason": (e.get("expiration") or {}).get("reason")},
                source="GLEIF Global LEI Index (api.gleif.org)", source_url=f"https://search.gleif.org/#/record/{a.get('lei')}")


async def lei(c, q, limit, country=None):
    q = q.strip()
    if re.fullmatch(r"[A-Z0-9]{18}\d{2}", q.upper()):
        r = await _get(c, f"https://api.gleif.org/api/v1/lei-records/{q.upper()}")
        return [_lei(r.json()["data"])] if r.status_code == 200 else []
    p = {"filter[fulltext]": q, "page[size]": min(limit, 100)}
    if country: p["filter[entity.legalAddress.country]"] = country
    r = await _get(c, "https://api.gleif.org/api/v1/lei-records", params=p)
    r.raise_for_status()
    return [_lei(x) for x in r.json().get("data", [])][:limit]


HANDLERS = {"FR": fr, "NO": no, "FI": fi, "CZ": cz, "PL": pl, "EE": ee}


async def lookup(query: str, country: str, limit: int = 5, client=None):
    """Return {query, country, status, count, results[], error}."""
    country = (country or "").upper().strip()
    if country in ("GB", "UK", "") or country not in HANDLERS and country != "LEI":
        mode = "LEI"
    else:
        mode = country
    own = client is None
    c = client or httpx.AsyncClient(timeout=25, headers=UA, follow_redirects=True)
    try:
        if mode == "LEI":
            res = await lei(c, query, limit, country if len(country) == 2 else None)
        else:
            res = await HANDLERS[mode](c, query, limit)
        return {"query": query, "country": country or None, "status": "found" if res else "not_found",
                "count": len(res), "results": res, "error": None}
    except Exception as e:  # noqa
        return {"query": query, "country": country or None, "status": "error", "count": 0, "results": [], "error": f"{type(e).__name__}: {e}"[:300]}
    finally:
        if own: await c.aclose()


async def lookup_many(items, limit=5, concurrency=5):
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=25, headers=UA, follow_redirects=True) as c:
        async def one(it):
            async with sem:
                return await lookup(it["query"], it.get("country", ""), limit, c)
        return await asyncio.gather(*[one(i) for i in items])
