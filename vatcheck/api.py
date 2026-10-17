from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from . import core, company, sanctions, kyb, __version__
from . import eori as eori_mod
from .x402 import X402Middleware
from .discovery import router as discovery_router

DESC = """EU business data API by W System (W System Hungary Kft., the maker): **EU VAT number validation (VIES)**,
**EORI validation (EC EOS)**, **official company-registry lookup**, **sanctions screening (EU, UN, OFAC SDN, UK)** and **one-call KYB**.

* **No API key, no signup:** every client IP gets 20 free calls/day across all endpoints (header `X-Free-Remaining`).
* **Beyond the free tier:** pay per call with **x402** (USDC on Base, HTTP 402 flow): $0.01 per single check, $0.10 per batch request, $0.045 per KYB check.
* **MCP:** the same tools are available to AI agents at `https://api.wsystem.dev/mcp/` (streamable HTTP).
* VIES/EOS are official European Commission services; this API adds retries when a member state is unavailable, caching,
  batch endpoints, audit consultation numbers and a stable JSON shape. Docs: https://wsystem.dev/llms.txt"""
TAGS = [{"name": "VAT", "description": "EU VAT number validation via VIES (27 EU states + XI Northern Ireland)"},
        {"name": "EORI", "description": "EU EORI customs number validation via the EC EOS service"},
        {"name": "Company", "description": "Official business-register lookup (FR, NO, FI, CZ, PL, EE; others via GLEIF LEI)"},
        {"name": "Sanctions", "description": "Name screening against EU, UN, OFAC SDN and UK sanctions lists (refreshed daily)"},
        {"name": "KYB", "description": "VAT + registry + sanctions in one call with a pass/review/fail verdict"}]
app = FastAPI(title="W System EU Business Data API (VAT/VIES, EORI, company registry, sanctions, KYB)", version=__version__,
              description=DESC, openapi_tags=TAGS, servers=[{"url": "https://api.wsystem.dev", "description": "Production"}],
              contact={"name": "W System", "url": "https://wsystem.dev"},
              license_info={"name": "Proprietary API; MCP server code MIT", "url": "https://github.com/zifengw/eu-business-data-mcp"})
app.add_middleware(X402Middleware)
# CORS outermost so browser widgets (e.g. https://wsystem.dev) can call the free tier and see 402/429 bodies.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["*"], expose_headers=["X-Free-Remaining", "PAYMENT-REQUIRED", "PAYMENT-RESPONSE", "X-PAYMENT-RESPONSE"])

class Batch(BaseModel):
    vats: list[str] = Field(..., min_length=1, max_length=100)
    requester: str | None = core.DEFAULT_REQUESTER

app.include_router(discovery_router)

@app.get("/health", summary="Liveness check", operation_id="health")
async def health():
    return {"ok": True, "version": __version__}

@app.get("/verify", tags=["VAT"], operation_id="verifyVat", summary='Validate one EU VAT number (VIES)', description='Check whether an EU VAT number (with country prefix, e.g. IE6388047V, DE811193231, HU33119856) is valid in the official VIES system. Returns `status` (valid | invalid | invalid_format | member_state_unavailable | error), `valid`, registered `name` and `address` where the member state discloses them, and a `consultation_number` (audit proof) when `requester` is a valid EU VAT. Use for reverse-charge invoicing, customer/supplier onboarding and checkout VAT exemption. Free 20/day per IP, then x402 $0.01.')
async def verify(vat: str = Query(..., examples=["HU33119856"]), requester: str | None = core.DEFAULT_REQUESTER,
                 nocache: bool = False):
    return await core.verify(vat, requester or None, use_cache=not nocache)

@app.post("/verify/batch", tags=["VAT"], operation_id="verifyVatBatch", summary='Validate up to 100 EU VAT numbers', description='Batch VIES validation. Body: {"vats": [...], "requester": "optional own VAT"}. Same per-item result shape as GET /verify. x402 $0.10 per batch request after the free tier.')
async def verify_batch(b: Batch):
    res = await core.verify_batch(b.vats, b.requester or None)
    return {"count": len(res), "valid": sum(1 for r in res if r.get("valid")), "results": res}


class CompanyBatch(BaseModel):
    queries: list[dict] = Field(..., min_length=1, max_length=25,
                                examples=[[{"query": "552032534", "country": "FR"}, {"query": "equinor", "country": "NO"}]])
    limit: int = Field(3, ge=1, le=25)

@app.get("/company", tags=["Company"], operation_id="lookupCompany", summary='Look up a company in official EU business registers', description='Search by company name or registry number. `country`: FR (SIREN/SIRET), NO (org.nr), FI (Y-tunnus), CZ (IČO), PL (KRS number), EE (registry code); any other ISO code or LEI uses GLEIF. Returns legal name, registry ID, active status, address, VAT ID, activity code and an official source link. x402 $0.01.')
async def company_lookup(q: str = Query(..., examples=["552032534"]), country: str = Query("", examples=["FR"]),
                         limit: int = Query(3, ge=1, le=25)):
    """Official business-register lookup: FR, NO, FI, CZ, PL (KRS no.), EE; other countries via GLEIF LEI."""
    return await company.lookup(q, country, limit)

@app.post("/company/batch", tags=["Company"], operation_id="lookupCompanyBatch", summary='Batch company-registry lookup (max 25 queries)', description='Body: {"queries":[{"query":"552032534","country":"FR"}], "limit":3}. x402 $0.10 per batch request.')
async def company_batch(b: CompanyBatch):
    res = await company.lookup_many([{"query": str(i.get("query", "")), "country": str(i.get("country", ""))} for i in b.queries], b.limit)
    return {"count": len(res), "found": sum(1 for r in res if r["status"] == "found"), "results": res}


class SanctionsBatch(BaseModel):
    names: list[str] = Field(..., min_length=1, max_length=50, examples=[["Sberbank", "Example Trading Ltd"]])
    threshold: float = Field(85, ge=50, le=100)

@app.get("/sanctions/screen", tags=["Sanctions"], operation_id="screenSanctions", summary='Screen a name against EU, UN, OFAC and UK sanctions lists', description='Fuzzy, transliteration-aware matching of a person or company name against the official EU consolidated list, UN Security Council list, US OFAC SDN and UK sanctions list (downloaded daily). Returns status (potential_match | no_match), scored candidates (0-100), list, programme and DOB. Screening aid only; potential matches need human review. x402 $0.01.')
async def sanctions_screen(name: str = Query(..., examples=["Sberbank"]), threshold: float = Query(85, ge=50, le=100),
                           birth_year: int | None = None, country: str | None = None, lists: str | None = Query(None, examples=["EU,UN,OFAC,UK"])):
    """Fuzzy-screen one name against official EU, UN, OFAC SDN and UK sanctions lists (refreshed daily)."""
    import asyncio
    s = await asyncio.to_thread(sanctions.get_screener)
    m = s.screen(name, threshold, birth_year=birth_year, country=country,
                 lists_=[x.strip().upper() for x in lists.split(",")] if lists else None)
    return {"query": name, "status": "potential_match" if m else "no_match", "top_score": m[0]["score"] if m else 0,
            "matches": m, "lists": s.index["lists"], "lists_date": s.index["built_at"],
            "disclaimer": "Screening aid only; potential matches require human review."}

@app.post("/sanctions/screen/batch", tags=["Sanctions"], operation_id="screenSanctionsBatch", summary='Batch sanctions screening (max 50 names)', description='Body: {"names":[...], "threshold":85}. x402 $0.10.')
async def sanctions_batch(b: SanctionsBatch):
    import asyncio
    res = await asyncio.to_thread(sanctions.screen_names, b.names, b.threshold)
    return {"count": len(res), "potential_matches": sum(r["status"] == "potential_match" for r in res), "results": res}


class KybCheck(BaseModel):
    company: str | dict = Field(..., examples=["FR40303265045", {"name": "Equinor ASA", "country": "NO"}],
        description="VAT ID ('FR40303265045'), 'CC:name or registry id' ('NO:equinor'), or {name, vatNumber, country, registryId}")
    requester: str | None = core.DEFAULT_REQUESTER
    threshold: float = Field(85, ge=50, le=100)

@app.post("/kyb/check", tags=["KYB"], operation_id="kybCheck", summary='One-call KYB: VAT + registry + sanctions -> verdict', description='Know-your-business check of an EU company in one request: VIES VAT validation, official business-register lookup and EU/UN/OFAC/UK sanctions screening, combined into a pass / review / fail verdict with reasons. x402 $0.045.')
async def kyb_check(b: KybCheck):
    """One-call KYB: EU VAT (VIES) + official business register + sanctions screening -> pass / review / fail verdict."""
    return await kyb.check(b.company, b.requester or None, b.threshold)


class EoriBatch(BaseModel):
    eoris: list[str] = Field(..., min_length=1, max_length=100, examples=[["SE5560360793", "IE6388047V"]])

@app.get("/eori", tags=["EORI"], operation_id="verifyEori", summary='Validate one EU EORI number (EC EOS)', description='Check an EU EORI (Economic Operators Registration and Identification) number such as SE5560360793 against the official European Commission EOS service. Returns valid/invalid and name/address when the trader consented to publication. Note: GB EORIs are validated by HMRC, not EOS. x402 $0.01.')
async def eori_verify(eori: str = Query(..., examples=["SE5560360793"])):
    """Validate one EU EORI number via the official European Commission EOS SOAP API."""
    return await eori_mod.validate_one(eori)

@app.post("/eori/batch", tags=["EORI"], operation_id="verifyEoriBatch", summary='Validate up to 100 EU EORI numbers', description='Body: {"eoris":[...]}. x402 $0.10.')
async def eori_batch(b: EoriBatch):
    """Batch-validate up to 100 EU EORI numbers via official EOS."""
    res = await eori_mod.validate_many(b.eoris)
    return {"count": len(res), "valid": sum(1 for r in res if r.get("valid")), "results": res}


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    from .discovery import PAID
    s = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes,
                    tags=app.openapi_tags, servers=app.servers, contact=app.contact, license_info=app.license_info)
    s["externalDocs"] = {"description": "Guides, prices and MCP setup", "url": "https://wsystem.dev/llms-full.txt"}
    s["x-discovery"] = {"mcp": "https://api.wsystem.dev/mcp/", "serverCard": "https://api.wsystem.dev/server-card",
                        "x402": "https://api.wsystem.dev/.well-known/x402"}
    for m, p, _, price in PAID:
        op = s["paths"].get(p, {}).get(m.lower())
        if op is not None:
            op["x-payment-info"] = {"protocols": ["x402"], "price": price, "currency": "USD", "asset": "USDC",
                                    "network": "eip155:8453", "freeTier": "20 calls/day per IP, no key"}
            op.setdefault("responses", {})["402"] = {"description": "Free tier used up: x402 payment required (challenge in PAYMENT-REQUIRED header)"}
            op["responses"]["429"] = {"description": "Free tier used up and x402 disabled"}
    app.openapi_schema = s
    return s

app.openapi = _custom_openapi
