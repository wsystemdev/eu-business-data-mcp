# Zapier (via the Apify app)

Zapier has an official **Apify** app, so our Actors work in any Zap without custom code.

1. Trigger: e.g. **Google Sheets → New Spreadsheet Row** or **Stripe → New Customer** with a VAT number field.
2. Action: **Apify → Run Actor**. Connect your Apify account.
3. Actor: `wsystem/eu-vat-checker`. Run synchronously: **Yes**.
4. Input JSON (map the VAT field from step 1):
```json
{ "vatNumbers": ["{{VAT field}}"] }
```
5. Action: **Apify → Fetch Dataset Items** (dataset ID from step 2), then e.g. **Filter by Zapier** on `status` = `valid` and update the row or customer.

Other Actors: `wsystem/european-company-registry`, `wsystem/sanctions-screening`, `wsystem/kyb-company-check`, `wsystem/eu-tenders-search`.

### Without the Apify app
**Webhooks by Zapier → GET**, URL `https://api.wsystem.dev/verify`, Query String `vat` = mapped field (free tier, 20 calls/day per IP; Zapier's shared IPs may exhaust it, so the Apify route is more reliable).

What the Apify action calls under the hood:
```bash
curl -X POST "https://api.apify.com/v2/acts/wsystem~eu-vat-checker/run-sync-get-dataset-items" \
  -H "Authorization: Bearer $APIFY_TOKEN" -H "Content-Type: application/json" \
  -d '{"vatNumbers":["IE6388047V"]}'
```
