"""MCP wrapper. Run: python -m vatcheck.mcp_server [stdio|http]  (http -> streamable HTTP on :8001/mcp)"""
import os, sys
from mcp.server.fastmcp import FastMCP
from . import core, company, sanctions, kyb
from . import eori as eori_mod  # alias: the verify_eori tool's public argument is also called 'eori'

mcp = FastMCP("eu-business-data", instructions=(
    "W System EU business data tools (maker: W System). Single checks: verify_vat (EU VAT number, VIES), verify_eori "
    "(EU customs EORI), lookup_company (official business register), screen_sanctions (EU/UN/OFAC/UK lists); lists: "
    "verify_vat_batch, verify_eori_batch, lookup_company_batch, screen_sanctions_batch. Combined counterparty checks: "
    "check_kyb = quick onboarding verdict (VAT + register + sanctions -> pass/review/fail); get_due_diligence_report = "
    "fuller audit report (adds EORI, GLEIF LEI, Peppol, risk score and flags). E-invoicing: get_einvoice_route = can this buyer "
    "receive e-invoices and in which format (no invoice needed); create_einvoice / validate_einvoice for EN 16931 and "
    "XRechnung XML. Old names (verify_batch, kyb_check, einvoice_route, due_diligence_report) still work as aliases. "
    "No API key needed; 20 free calls/day per IP."), host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("MCP_PORT", 8001)))

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
    """Check ONE EU VAT number against the official European Commission VIES service (27 EU states + XI Northern Ireland).
    Use when: asked whether a VAT ID is valid/active, before a zero-rated reverse-charge B2B invoice, or to get the
    registered name/address behind a VAT number. Not for: several numbers (use verify_vat_batch), GB numbers (HMRC, not VIES),
    a full supplier check (use check_kyb).
    Input: vat with country prefix, e.g. 'IE6388047V', 'DE811193231' (spaces/dots ok); requester = your own EU VAT number
    (optional) to get a VIES consultation number as audit proof.
    Output: {vat, valid, status: valid | invalid | invalid_format | member_state_unavailable (retry later, valid=null) | error,
    name, address (when the member state discloses them; DE/ES often don't), request_date, consultation_number, source}."""
    _guard()
    return await core.verify(vat, requester)

@mcp.tool()
async def verify_vat_batch(vats: list[str], requester: str | None = core.DEFAULT_REQUESTER) -> list[dict]:
    """Check up to 100 EU VAT numbers in one call via VIES (supplier/customer list cleanup, onboarding, invoice runs).
    Use when: you have a list of VAT numbers. For one number use verify_vat.
    Input: vats = list of VAT numbers with country prefix (items after 100 are ignored); requester optional (audit proof).
    Output: list in input order, one verify_vat result per number; retry items with status member_state_unavailable later.
    (Old name: verify_batch, still accepted.)"""
    _guard()
    return await core.verify_batch(vats[:100], requester)

@mcp.tool()
async def lookup_company(query: str, country: str = "", limit: int = 3) -> dict:
    """Find ONE company in an official European business register by name or registry number.
    Use when: confirming a company exists and is active, finding its registry number, legal form or address, or mapping a
    name to a VAT ID. Not for: sanctions (screen_sanctions) or a pass/fail verdict (check_kyb). Several companies: lookup_company_batch.
    Input: query = company name or registry number; country = FR (SIREN), NO (org.nr), FI (Y-tunnus), CZ (ICO), PL (KRS number),
    EE, or any other ISO code / 'LEI' (searched in the GLEIF LEI index); limit = max matches (default 3, max 25).
    Output: {query, country, status: found | not_found | error, count, results: [{name, registry_id, status, active, address,
    vat_id, activity_code, source, source_url}]}."""
    _guard()
    return await company.lookup(query, country, min(limit, 25))

@mcp.tool()
async def lookup_company_batch(queries: list[dict], limit: int = 1) -> dict:
    """Look up up to 25 companies in official European business registers in one call (same search as lookup_company).
    Use when: you have a list of company names or registry numbers to resolve (vendor master data, CRM import).
    Input: queries = [{"query": "552032534", "country": "FR"}, {"query": "Equinor", "country": "NO"}] (max 25; items after 25
    are ignored); limit = matches per query (default 1, max 5).
    Output: {count, found, results: [one lookup_company answer per query, in input order]}."""
    _guard()
    items = [{"query": str((q or {}).get("query", "")), "country": str((q or {}).get("country", ""))} for q in queries[:25]]
    res = await company.lookup_many(items, max(1, min(limit, 5)))
    return {"count": len(res), "found": sum(1 for r in res if r.get("status") == "found"), "results": res}

@mcp.tool()
async def screen_sanctions(name: str, threshold: float = 85, birth_year: int | None = None, country: str | None = None) -> dict:
    """Screen ONE person or company name against the official EU, UN, US OFAC SDN and UK sanctions lists (refreshed daily).
    Use when: a sanctions/PEP-style name screen is needed on its own. Several names: screen_sanctions_batch. With VAT and
    register checks in one verdict: check_kyb.
    Input: name; threshold = minimum fuzzy score 0-100 (default 85, lower finds more); birth_year and country (ISO) narrow matches.
    Output: {query, status: potential_match | no_match, top_score, match_count, matches: [{score, matched_name, sanctioned_name, list, programs, dob, nationality, listed_on}], lists_checked}.
    A potential match needs human review; this is a screening aid, not legal advice."""
    _guard()
    import asyncio
    r = await asyncio.to_thread(sanctions.screen_names, [name], threshold, birth_year=birth_year, country=country)
    return r[0]

@mcp.tool()
async def screen_sanctions_batch(names: list[str], threshold: float = 85) -> dict:
    """Screen up to 50 person or company names against the EU, UN, US OFAC SDN and UK sanctions lists in one call.
    Use when: screening a customer, supplier or payee list. One name with birth year/country filters: screen_sanctions.
    Input: names (max 50; items after 50 are ignored); threshold = minimum fuzzy score 0-100 (default 85).
    Output: {count, potential_matches, results: [one screen_sanctions result per name, in input order]}."""
    _guard()
    import asyncio
    r = await asyncio.to_thread(sanctions.screen_names, [str(n) for n in names[:50]], threshold)
    return {"count": len(r), "potential_matches": sum(1 for x in r if x.get("status") == "potential_match"), "results": r}

@mcp.tool()
async def check_kyb(company: str, country: str = "", requester: str | None = core.DEFAULT_REQUESTER) -> dict:
    """Quick KYB onboarding verdict for one supplier or customer: EU VAT (VIES) + official business register + EU/UN/OFAC/UK
    sanctions screening, combined into pass / review / fail with reasons.
    Use when: you need a go/no-go answer fast (onboarding, before the first payment). Choose get_due_diligence_report instead
    when you need an audit-ready report that also covers EORI, GLEIF LEI, Peppol e-invoicing and a 0-100 risk score.
    Input: company = VAT ID (best, e.g. 'FR40303265045'), company name or registry number; country = ISO code for names;
    requester optional (VIES consultation number).
    Output: {verdict: pass | review | fail, reasons, vat, registry, sanctions}. (Old name: kyb_check, still accepted.)"""
    _guard()
    item = f"{country.upper()}:{company}" if country else company
    return await kyb.check(item, requester)

@mcp.tool()
async def verify_eori(eori: str) -> dict:
    """Check ONE EU EORI number (customs Economic Operators Registration and Identification) against the official European
    Commission EOS service. Use for import/export, customs declarations and freight-forwarder onboarding; several numbers:
    verify_eori_batch. GB EORIs are not in EOS (HMRC checks those); XI EORIs are.
    Input: eori with country prefix, e.g. 'SE5560360793'.
    Output: {eori, valid, status, name, address (only when the trader consented to publication)}."""
    _guard()
    return await eori_mod.validate_one(eori)

@mcp.tool()
async def verify_eori_batch(eoris: list[str]) -> list[dict]:
    """Check up to 100 EU EORI numbers in one call via the official EC EOS service (customs / import-export KYB).
    Input: eoris with country prefix (items after 100 are ignored). Output: list in input order, one verify_eori result per number."""
    _guard()
    return await eori_mod.validate_many(eoris[:100])



@mcp.tool()
async def validate_einvoice(xml: str, rules: str = "auto") -> dict:
    """Validate an EU e-invoice XML you already have (UBL 2.1 Invoice/CreditNote or UN/CEFACT CII, e.g. XRechnung,
    Factur-X/ZUGFeRD XML) against the official CEN EN 16931 rules and, for XRechnung, the KoSIT XRechnung 3.0 rules.
    Use when: checking an invoice before sending it or after receiving it. To build a new invoice from data use create_einvoice.
    Input: xml as text; rules = auto | en16931 | xrechnung. Peppol-specific rules are not checked yet; for PDFs use the REST
    endpoint POST /einvoice/validate.
    Output: {valid, syntax, errors: [{rule_id (e.g. BR-CO-10), severity, message, location (XPath), fix}], warnings, checked}."""
    _guard()
    import asyncio
    from . import einvoice
    try:
        return await asyncio.to_thread(einvoice.validate, xml.encode("utf-8"), rules)
    except einvoice.InputError as e:
        return {"error": "bad_input", "detail": str(e)}


@mcp.tool()
async def create_einvoice(invoice: dict) -> dict:
    """Create a validated EU e-invoice (UBL 2.1) from simple JSON. Use get_einvoice_route first if you do not know which
    format the buyer needs; use validate_einvoice for XML you already have.
    Input: invoice = {format: en16931|xrechnung|peppol-bis3, invoice_number, issue_date (YYYY-MM-DD), due_date?, currency (EUR),
    buyer_reference?, seller: {name, vat_id, street, city, postal_code, country, email, phone, contact_name}, buyer: {...},
    lines: [{description, quantity, unit_price, unit?, vat_category (S|Z|E|AE|K|G|O), vat_rate}], payment: {iban, bic?, terms?}}.
    Output: {valid, xml, totals, errors}; totals and the VAT breakdown are computed."""
    _guard()
    import asyncio
    from . import einvoice
    try:
        return await asyncio.to_thread(einvoice.create, invoice)
    except einvoice.InputError as e:
        return {"error": "bad_input", "detail": str(e)}


@mcp.tool()
async def get_einvoice_route(id: str) -> dict:
    """Answer "can I e-invoice this company, and in which format?" for one buyer, before any invoice exists.
    Use when: deciding how to send an invoice to a buyer. Not a KYB or risk check (use check_kyb / get_due_diligence_report).
    Input: id = buyer's EU VAT number (e.g. 'BE0403170701') or Peppol participant id ('0208:0403170701').
    Output: {vat (VIES status), peppol (registered, Directory details, BIS 3 invoice support), country, mandate (the country's B2B
    e-invoice rules: dates, format, source), recommendation {format (feed into create_einvoice), channel}, sources}. France's PPF directory is not included yet.
    (Old name: einvoice_route, still accepted.)"""
    _guard()
    from . import einvoice_route as er
    try:
        return await er.route(id)
    except ValueError as e:
        return {"error": "bad_input", "detail": str(e)}


@mcp.tool()
async def get_due_diligence_report(company: str, country: str = "") -> dict:
    """Full counterparty due-diligence report for one company, for an audit file or a risk review: VIES VAT, official
    business register, EU/UN/OFAC/UK sanctions, EC EORI, GLEIF LEI and Peppol e-invoice route, with deterministic risk
    flags, a 0-100 risk score, a pass / review / fail verdict, every source and a UTC timestamp.
    Use when: you need the evidence, not just the verdict. For a fast onboarding go/no-go use check_kyb (fewer sources, faster).
    Input: company = VAT ID (best), registry number or name; country = ISO code for names.
    Output: {company, verdict, risk_score, flags, checks: {vat, registry, sanctions, eori, lei, einvoice}, sources, generated_at}.
    (Old name: due_diligence_report, still accepted.)"""
    _guard()
    from . import ddreport
    return await ddreport.report(company, country)


# Renamed in 1.1.0 to consistent verb_noun names. The old names keep working for existing clients (tools/call resolves
# them) but are not listed in tools/list, so agents see one name per tool.
TOOL_ALIASES = {"verify_batch": "verify_vat_batch", "kyb_check": "check_kyb", "einvoice_route": "get_einvoice_route",
                "due_diligence_report": "get_due_diligence_report"}


def _install_aliases() -> None:
    tm = mcp._tool_manager
    orig = tm.get_tool

    def get_tool(name):
        return orig(name) or orig(TOOL_ALIASES.get(name, name))
    tm.get_tool = get_tool


_install_aliases()


# --- Tool titles, MCP annotations and per-argument descriptions (B195b/B198, 2026-11-10) -------------------------------
# Directories (mcptry, Glama, PulseMCP) and clients (Claude connectors) read these: readOnlyHint lets clients run lookups
# without a write-confirmation, and argument descriptions help agents fill inputs. Applied after registration so the tool
# functions above stay unchanged. test_mcp_metadata.py fails if a tool or an argument is missing here.
from mcp.types import ToolAnnotations as _TA

_TOOL_META = {
    # name: (title, read_only, idempotent, open_world, {arg: description})
    "verify_vat": ("Verify EU VAT number (VIES)", True, True, True, {
        "vat": "VAT number with country prefix, e.g. 'IE6388047V' or 'DE811193231'; spaces and dots are ignored.",
        "requester": "Optional: your own EU VAT number, so VIES returns a consultation number as audit proof."}),
    "verify_vat_batch": ("Verify up to 100 EU VAT numbers", True, True, True, {
        "vats": "List of up to 100 VAT numbers with country prefix; extra items are ignored.",
        "requester": "Optional: your own EU VAT number, so VIES returns consultation numbers as audit proof."}),
    "lookup_company": ("Look up a company in EU business registers", True, True, True, {
        "query": "Company name or official registry number (e.g. a French SIREN, Polish KRS or Czech ICO).",
        "country": "Register to search: FR, NO, FI, CZ, PL (KRS), EE, another ISO country code, or 'LEI' for GLEIF.",
        "limit": "Maximum number of matches to return (default 3)."}),
    "lookup_company_batch": ("Look up up to 25 companies in EU business registers", True, True, True, {
        "queries": "List of up to 25 objects {query, country}: company name or registry number plus the register country (see lookup_company).",
        "limit": "Matches to return per query (default 1, max 5)."}),
    "screen_sanctions_batch": ("Screen up to 50 names against EU/UN/OFAC/UK sanctions", True, True, False, {
        "names": "List of up to 50 person or company names; extra items are ignored.",
        "threshold": "Minimum fuzzy match score from 0 to 100 (default 85); lower finds more possible matches."}),
    "screen_sanctions": ("Screen a name against EU/UN/OFAC/UK sanctions", True, True, False, {
        "name": "Person or company name to screen.",
        "threshold": "Minimum fuzzy match score from 0 to 100 (default 85); lower finds more possible matches.",
        "birth_year": "Optional birth year of a person, to narrow matches.",
        "country": "Optional ISO two-letter country code, to narrow matches."}),
    "check_kyb": ("Quick KYB verdict: VAT, register and sanctions", True, True, True, {
        "company": "Company VAT number (best) or company name.",
        "country": "Optional ISO two-letter country code when a name is given.",
        "requester": "Optional: your own EU VAT number, so VIES returns a consultation number as audit proof."}),
    "verify_eori": ("Verify EU EORI number (EC EOS)", True, True, True, {
        "eori": "EORI number with country prefix, e.g. 'SE5560360793'."}),
    "verify_eori_batch": ("Verify up to 100 EU EORI numbers", True, True, True, {
        "eoris": "List of up to 100 EORI numbers with country prefix."}),
    "validate_einvoice": ("Validate an e-invoice (EN 16931, XRechnung, Peppol BIS)", True, True, False, {
        "xml": "The e-invoice XML as text: UBL 2.1 Invoice/CreditNote or UN/CEFACT CII (XRechnung, Factur-X/ZUGFeRD XML).",
        "rules": "Rule set: 'auto' (detect from the invoice, default), 'en16931' or 'xrechnung'.",
        "autofix": "If true, apply the safe fixes to a copy and return the fixed XML in autofix.xml (the input is never changed).",
        "allow_amount_changes": "If true together with autofix, also apply fixes that change amounts (e.g. recomputed totals)."}),
    "create_einvoice": ("Create a validated e-invoice (UBL 2.1)", True, True, False, {
        "invoice": "Invoice as simple JSON: format, invoice_number, issue_date, currency, seller, buyer, lines, payment (see the tool description).",
        "autofix": "If true, first apply safe input fixes (date formats, codes, VAT ID format, misnamed fields); the corrected input is in autofix.invoice."}),
    "get_einvoice_route": ("Can this company receive e-invoices? (Peppol + mandate)", True, True, True, {
        "id": "Buyer's EU VAT number (e.g. 'BE0403170701') or Peppol participant id (e.g. '0208:0403170701')."}),
    "get_due_diligence_report": ("Full counterparty due-diligence report", True, True, True, {
        "company": "Company VAT number (best) or company name.",
        "country": "Optional ISO two-letter country code when a name is given."}),
}


def _apply_tool_meta() -> None:
    for t in mcp._tool_manager.list_tools():
        meta = _TOOL_META.get(t.name)
        if not meta:
            continue
        title, ro, idem, ow, args = meta
        t.title = title
        t.annotations = _TA(title=title, readOnlyHint=ro, destructiveHint=False, idempotentHint=idem, openWorldHint=ow)
        for arg, desc in args.items():
            prop = t.parameters.get("properties", {}).get(arg)
            if prop is not None and "description" not in prop:
                prop["description"] = desc


_apply_tool_meta()

# Must stay last: mcp.run() blocks, so tools defined below it were missing in stdio/http mode.
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    mcp.run(transport="streamable-http" if mode == "http" else "stdio")
