# REST API from Python and JavaScript

Base URL `https://api.wsystem.dev` · OpenAPI docs: <https://api.wsystem.dev/docs> · CORS is enabled, so browser calls work too.
Free: 20 calls per day per IP (see the `X-Free-Remaining` response header). After that you get HTTP 402 (x402 payment, see [x402-agents.md](x402-agents.md)).

| Endpoint | Purpose |
|---|---|
| `GET /verify?vat=DE811193231` | One EU VAT number via VIES |
| `POST /verify/batch` `{"vats": [...]}` | Up to 100 VAT numbers |
| `GET /eori?eori=SE5560360793` | One EORI number via EC EOS |
| `POST /eori/batch` `{"eoris": [...]}` | Up to 100 EORI numbers |
| `GET /company?q=552032534&country=FR` | Official business register lookup |
| `GET /sanctions/screen?name=Sberbank` | EU / UN / OFAC / UK sanctions screening |
| `POST /kyb/check` `{"company": "FR40303265045"}` | VAT + register + sanctions in one call |

VAT `status` values: `valid`, `invalid`, `invalid_format`, `member_state_unavailable` (national backend down: retry later, `valid` is `null`), `error`.

## curl
```bash
curl "https://api.wsystem.dev/verify?vat=IE6388047V"
curl "https://api.wsystem.dev/eori?eori=SE5560360793"
```

## Python (requests)
```python
import requests

API = "https://api.wsystem.dev"

r = requests.get(f"{API}/verify", params={"vat": "IE6388047V"}, timeout=30)
r.raise_for_status()
v = r.json()
print(v["status"], v["name"], v.get("consultation_number"))

r = requests.post(f"{API}/verify/batch", json={"vats": ["DE811193231", "FR40303265045"]}, timeout=60)
for item in r.json()["results"]:
    print(item["vat"], item["status"])

e = requests.get(f"{API}/eori", params={"eori": "SE5560360793"}, timeout=30).json()
print(e["status"], e["name"])
```
Skip the consultation number with `requester=` (empty), or pass your own EU VAT number as `requester` to get one issued to you.

## JavaScript (Node 18+ or browser, fetch)
```js
const API = "https://api.wsystem.dev";

const v = await (await fetch(`${API}/verify?vat=${encodeURIComponent("IE6388047V")}`)).json();
console.log(v.status, v.name);

const batch = await (await fetch(`${API}/eori/batch`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ eoris: ["SE5560360793", "DE1234567"] }),
})).json();
console.log(batch.valid, "of", batch.count, "valid");
```

## Handling limits
- `429 free_quota_exhausted` or `402 Payment Required`: free allowance used. Pay per call with x402 or use the Apify Actor [wsystem/eu-vat-checker](https://apify.com/wsystem/eu-vat-checker) for volume.
- `member_state_unavailable`: retry with backoff; do not treat it as invalid.
