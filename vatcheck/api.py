from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from . import core, company, sanctions, kyb, __version__
from . import eori as eori_mod
from .x402 import X402Middleware

app = FastAPI(title="vatcheck — EU VAT / VIES verification API", version=__version__,
              description="Validate EU VAT numbers via VIES with consultation (proof) number, caching, retries and Hungarian enrichment.")
app.add_middleware(X402Middleware)
# CORS outermost so browser widgets (e.g. https://wsystem.dev) can call the free tier and see 402/429 bodies.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["*"], expose_headers=["X-Free-Remaining", "PAYMENT-REQUIRED", "PAYMENT-RESPONSE", "X-PAYMENT-RESPONSE"])

class Batch(BaseModel):
    vats: list[str] = Field(..., min_length=1, max_length=100)
    requester: str | None = core.DEFAULT_REQUESTER

@app.get("/health")
async def health():
    return {"ok": True, "version": __version__}

@app.get("/verify")
async def verify(vat: str = Query(..., examples=["HU33119856"]), requester: str | None = core.DEFAULT_REQUESTER,
                 nocache: bool = False):
    return await core.verify(vat, requester or None, use_cache=not nocache)

@app.post("/verify/batch")
async def verify_batch(b: Batch):
    res = await core.verify_batch(b.vats, b.requester or None)
    return {"count": len(res), "valid": sum(1 for r in res if r.get("valid")), "results": res}


class CompanyBatch(BaseModel):
    queries: list[dict] = Field(..., min_length=1, max_length=25,
                                examples=[[{"query": "552032534", "country": "FR"}, {"query": "equinor", "country": "NO"}]])
    limit: int = Field(3, ge=1, le=25)

@app.get("/company")
async def company_lookup(q: str = Query(..., examples=["552032534"]), country: str = Query("", examples=["FR"]),
                         limit: int = Query(3, ge=1, le=25)):
    """Official business-register lookup: FR, NO, FI, CZ, PL (KRS no.), EE; other countries via GLEIF LEI."""
    return await company.lookup(q, country, limit)

@app.post("/company/batch")
async def company_batch(b: CompanyBatch):
    res = await company.lookup_many([{"query": str(i.get("query", "")), "country": str(i.get("country", ""))} for i in b.queries], b.limit)
    return {"count": len(res), "found": sum(1 for r in res if r["status"] == "found"), "results": res}


class SanctionsBatch(BaseModel):
    names: list[str] = Field(..., min_length=1, max_length=50, examples=[["Sberbank", "Example Trading Ltd"]])
    threshold: float = Field(85, ge=50, le=100)

@app.get("/sanctions/screen")
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

@app.post("/sanctions/screen/batch")
async def sanctions_batch(b: SanctionsBatch):
    import asyncio
    res = await asyncio.to_thread(sanctions.screen_names, b.names, b.threshold)
    return {"count": len(res), "potential_matches": sum(r["status"] == "potential_match" for r in res), "results": res}


class KybCheck(BaseModel):
    company: str | dict = Field(..., examples=["FR40303265045", {"name": "Equinor ASA", "country": "NO"}],
        description="VAT ID ('FR40303265045'), 'CC:name or registry id' ('NO:equinor'), or {name, vatNumber, country, registryId}")
    requester: str | None = core.DEFAULT_REQUESTER
    threshold: float = Field(85, ge=50, le=100)

@app.post("/kyb/check")
async def kyb_check(b: KybCheck):
    """One-call KYB: EU VAT (VIES) + official business register + sanctions screening -> pass / review / fail verdict."""
    return await kyb.check(b.company, b.requester or None, b.threshold)


class EoriBatch(BaseModel):
    eoris: list[str] = Field(..., min_length=1, max_length=100, examples=[["SE5560360793", "IE6388047V"]])

@app.get("/eori")
async def eori_verify(eori: str = Query(..., examples=["SE5560360793"])):
    """Validate one EU EORI number via the official European Commission EOS SOAP API."""
    return await eori_mod.validate_one(eori)

@app.post("/eori/batch")
async def eori_batch(b: EoriBatch):
    """Batch-validate up to 100 EU EORI numbers via official EOS."""
    res = await eori_mod.validate_many(b.eoris)
    return {"count": len(res), "valid": sum(1 for r in res if r.get("valid")), "results": res}
