"""Free tier + x402 payments (official `x402` library).

Flow for paid paths (/verify*, /company*, /sanctions*, /eori*, /kyb*):
  1. Each client IP gets FREE_PER_DAY free checks (default 20/day, sqlite-counted).
  2. Beyond that: if x402 is enabled (X402_ENABLED=1 and X402_PAY_TO set), the official x402
     middleware returns HTTP 402 and verifies/settles payments through the facilitator.
     If x402 is not enabled, returns 429 (quota exhausted).
Env:
  X402_ENABLED=0|1, X402_PAY_TO=0x... (payee wallet), X402_PRICE=$0.01,
  X402_NETWORK=eip155:8453 (default, Base mainnet USDC) | eip155:84532 (Base Sepolia testnet)
  X402_FACILITATOR_URL=https://facilitator.payai.network (default, PayAI; keyless) | https://x402.org/facilitator (testnet)
  X402_PAY_TO default 0xfbbe4924a9d36e1de2b90cf4f8ee6e03f978714c, FREE_PER_DAY=20
  X402_PRICE_SANCTIONS=$0.01 (GET /sanctions/screen), X402_PRICE_SANCTIONS_BATCH=$0.10 (POST /sanctions/screen/batch)
"""
import os, sqlite3, time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from . import core

PAY_TO = os.getenv("X402_PAY_TO", "0xfbbe4924a9d36e1de2b90cf4f8ee6e03f978714c")
ENABLED = os.getenv("X402_ENABLED", "0") == "1" and PAY_TO.startswith("0x")
PRICE = os.getenv("X402_PRICE", "$0.01")
NETWORK = os.getenv("X402_NETWORK", "eip155:8453")
FACILITATOR = os.getenv("X402_FACILITATOR_URL", "https://facilitator.payai.network")
FREE_PER_DAY = int(os.getenv("FREE_PER_DAY", 20))
PRICE_COMPANY = os.getenv("X402_PRICE_COMPANY", "$0.01")
PRICE_COMPANY_BATCH = os.getenv("X402_PRICE_COMPANY_BATCH", "$0.10")
PRICE_SANCTIONS = os.getenv("X402_PRICE_SANCTIONS", "$0.01")
PRICE_SANCTIONS_BATCH = os.getenv("X402_PRICE_SANCTIONS_BATCH", "$0.10")
PRICE_EORI = os.getenv("X402_PRICE_EORI", "$0.01")
PRICE_EORI_BATCH = os.getenv("X402_PRICE_EORI_BATCH", "$0.10")
PAID_PREFIXES = ("/verify", "/company", "/sanctions", "/kyb", "/eori")


def _client_ip(request) -> str:
    return (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "?"))


def take_free(ip: str, limit: int | None = None) -> int | None:
    """Consume one free call; return remaining or None if exhausted."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with sqlite3.connect(core.CACHE_PATH) as c:
        c.execute("CREATE TABLE IF NOT EXISTS quota (ip TEXT, day TEXT, n INT, PRIMARY KEY (ip, day))")
        row = c.execute("SELECT n FROM quota WHERE ip=? AND day=?", (ip, day)).fetchone()
        n = row[0] if row else 0
        lim = FREE_PER_DAY if limit is None else limit
        if n >= lim:
            return None
        c.execute("INSERT OR REPLACE INTO quota VALUES (?,?,?)", (ip, day, n + 1))
        return lim - n - 1


def _build_x402():
    from x402 import x402ResourceServer
    from x402.http import HTTPFacilitatorClient
    from x402.http.middleware.fastapi import payment_middleware
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    server = x402ResourceServer(HTTPFacilitatorClient({"url": FACILITATOR}))
    server.register(NETWORK, ExactEvmServerScheme())
    accepts = {"scheme": "exact", "payTo": PAY_TO, "price": PRICE, "network": NETWORK}
    from x402.extensions.bazaar import declare_discovery_extension
    from x402.extensions.bazaar import OutputConfig
    out = OutputConfig(example={"vat": "IE6388047V", "status": "valid", "valid": True, "name": "GOOGLE IRELAND LIMITED",
                       "address": "3RD FLOOR, GORDON HOUSE, BARROW STREET, DUBLIN 4", "consultation_number": "WAPIAAAAaE_hpYRy"})
    def r(desc, ext):
        return {"accepts": accepts, "description": desc, "mimeType": "application/json", "extensions": ext}
    routes = {
        "GET /verify": r("EU VAT number validation via official VIES (all 27 EU states + XI): valid/invalid, name, address, consultation (proof) number",
            declare_discovery_extension(input={"vat": "IE6388047V"}, input_schema={"properties": {"vat": {"type": "string", "description": "EU VAT ID with country prefix"}}, "required": ["vat"]}, output=out)),
        "POST /verify/batch": r("Batch EU VAT validation via VIES (up to 100 per request)",
            declare_discovery_extension(input={"vats": ["IE6388047V", "DE811193231"]}, input_schema={"properties": {"vats": {"type": "array", "items": {"type": "string"}}}, "required": ["vats"]}, body_type="json")),
        "GET /company": r("EU company registry lookup by name or registration number",
            declare_discovery_extension(input={"q": "552032534", "country": "FR"}, input_schema={"properties": {"q": {"type": "string"}, "country": {"type": "string"}}, "required": ["q"]})),
        "POST /company/batch": r("Batch EU company registry lookup", None),
    }
    routes["POST /company/batch"].pop("extensions")
    routes["GET /sanctions/screen"] = {**r("Sanctions screening of a person/company name against official EU, UN, OFAC SDN and UK lists (fuzzy match, 0-100 scores, daily refresh)",
        declare_discovery_extension(input={"name": "Sberbank"}, input_schema={"properties": {"name": {"type": "string", "description": "Person or company name"}, "threshold": {"type": "number"}, "birth_year": {"type": "integer"}}, "required": ["name"]})),
        "accepts": {**accepts, "price": PRICE_SANCTIONS}}
    routes["POST /sanctions/screen/batch"] = {"accepts": {**accepts, "price": PRICE_SANCTIONS_BATCH}, "mimeType": "application/json",
        "description": "Batch sanctions screening (EU, UN, OFAC, UK), up to 50 names per request"}
    routes["POST /kyb/check"] = {**r("One-call KYB check of an EU company: VIES VAT validation + official business register + EU/UN/OFAC/UK sanctions screening -> pass/review/fail verdict ($0.03 per company)",
        declare_discovery_extension(input={"company": "FR40303265045"}, input_schema={"properties": {"company": {"description": "VAT ID, 'CC:name', or {name, vatNumber, country, registryId}"}, "threshold": {"type": "number"}}, "required": ["company"]},
            body_type="json", output=OutputConfig(example={"verdict": "pass", "reasons": []}))),
        "accepts": {**accepts, "price": os.getenv("X402_PRICE_KYB", "$0.03")}}
    routes["GET /eori"] = {**r("EU EORI number validation via official EC EOS: valid/invalid, name and address when disclosed",
        declare_discovery_extension(input={"eori": "SE5560360793"}, input_schema={"properties": {"eori": {"type": "string", "description": "EU EORI number with country prefix"}}, "required": ["eori"]},
            output=OutputConfig(example={"eori": "SE5560360793", "status": "valid", "valid": True, "name": "SAAB Aktiebolag", "city": "LINKÖPING", "country": "Sweden"}))),
        "accepts": {**accepts, "price": PRICE_EORI}}
    routes["POST /eori/batch"] = {"accepts": {**accepts, "price": PRICE_EORI_BATCH}, "mimeType": "application/json",
        "description": "Batch EU EORI validation via official EOS (up to 100 per request)"}
    return payment_middleware(routes, server)


class X402Middleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._pay = _build_x402() if ENABLED else None

    async def dispatch(self, request, call_next):
        if not request.url.path.startswith(PAID_PREFIXES):
            return await call_next(request)
        # paying clients skip the free quota
        if self._pay and (request.headers.get("payment-signature") or request.headers.get("x-payment")):
            return await self._pay(request, call_next)
        left = take_free(_client_ip(request))
        if left is not None:
            resp = await call_next(request)
            resp.headers["X-Free-Remaining"] = str(left)
            return resp
        if self._pay:
            return await self._pay(request, call_next)
        return JSONResponse({"error": "free_quota_exhausted", "free_per_day": FREE_PER_DAY,
                             "hint": "Use the Apify Actor wsystem/eu-vat-checker for paid volume."}, status_code=429)
