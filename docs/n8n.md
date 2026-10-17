# n8n

Two options: call the REST API with the built-in **HTTP Request** node (free tier / small volume), or run our Apify Actors with the **Apify** node (pay per event, good for scheduled bulk jobs).

## Option A: HTTP Request node
1. Add an **HTTP Request** node. Method `GET`, URL `https://api.wsystem.dev/verify`.
2. Enable **Send Query Parameters** and add `vat` = `{{ $json.vat }}` (the field holding the VAT number).
3. Add an **IF** node on `{{ $json.status }}` equals `valid`. Route `member_state_unavailable` to a **Wait** node and retry.

Importable workflow (paste into the n8n editor):
```json
{
  "nodes": [
    { "parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0] },
    { "parameters": { "assignments": { "assignments": [ { "id": "1", "name": "vat", "value": "IE6388047V", "type": "string" } ] }, "options": {} },
      "name": "Set VAT", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [220, 0] },
    { "parameters": { "url": "https://api.wsystem.dev/verify", "sendQuery": true,
        "queryParameters": { "parameters": [ { "name": "vat", "value": "={{ $json.vat }}" } ] }, "options": {} },
      "name": "Check VAT", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [440, 0] }
  ],
  "connections": {
    "Manual Trigger": { "main": [[ { "node": "Set VAT", "type": "main", "index": 0 } ]] },
    "Set VAT": { "main": [[ { "node": "Check VAT", "type": "main", "index": 0 } ]] }
  }
}
```
For EORI use URL `https://api.wsystem.dev/eori` with query parameter `eori`. For many numbers per execution, use `POST https://api.wsystem.dev/verify/batch` with JSON body `{"vats": {{ JSON.stringify($json.vats) }}}`.

## Option B: Apify node (bulk, scheduled)
1. Install the Apify node (n8n Cloud: built in; self-hosted: **Settings → Community nodes → `@apify/n8n-nodes-apify`**).
2. Create Apify credentials with your [Apify API token](https://console.apify.com/settings/integrations).
3. Operation **Run actor and get dataset**, Actor `wsystem/eu-vat-checker`, input:
```json
{ "vatNumbers": ["IE6388047V", "DE811193231"], "requesterVatNumber": "" }
```
Equivalent raw call (also works from an HTTP Request node):
```bash
curl -X POST "https://api.apify.com/v2/acts/wsystem~eu-vat-checker/run-sync-get-dataset-items" \
  -H "Authorization: Bearer $APIFY_TOKEN" -H "Content-Type: application/json" \
  -d '{"vatNumbers":["IE6388047V"]}'
```
