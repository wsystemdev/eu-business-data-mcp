"""MCP wrapper. Run: python -m vatcheck.mcp_server [stdio|http]  (http -> streamable HTTP on :8001/mcp)"""
import os, sys
from mcp.server.fastmcp import FastMCP
from . import core, company, sanctions, kyb, eori

mcp = FastMCP("eu-business-data", instructions=(
    "W System EU business data tools (maker: W System Hungary Kft.). Use verify_vat for EU VAT numbers (VIES), "
    "verify_eori for EU customs EORI numbers, lookup_company for official business-register data, screen_sanctions for "
    "EU/UN/OFAC/UK sanctions lists, kyb_check to combine all three into a verdict. No API key needed; 20 free calls/day per IP."), host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("MCP_PORT", 8001)))

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
    """Validate an EU VAT number against the official European Commission VIES service (27 EU states + XI Northern Ireland).
    Use when asked whether a VAT ID is valid/active, before zero-rated reverse-charge B2B invoices, or to get a company's
    registered name/address from its VAT number. Input: VAT with country prefix, e.g. 'IE6388047V', 'DE811193231' (spaces/dots ok).
    Returns status: valid | invalid | invalid_format | member_state_unavailable (VIES/member state down - retry later, valid=null) | error,
    plus name, address (if the member state discloses them; DE/ES often don't) and consultation_number (audit proof) when requester is set.
    For GB VAT numbers use HMRC instead; for >1 number use verify_batch."""
    _guard()
    return await core.verify(vat, requester)

@mcp.tool()
async def verify_batch(vats: list[str], requester: str | None = core.DEFAULT_REQUESTER) -> list[dict]:
    """Validate up to 100 EU VAT numbers in one call via VIES (supplier/customer list cleanup, onboarding, invoice runs).
    Same per-item result as verify_vat; items with member_state_unavailable should be retried later."""
    _guard()
    return await core.verify_batch(vats[:100], requester)

@mcp.tool()
async def lookup_company(query: str, country: str = "", limit: int = 3) -> dict:
    """Look up a company in official European business registers by name or registry number.
    country: FR (SIREN), NO (org.nr), FI (Y-tunnus), CZ (ICO), PL (KRS number), EE, or any other ISO code / 'LEI' (GLEIF).
    Returns legal name, ID, status/active, address, VAT ID, activity code and official source link.
    Use to confirm a company exists and is active, find its registry number, or map a name to a VAT ID."""
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
    """Validate an EU EORI (customs Economic Operators Registration and Identification) number, e.g. 'SE5560360793',
    'DE1234567890123', against the official European Commission EOS service. Use for import/export, customs declarations
    and freight-forwarder onboarding. Returns valid/invalid and trader name/address only when the trader consented to publication.
    UK (GB) EORIs are not in EOS (HMRC checks those); XI EORIs are."""
    _guard()
    return await eori.validate_one(eori)

@mcp.tool()
async def verify_eori_batch(eoris: list[str]) -> list[dict]:
    """Validate up to 100 EU EORI numbers in one call via the official EC EOS service (customs / import-export KYB)."""
    _guard()
    return await eori.validate_many(eoris[:100])

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    mcp.run(transport="streamable-http" if mode == "http" else "stdio")
