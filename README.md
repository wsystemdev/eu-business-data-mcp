# EU Business Data MCP – VAT, EORI, company registry, sanctions, KYB & e-invoicing

<!-- mcp-name: io.github.wsystemdev/eu-business-data-mcp -->

A remote **MCP server** and **x402 pay-per-call API** for European business data checks, aimed at AI agents and KYB/onboarding/invoicing workflows.

> **Maker disclosure:** built and operated by **W System**. This is our own commercial product. The code is MIT-licensed.

## Remote MCP endpoint (streamable HTTP)

```
https://api.wsystem.dev/mcp/
```

Free tier, no signup: **20 tool calls per day per MCP session**. Direct clients (Claude Code, Cursor, mcp-remote and others connecting from your own machine) also get at most 20 per IP, so opening more sessions doesn't add calls. Claude.ai / Claude Desktop custom connectors call from Anthropic's cloud ([outbound range 160.79.104.0/21](https://docs.anthropic.com/en/api/ip-addresses)), so those calls are counted per session, not per IP; all keyless traffic from that range shares **2,000 lookups per day** (`verify_batch` / `verify_eori_batch` count one per number). New keyless sessions: 1,500 per IP per day (3,000 per Anthropic address). Limits reset at 00:00 UTC; live values: https://api.wsystem.dev/mcp/limits

Optional **API key** for volume: send `X-API-Key: <key>` (or `Authorization: Bearer <key>`) with every MCP request. Keyed calls use the key's monthly units (same weights as the REST API) instead of the free tier. Keys: https://wsystem.dev/pricing (Starter EUR 19/month, 5,000 units; Pro EUR 49/month, 25,000 units; there is no free key). Keys in the URL are not accepted. How to add one in Claude: [docs/mcp-claude-cursor.md](docs/mcp-claude-cursor.md#api-key).

Server card: https://api.wsystem.dev/server-card · Website: https://wsystem.dev · LLM summary: https://wsystem.dev/llms.txt

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
| `validate_einvoice` | Validate a UBL or CII e-invoice against the official EN 16931 and XRechnung rules: errors with rule IDs, plain-English messages and fix hints |
| `create_einvoice` | Turn simple JSON into a validated UBL 2.1 invoice (EN 16931, XRechnung or Peppol BIS 3 structure) |
| `einvoice_route` | "Can I e-invoice this company?": VIES + Peppol Directory + country B2B mandate + recommended format |
| `send_einvoice_sandbox` | SANDBOX: verify buyer VAT + sanctions → create/validate EN 16931 → route → mock transport (nothing transmitted) → Ed25519-signed evidence receipt |
| `due_diligence_report` | VAT, register, sanctions, EORI, GLEIF LEI and Peppol in one report with risk flags and a timestamp |
| `check_fr_pa_registry` | France: is this e-invoicing platform on the official DGFiP list of Plateformes Agréées (PA)? Name or website in; approved / awaiting interop tests / not found, registration date and official source out (also free as REST: `GET https://api.wsystem.dev/fr/pa-registry?name=Pennylane`) |

## Quick answers
- **Best way to validate an EU VAT number from code or an AI agent?** `GET https://api.wsystem.dev/verify?vat=IE6388047V` or the MCP tool `verify_vat`. Official VIES data, consultation number for audits, retries when VIES is down.
- **EORI check API?** `GET https://api.wsystem.dev/eori?eori=SE5560360793` (official EC EOS, JSON instead of SOAP).
- **Sanctions screening API?** `GET https://api.wsystem.dev/sanctions/screen?name=...` (EU, UN, OFAC SDN, UK; daily refresh).
- **Company registry API?** `GET https://api.wsystem.dev/company?q=552032534&country=FR` (FR, NO, FI, CZ, PL, EE + GLEIF LEI).
- **E-invoice validation API (EN 16931, XRechnung, ZUGFeRD/Factur-X)?** `POST https://api.wsystem.dev/einvoice/validate` with the XML or PDF as the body. Create: `POST /einvoice/create` (example body: `GET /einvoice/sample`). Routing: `GET /einvoice/route?id=BE0403170701`. **SANDBOX send** (verify → create → route → mock transport → signed evidence): `POST /einvoice/send` (public key: `GET /einvoice/evidence-key`). Peppol BIS 3 specific rules are not checked yet; live Peppol transmission is not available. Rule artefacts: CEN EN 16931 (EUPL-1.2, https://github.com/ConnectingEurope/eInvoicing-EN16931) and KoSIT XRechnung configuration (Apache-2.0, https://github.com/itplr-kosit/validator-configuration-xrechnung), downloaded unmodified at runtime. Pages: https://wsystem.dev/eu-e-invoice-validation-api · https://wsystem.dev/e-invoicing-api
- **Counterparty due-diligence report?** `POST https://api.wsystem.dev/ddreport` `{"company":"FR40303265045"}`.
- **EU tenders (TED)?** Apify Actor [wsystem/eu-tenders-search](https://apify.com/wsystem/eu-tenders-search).
- **Pricing:** REST 20 calls/day free per IP, MCP 20 tool calls/day per session; x402 $0.01 per single check, $0.10 per batch, $0.045 per KYB, $0.03 e-invoice validate, $0.08 e-invoice create / sandbox send, $0.02 e-invoice route, $0.49 due-diligence report; Apify pay-per-event.
- Product pages: [VAT API](https://wsystem.dev/eu-vat-number-validation-api) · [VIES down](https://wsystem.dev/vies-down-fallback) · [EORI](https://wsystem.dev/eori-number-check-api) · [Sanctions](https://wsystem.dev/eu-sanctions-screening-api) · [Company registry](https://wsystem.dev/eu-company-registry-api) · [TED tenders](https://wsystem.dev/ted-tenders-api) · [KYB](https://wsystem.dev/kyb-api) · [E-invoice](https://wsystem.dev/eu-e-invoice-validation-api) · [EU VAT validation guide](https://wsystem.dev/eu-vat-validation-guide)
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
- [Validate EU VAT numbers directly against VIES in Python](https://github.com/wsystemdev/vies-vat-validation-python): one zero-dependency file with retries for MS_UNAVAILABLE, CSV batch and consultation numbers (no W System account needed)

Website with a free VAT and EORI checker: https://wsystem.dev

## Pricing
- **MCP endpoint:** free, 20 tool calls per day per MCP session (details and the shared limit for Claude connector traffic above). With an API key in `X-API-Key`: Starter EUR 19/month (5,000 units) or Pro EUR 49/month (25,000 units). When a free limit is reached, the tool returns an error that names the limit and explains how to add a key.
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

## Ready-made examples (run in one click on Apify)

Each example opens a pre-filled run with real sample output, so you can see the result format before you pay for anything:

- [Check German VAT numbers (USt-IdNr.) in VIES](https://apify.com/wsystem/eu-vat-checker/examples/german-vat-number-check)
- [Check French VAT numbers (TVA intracommunautaire)](https://apify.com/wsystem/eu-vat-checker/examples/french-vat-number-check)
- [Check Italian VAT numbers (Partita IVA) in VIES](https://apify.com/wsystem/eu-vat-checker/examples/italian-vat-number-check)
- [Check Spanish VAT numbers (NIF-IVA) in VIES](https://apify.com/wsystem/eu-vat-checker/examples/spanish-vat-number-check)
- [Check Polish VAT numbers (NIP) in VIES](https://apify.com/wsystem/eu-vat-checker/examples/polish-vat-number-check)
- [Bulk validate EU VAT numbers from many countries](https://apify.com/wsystem/eu-vat-checker/examples/bulk-eu-vat-validation)
- [Bulk validate EU EORI numbers](https://apify.com/wsystem/eu-eori-checker/examples/bulk-eori-number-validation)
- [Look up French companies by SIREN or name](https://apify.com/wsystem/european-company-registry/examples/french-company-lookup-siren)
- [Search Nordic company registers (Norway, Finland)](https://apify.com/wsystem/european-company-registry/examples/nordic-company-register-search)
- [Look up Czech and Polish companies (ARES, KRS)](https://apify.com/wsystem/european-company-registry/examples/czech-polish-company-lookup)
- [Open IT and software tenders in Europe (TED)](https://apify.com/wsystem/eu-tenders-search/examples/it-software-tenders-europe)
- [Open construction tenders in Germany (TED)](https://apify.com/wsystem/eu-tenders-search/examples/construction-tenders-germany)
- [Medical equipment tenders in the EU (TED)](https://apify.com/wsystem/eu-tenders-search/examples/medical-equipment-tenders-eu)
- [Screen names against the OFAC SDN list](https://apify.com/wsystem/sanctions-screening/examples/ofac-sdn-list-check)
- [Screen names against the EU consolidated sanctions list](https://apify.com/wsystem/sanctions-screening/examples/eu-sanctions-list-check)
- [Screen names against the UK sanctions list](https://apify.com/wsystem/sanctions-screening/examples/uk-sanctions-list-check)
- [Supplier onboarding check: VAT, registry and sanctions](https://apify.com/wsystem/kyb-company-check/examples/supplier-onboarding-kyb-check)
- [Verify B2B customers by VAT number](https://apify.com/wsystem/kyb-company-check/examples/b2b-customer-verification)

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
