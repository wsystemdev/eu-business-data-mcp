"""MCP wrapper. Run: python -m vatcheck.mcp_server [stdio|http]  (http -> streamable HTTP on :8001/mcp)"""
import os, sys
from mcp.server.fastmcp import FastMCP
from . import core, company, sanctions, kyb, eori

mcp = FastMCP("eu-vat-checker", host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("MCP_PORT", 8001)))

PUBLIC_URL = os.getenv("PUBLIC_URL", "https://api.wsystem.dev")
MCP_FREE_PER_DAY = int(os.getenv("MCP_FREE_PER_DAY", 20))


def _guard() -> None:
    """Daily per-IP free cap for MCP over HTTP (stdio/local use is unlimited). Raises -> MCP tool error."""
    try:
        req = mcp.get_context().request_context.request
    except Exception:
        req = None
    if req is None:
        return
    from . import x402
    ip = x402._client_ip(req)
    if x402.take_free("mcp:" + ip, MCP_FREE_PER_DAY) is None:
        raise ValueError(
            f"Free MCP quota exhausted ({MCP_FREE_PER_DAY} calls/day per IP). For more volume use the paid REST API "
            f"(x402, $0.01 USDC on Base per call): GET {PUBLIC_URL}/verify?vat=..., POST {PUBLIC_URL}/verify/batch, "
            f"GET {PUBLIC_URL}/company?q=... - or the Apify Actor https://apify.com/wsystem/eu-vat-checker "
            f"(also via https://mcp.apify.com?tools=wsystem/eu-vat-checker,wsystem/eu-eori-checker).")

@mcp.tool()
async def verify_vat(vat: str, requester: str | None = core.DEFAULT_REQUESTER) -> dict:
    """Verify one EU VAT number (e.g. 'HU33119856', 'DE811193231') against official VIES.
    Returns valid/invalid, registered name and address, and a consultation number (audit proof)
    when a requester VAT is given. Hungarian numbers get extra enrichment (legal form, checksum)."""
    _guard()
    return await core.verify(vat, requester)

@mcp.tool()
async def verify_batch(vats: list[str], requester: str | None = core.DEFAULT_REQUESTER) -> list[dict]:
    """Verify up to 100 EU VAT numbers at once (supplier/customer onboarding, KYB, invoice checks)."""
    _guard()
    return await core.verify_batch(vats[:100], requester)

@mcp.tool()
async def lookup_company(query: str, country: str = "", limit: int = 3) -> dict:
    """Look up a company in official European business registers by name or registry number.
    country: FR (SIREN), NO (org.nr), FI (Y-tunnus), CZ (ICO), PL (KRS number), EE, or any other ISO code / 'LEI' (GLEIF).
    Returns legal name, ID, status/active, address, VAT ID, activity code and official source link."""
    _guard()
    return await company.lookup(query, country, min(limit, 25))

@mcp.tool()
async def screen_sanctions(name: str, threshold: float = 85, birth_year: int | None = None, country: str | None = None) -> dict:
    """Screen a person or company name against official EU, UN, OFAC SDN and UK sanctions lists.
    Fuzzy matching with transliteration; returns candidate matches with 0-100 scores, list, programme, DOB.
    A potential match needs human review; this is a screening aid, not legal advice."""
    _guard()
    import asyncio
    r = await asyncio.to_thread(sanctions.screen_names, [name], threshold, birth_year=birth_year, country=country)
    return r[0]

@mcp.tool()
async def kyb_check(company: str, country: str = "", requester: str | None = core.DEFAULT_REQUESTER) -> dict:
    """One-call KYB check of a supplier/customer: EU VAT validation (VIES) + official business register
    + EU/UN/OFAC/UK sanctions screening, returning a pass / review / fail verdict with reasons.
    company: a VAT ID ('FR40303265045'), a company name, or a registry id; country: optional ISO code (e.g. 'NO')."""
    _guard()
    item = f"{country.upper()}:{company}" if country else company
    return await kyb.check(item, requester)

@mcp.tool()
async def verify_eori(eori: str) -> dict:
    """Verify one EU EORI (Economic Operators Registration and Identification) number, e.g. 'SE5560360793'.
    Uses the official European Commission EOS API. Returns valid/invalid and name/address when disclosed."""
    _guard()
    return await eori.validate_one(eori)

@mcp.tool()
async def verify_eori_batch(eoris: list[str]) -> list[dict]:
    """Verify up to 100 EU EORI numbers at once via official EOS (customs / import-export KYB)."""
    _guard()
    return await eori.validate_many(eoris[:100])

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    mcp.run(transport="streamable-http" if mode == "http" else "stdio")
