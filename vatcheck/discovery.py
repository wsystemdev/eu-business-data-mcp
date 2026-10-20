"""Machine-readable discovery docs for AI agents and indexers (server card, AI catalog, x402 manifest, llms.txt)."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from . import __version__

BASE = "https://api.wsystem.dev"
router = APIRouter(include_in_schema=False)

SERVER_CARD = {
    "$schema": "https://static.modelcontextprotocol.io/schemas/v1/server-card.schema.json",
    "name": "io.github.wsystemdev/eu-business-data-mcp",
    "title": "W System EU Business Data (VAT/VIES, EORI, company registry, sanctions, KYB)",
    "version": "1.0.3",
    "description": "EU VAT number validation (VIES), EORI validation (EC EOS), official company-registry lookup, "
                   "EU/UN/OFAC/UK sanctions screening and one-call KYB. Free 20 calls/day per IP, no API key; x402 pay-per-call beyond.",
    "websiteUrl": "https://wsystem.dev",
    "repository": {"url": "https://github.com/wsystemdev/eu-business-data-mcp", "source": "github"},
    "remotes": [{"type": "streamable-http", "url": f"{BASE}/mcp/"}],
    "_meta": {"dev.wsystem/tools": ["verify_vat", "verify_batch", "verify_eori", "verify_eori_batch",
                                    "lookup_company", "screen_sanctions", "kyb_check"],
              "dev.wsystem/maker": "W System"},
}
PAID = [("GET", "/verify", "EU VAT validation via VIES", "$0.01"), ("POST", "/verify/batch", "Batch VIES VAT validation (<=100)", "$0.10"),
        ("GET", "/eori", "EU EORI validation via EC EOS", "$0.01"), ("POST", "/eori/batch", "Batch EORI validation (<=100)", "$0.10"),
        ("GET", "/company", "EU company registry lookup", "$0.01"), ("POST", "/company/batch", "Batch company lookup (<=25)", "$0.10"),
        ("GET", "/sanctions/screen", "EU/UN/OFAC/UK sanctions screening", "$0.01"),
        ("POST", "/sanctions/screen/batch", "Batch sanctions screening (<=50)", "$0.10"),
        ("POST", "/kyb/check", "One-call KYB: VAT + registry + sanctions verdict", "$0.045")]

def _j(d, mt="application/json"):
    return JSONResponse(d, media_type=mt, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"})

@router.get("/server-card")
@router.get("/mcp/server-card")
@router.get("/.well-known/mcp/server-card")
@router.get("/.well-known/mcp/server-card.json")
@router.get("/.well-known/mcp-server-card")
@router.get("/.well-known/mcp.json")
async def server_card():
    return _j(SERVER_CARD)

@router.get("/.well-known/ai-catalog.json")
@router.get("/.well-known/mcp/catalog.json")
async def ai_catalog():
    return _j({"version": "1", "publisher": {"name": "W System", "url": "https://wsystem.dev"},
               "entries": [{"identifier": SERVER_CARD["name"], "type": "application/mcp-server-card+json", "url": f"{BASE}/server-card"},
                           {"identifier": "wsystem-openapi", "type": "application/openapi+json", "url": f"{BASE}/openapi.json"}]})

@router.get("/.well-known/x402")
async def x402_manifest():
    return _j({"version": 1, "x402Version": 2, "kind": "resource-server",
               "resources": [f"{BASE}{p}" for _, p, _, _ in PAID],
               "endpoints": [{"url": f"{BASE}{p}", "method": m, "description": d, "price": pr,
                              "network": "eip155:8453", "asset": "USDC"} for m, p, d, pr in PAID],
               "freeTier": "20 calls/day per IP without payment or API key",
               "instructions": "Unpaid calls beyond the free tier return HTTP 402 with an x402 v2 challenge (exact scheme, USDC on Base). "
                               "Docs: https://wsystem.dev/x402"})

@router.get("/llms.txt")
async def llms():
    return RedirectResponse("https://wsystem.dev/llms.txt", 302)

@router.get("/", include_in_schema=False)
async def root():
    return _j({"name": "W System EU Business Data API", "version": __version__, "docs": f"{BASE}/docs",
               "openapi": f"{BASE}/openapi.json", "mcp": f"{BASE}/mcp/", "server_card": f"{BASE}/server-card",
               "x402": f"{BASE}/.well-known/x402", "llms_txt": "https://wsystem.dev/llms.txt",
               "free_tier": "20 calls/day per IP, no API key"})
