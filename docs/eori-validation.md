# Validate EU EORI numbers with the official EOS API

*Disclosure: I'm the maker of the EORI checker linked at the end (W System). Everything before that section works with free, official endpoints only.*

If you import into or export out of the EU, every customs declaration carries an EORI number (Economic Operators Registration and Identification). Freight forwarders, customs brokers and marketplaces check these numbers when onboarding a shipper, and a typo or a deregistered number means a held shipment. The European Commission runs a free validation service called EOS. It has a web form, but for anything automated you'll want its SOAP API. Here is what works today, including two gotchas that cost me time.

## The endpoint

```
POST https://ec.europa.eu/taxation_customs/dds2/eos/validation/services/validation
Content-Type: text/xml; charset=utf-8
```

No API key, no account. One request can carry **up to 10 EORI numbers**.

```bash
curl -s -X POST https://ec.europa.eu/taxation_customs/dds2/eos/validation/services/validation \
  -H 'Content-Type: text/xml; charset=utf-8' \
  -d '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ev:validateEORI xmlns:ev="http://eori.ws.eos.dds.s/">
      <ev:eori>SE5560360793</ev:eori>
      <ev:eori>DE1234567</ev:eori>
    </ev:validateEORI>
  </soap:Body>
</soap:Envelope>'
```

The response I got back today (trimmed):

```xml
<return>
  <requestDate>17/10/2026</requestDate>
  <result>
    <eori>SE5560360793</eori><status>0</status><statusDescr>Valid</statusDescr>
    <name>SAAB Aktiebolag</name><postalCode>58188</postalCode>
    <city>LINKÖPING</city><country>Sweden</country>
  </result>
  <result>
    <eori>DE1234567</eori><status>1</status><statusDescr>Not valid</statusDescr>
  </result>
</return>
```

- `status` **0 = valid, 1 = not valid**.
- Name and address only appear when the trader agreed to publish them. Many valid numbers come back with no name at all, so don't treat a missing name as a failure.
- `requestDate` is the date of the check. Store it alongside the result if you need an audit trail.

## A Python version (tested)

```python
import re
import xml.etree.ElementTree as ET
import requests

EOS_URL = "https://ec.europa.eu/taxation_customs/dds2/eos/validation/services/validation"
NS = "http://eori.ws.eos.dds.s/"

def clean(eori: str) -> str:
    return re.sub(r"[\s.\-]", "", eori).upper()

def check_eoris(eoris):
    """Validate up to 10 EU EORI numbers per call against the EC EOS service."""
    eoris = [clean(e) for e in eoris]
    body = "".join(f"<ev:eori>{e}</ev:eori>" for e in eoris)
    envelope = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<soap:Body><ev:validateEORI xmlns:ev="{NS}">{body}</ev:validateEORI>'
        "</soap:Body></soap:Envelope>"
    )
    r = requests.post(EOS_URL, data=envelope.encode(),
                      headers={"Content-Type": "text/xml; charset=utf-8",
                               "User-Agent": "my-app-eori-check/1.0"},  # see gotcha below
                      timeout=20)
    r.raise_for_status()
    out = []
    for res in ET.fromstring(r.content).iter("result"):
        item = {child.tag: child.text for child in res}
        item["valid"] = item.get("status") == "0"
        out.append(item)
    return out

if __name__ == "__main__":
    for row in check_eoris(["SE 556036-0793", "DE1234567"]):
        print(row)
```

Output:

```
{'eori': 'SE5560360793', 'status': '0', 'statusDescr': 'Valid', 'name': 'SAAB Aktiebolag', ..., 'city': 'LINKÖPING', 'country': 'Sweden', 'valid': True}
{'eori': 'DE1234567', 'status': '1', 'statusDescr': 'Not valid', 'valid': False}
```

For more than 10 numbers, chunk the list into groups of 10 and send the chunks with modest concurrency.

## Gotcha 1: the default python-requests User-Agent gets a 502

This one surprised me. The exact same envelope that works with `curl` returned **HTTP 502 Bad Gateway** from Python `requests` every time. The only difference was the User-Agent header: with the library default (`python-requests/x.y`) the gateway answers 502, and with any other value, like `my-app-eori-check/1.0`, it answers 200. So if you see 502s from EOS in code but not in your terminal, set a User-Agent before you start debugging the XML.

## Gotcha 2: GB numbers aren't in EOS any more

Since Brexit, `GB...` EORIs are validated by HMRC, not the Commission. Northern Ireland traders have an `XI...` number, which **is** in EOS. HMRC's API is a simple JSON call, also without auth:

```bash
curl -s -X POST https://api.service.hmrc.gov.uk/customs/eori/lookup/check-multiple-eori \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/vnd.hmrc.1.0+json' \
  -d '{"eoris":["GB225109100000"]}'
```

A robust checker routes by prefix: `GB` goes to HMRC, everything else (EU-27 plus `XI`) goes to EOS.

## Other practical tips

- **Clean the input.** People paste `SE 556036-0793` or `de-123.456`. Strip spaces, dots and dashes and uppercase it before sending.
- **Validate the format locally.** An EORI is a two-letter country code followed by up to 15 alphanumeric characters (17 in total at most). Rejecting garbage locally saves calls and gives users instant feedback.
- **An outage is not "invalid".** EOS has maintenance windows and occasional 5xx errors. Retry with backoff and mark the check as pending, so a customer isn't rejected because Brussels was down.
- **EORI is not VAT.** In many EU countries the EORI is based on the company's VAT or tax number, but it's a separate registration: a valid VAT number doesn't mean the company has an EORI, and vice versa. Check VAT numbers against VIES separately.

## If you'd rather not maintain the SOAP plumbing

I built two hosted versions of the above, with the input cleaning, format checks, chunking and retries done for you:

- **Apify Actor:** [EU EORI Number Checker](https://apify.com/wsystem/eu-eori-checker). Batch up to 1,000 EORIs per run, JSON or CSV out, $0.005 per answered check plus $0.005 per run. Format errors and EOS outages aren't charged. It also works as a tool for AI agents through Apify's MCP server (`https://mcp.apify.com/?tools=wsystem/eu-eori-checker`).
- **HTTP API / MCP server:** `GET https://api.wsystem.dev/eori?eori=SE5560360793` and `POST /eori/batch`, plus a remote MCP endpoint at `https://api.wsystem.dev/mcp/` with a `verify_eori` tool. 20 free calls per day per IP, then pay per call in USDC via x402. Source on [GitHub](https://github.com/zifengw/eu-business-data-mcp).

Either way, the official endpoint above is free and works fine if you'd rather run it yourself.
