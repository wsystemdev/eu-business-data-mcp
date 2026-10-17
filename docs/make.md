# Make (Integromat)

## Option 0: native W System app (recommended)
1. Open the invite link: [https://www.make.com/en/hq/app-invitation/e2fadd990d4db5e5c354e16bcb82252d](https://www.make.com/en/hq/app-invitation/e2fadd990d4db5e5c354e16bcb82252d) and install the app into your Make organization.
2. Add a module: **Check VAT Number**, **Check VAT Numbers (Batch)**, **Check EORI Number** or **Search Companies**.
3. Create a connection. The API key is optional: leave it empty for the free tier (20 calls/day), or paste a key from https://wsystem.dev/#plans.
4. Route on `status` = `valid`; send `member_state_unavailable` to a retry branch, not to "invalid".

## Option A: HTTP module (REST API)
1. Add **HTTP → Make a request**.
2. URL `https://api.wsystem.dev/verify`, Method `GET`, Query String: `vat` = the mapped VAT field. Enable **Parse response**.
3. Add a **Router/Filter** on `status` = `valid`. Send `member_state_unavailable` to a **Sleep** + retry branch, not to "invalid".

Batch: Method `POST`, URL `https://api.wsystem.dev/verify/batch`, Body type **Raw**, Content type **JSON**, body:
```json
{"vats": ["IE6388047V", "DE811193231"]}
```
The response has `count`, `valid` and a `results` array (use an **Iterator** on `results`).
EORI: `GET https://api.wsystem.dev/eori?eori=SE5560360793`.

## Option B: Apify app (bulk, pay per event)
1. Add **Apify → Run an Actor**, connect your Apify account.
2. Actor: `wsystem/eu-vat-checker` (or `wsystem/european-company-registry`, `wsystem/sanctions-screening`, `wsystem/kyb-company-check`).
3. Input JSON: `{"vatNumbers": ["IE6388047V", "DE811193231"]}`, enable **Run synchronously**.
4. Add **Apify → Get Dataset Items** with the run's default dataset ID to iterate results.
