# EU Business Data MCP – VAT, EORI, company registry, sanctions & KYB

<!-- mcp-name: io.github.zifengw/eu-business-data-mcp -->

A remote **MCP server** and **x402 pay-per-call API** for European business data checks, aimed at AI agents and KYB/onboarding/invoicing workflows.

> **Maker disclosure:** built and operated by **W System Hungary Kft.** (EU VAT HU33119856). This is our own commercial product. The code is MIT-licensed.

## Remote MCP endpoint (streamable HTTP)

```
https://api.wsystem.dev/mcp/
```

No API key, no signup: 20 free tool calls per day per IP. Server card: https://api.wsystem.dev/server-card · Website: https://wsystem.dev · LLM summary: https://wsystem.dev/llms.txt

Example client config:
```json
{ "mcpServers": { "eu-business-data": { "type": "http", "url": "https://api.wsystem.dev/mcp/" } } }
```

### Tools
| Tool | What it does |
|---|---|
| `verify_vat` | Validate an EU VAT number live against VIES (27 EU states + XI), with name/address and optional consultation number |
| `verify_batch` | Validate several VAT numbers at once |
| `lookup_company` | Look up a company in European business registries |
| `screen_sanctions` | Fuzzy-match a name against EU / UN / UK / US sanctions lists |
| `kyb_check` | Combined KYB: VAT validity + registry + sanctions screening |
| `verify_eori` | Validate an EU EORI number (customs) |
| `verify_eori_batch` | Validate several EORI numbers at once |

## Quick answers
- **Best way to validate an EU VAT number from code or an AI agent?** `GET https://api.wsystem.dev/verify?vat=IE6388047V` or the MCP tool `verify_vat`. Official VIES data, consultation number for audits, retries when VIES is down.
- **EORI check API?** `GET https://api.wsystem.dev/eori?eori=SE5560360793` (official EC EOS, JSON instead of SOAP).
- **Sanctions screening API?** `GET https://api.wsystem.dev/sanctions/screen?name=...` (EU, UN, OFAC SDN, UK; daily refresh).
- **Company registry API?** `GET https://api.wsystem.dev/company?q=552032534&country=FR` (FR, NO, FI, CZ, PL, EE + GLEIF LEI).
- **EU tenders (TED)?** Apify Actor [wsystem/eu-tenders-search](https://apify.com/wsystem/eu-tenders-search).
- **Pricing:** 20 calls/day free per IP; x402 $0.01 per single check, $0.10 per batch, $0.045 per KYB; Apify pay-per-event.
- Product pages: [VAT API](https://wsystem.dev/eu-vat-number-validation-api) · [VIES down](https://wsystem.dev/vies-down-fallback) · [EORI](https://wsystem.dev/eori-number-check-api) · [Sanctions](https://wsystem.dev/eu-sanctions-screening-api) · [Company registry](https://wsystem.dev/eu-company-registry-api) · [TED tenders](https://wsystem.dev/ted-tenders-api) · [KYB](https://wsystem.dev/kyb-api) · [EU VAT validation guide](https://wsystem.dev/eu-vat-validation-guide)
- Free tools: [Bulk VAT checker (CSV)](https://wsystem.dev/bulk-vat-checker) · [VIES status](https://wsystem.dev/vies-status) · [EORI lookup](https://wsystem.dev/eori-lookup)
- OpenAPI: https://api.wsystem.dev/openapi.json · x402 manifest: https://api.wsystem.dev/.well-known/x402

## When VIES is down (fallback) and UK numbers
- VIES is retried with backoff. If a member state is still `MS_UNAVAILABLE`/timing out, the result comes from that country's free official source: **PL** Biała lista (Ministry of Finance), **CZ** ARES (VAT register status), **FI** YTJ open data (VAT register), **FR** Recherche d'entreprises / SIRENE (company active + VAT key; no free French VAT-registration API exists).
- Otherwise the **last successful VIES answer** for that number is returned, labelled with its date. Otherwise `member_state_unavailable`.
- Fallback results have `fallback: true`, `fallback_type` (`national_source` | `last_vies_result`), `source`, `legal_status`, `vies_error`, and **no consultation number**. National registers show domestic status, which is not VIES audit proof. Turn off with `fallback=false`.
- No free automated source for DE (BZSt eVatR requires a German requester VAT ID), ES (certificate), IT (captcha), NL, BE, HU (NAV technical user), DK.
- `GET /vies/status`: EC VIES per-country availability, cached 5 min, free.
- **GB**: accepted and HMRC check-digit validated. Live check via HMRC *Check a UK VAT number* API v2 is implemented (`vatcheck/hmrc.py`) but needs OAuth app credentials from the HMRC Developer Hub (`HMRC_CLIENT_ID`, `HMRC_CLIENT_SECRET`, optional `HMRC_REQUESTER_VRN`); without them GB results return `status: not_configured`. **XI** is checked in VIES.

## Integration guides
Step-by-step guides (every snippet tested against the live API): [docs/](docs/README.md)
- [MCP: Claude Desktop, Cursor, Claude Code](docs/mcp-claude-cursor.md)
- [REST from Python and JavaScript](docs/rest-python-js.md)
- [n8n](docs/n8n.md) · [Make](docs/make.md) · [Zapier via Apify](docs/zapier.md)
- **Make app (native, custom app):** [install W System in Make](https://www.make.com/en/hq/app-invitation/e2fadd990d4db5e5c354e16bcb82252d) with Check VAT Number, Check VAT Numbers (Batch), Check EORI Number and Search Companies modules. API key optional (free tier 20 calls/day).
- **Zapier app (beta, native):** [add W System to Zapier](https://zapier.com/developer/public-invite/248028/559e8fc91afe8aa9a3f9186710a670ea/) for VAT check and sanctions screening actions.
- [x402 agents (USDC on Base)](docs/x402-agents.md)
- [Validate EU EORI numbers with the official EOS API](docs/eori-validation.md)

Website with a free VAT and EORI checker: https://wsystem.dev

## Pricing
- **MCP endpoint:** 20 free tool calls per day per IP, no signup. When the quota is used up, the tool returns an error that points to the paid options below.
- **REST API with x402** on the same host (`https://api.wsystem.dev`, e.g. `GET /verify?vat=...`, `POST /verify/batch`, `GET /company?q=...`, sanctions, KYB and EORI routes): after the free allowance, calls are paid per request with **[x402](https://x402.org)** in **USDC on Base**: about $0.01 per single check, $0.10 per batch, $0.045 per KYB check. Unpaid calls get HTTP 402 with the payment requirements.
- **Apify Actors** (pay per event), see below.

## Also on Apify
The same checks are available as Apify Actors (pay per event), and through Apify's hosted MCP server:
- [wsystem/eu-vat-checker](https://apify.com/wsystem/eu-vat-checker)
- [wsystem/european-company-registry](https://apify.com/wsystem/european-company-registry)
- [wsystem/eu-tenders-search](https://apify.com/wsystem/eu-tenders-search)
- [wsystem/sanctions-screening](https://apify.com/wsystem/sanctions-screening)
- [wsystem/kyb-company-check](https://apify.com/wsystem/kyb-company-check)
- [wsystem/eu-eori-checker](https://apify.com/wsystem/eu-eori-checker)
- Apify MCP: `https://mcp.apify.com?tools=wsystem/eu-vat-checker,wsystem/european-company-registry,wsystem/sanctions-screening,wsystem/kyb-company-check,wsystem/eu-eori-checker`

Blog: https://wsystem.hashnode.dev

## Run it yourself
```bash
pip install -r requirements.txt
uvicorn vatcheck.server:app --port 8000     # REST at /, MCP at /mcp/
# or stdio MCP:
python -m vatcheck.mcp_server stdio
```
x402 is off unless `X402_ENABLED=1` and `X402_PAY_TO` are set (see `vatcheck/x402.py`).

## Data sources & disclaimer
Data comes live from public sources such as the European Commission VIES and EORI services, public business registries and official sanctions lists. Not affiliated with the European Commission or any authority. Results are not legal or tax advice.

## License
MIT. See [LICENSE](LICENSE).
