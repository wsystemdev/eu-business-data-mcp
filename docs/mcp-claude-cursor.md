# MCP setup: Claude Desktop, Cursor and other clients

Remote MCP endpoint (streamable HTTP, no signup):

```
https://api.wsystem.dev/mcp/
```

Tools (13): `verify_vat`, `verify_batch`, `verify_eori`, `verify_eori_batch`, `lookup_company`, `screen_sanctions`, `kyb_check`, `validate_einvoice`, `create_einvoice`, `einvoice_route`, `send_einvoice_sandbox`, `due_diligence_report`, `check_fr_pa_registry`.

## Free limits
| Limit | Value |
|---|---|
| Free tool calls per MCP session | 20 per UTC day |
| Keyless calls from Anthropic's outbound range 160.79.104.0/21 (all Claude connector users together) | 2,000 lookups per UTC day; a batch counts one per number |
| Direct clients (Claude Code, mcp-remote, Cursor and others on your machine) | 20 per day per session and 20 per IP |
| New keyless sessions per IP | 1,500 per day (3,000 per Anthropic address) |
| With an API key (`X-API-Key` or `Authorization: Bearer`) | No daily free limit; the key's monthly units, same weights as the REST API |

Live values: https://api.wsystem.dev/mcp/limits. When a limit is reached, the tool returns an error that names it and explains how to add a key.

## API key
<a id="api-key"></a>
Keys: https://wsystem.dev/pricing (Starter EUR 19/month, 5,000 units; Pro EUR 49/month, 25,000 units; there is no free key, the free tier needs none). Send it as the header `X-API-Key: <key>` (or `Authorization: Bearer <key>`). Don't put it in the URL: the server doesn't read keys from query parameters, and [Anthropic's connector docs](https://claude.com/docs/connectors/building/authentication) say credentials must not go there.

- **Claude.ai / Claude Desktop custom connector:** add the connector with **No sign in**, open **Request headers**, pick `x-api-key` and paste the key. Anthropic says Request headers are in beta and [available to a limited set of organizations](https://claude.com/docs/connectors/custom/remote-mcp#authenticate-with-request-headers); headers can't be changed after adding (remove and re-add the connector). On Team/Enterprise the key is shared by everyone using that connector.
- **No Request headers section?** Use Claude Code or `mcp-remote` (below), both send the header.
- Full guide: https://wsystem.dev/vat-number-checker-mcp-claude

## Cursor
`~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project):
```json
{
  "mcpServers": {
    "eu-business-data": { "url": "https://api.wsystem.dev/mcp/" }
  }
}
```
With an API key, add `"headers": { "X-API-Key": "YOUR_KEY" }` next to `"url"`.

## Claude.ai and Claude Desktop (custom connector)
Go to **Customize > Connectors**, click **+ Add > Add custom connector**, enter a name and the URL above, choose **No sign in** (optionally add `x-api-key` under Request headers, see above) and click **Add**. Team/Enterprise: an Owner adds it under **Organization settings > Connectors**. Custom connectors are available on Free (one connector), Pro, Max, Team and Enterprise plans ([Anthropic help center](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)). They connect from Anthropic's cloud, not your computer.

## Claude Desktop config file (local bridge)
You can bridge the remote server to stdio with [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) (needs Node.js) in `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "eu-business-data": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://api.wsystem.dev/mcp/"]
    }
  }
}
```
With an API key (no spaces around the colon; value kept in `env`, as the [mcp-remote README](https://github.com/geelen/mcp-remote#custom-headers) recommends):
```json
{
  "mcpServers": {
    "eu-business-data": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://api.wsystem.dev/mcp/", "--header", "X-API-Key:${WSYSTEM_API_KEY}"],
      "env": { "WSYSTEM_API_KEY": "YOUR_KEY" }
    }
  }
}
```
Restart Claude Desktop after editing.

## Claude Code
```bash
claude mcp add --transport http eu-business-data https://api.wsystem.dev/mcp/
# with an API key:
claude mcp add --transport http eu-business-data https://api.wsystem.dev/mcp/ --header "X-API-Key: YOUR_KEY"
```

## Generic clients (VS Code, Windsurf, custom agents)
```json
{ "mcpServers": { "eu-business-data": { "type": "http", "url": "https://api.wsystem.dev/mcp/" } } }
```

## Test from Python (official `mcp` SDK)
```python
# pip install mcp
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client("https://api.wsystem.dev/mcp/") as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            print([t.name for t in (await s.list_tools()).tools])
            r = await s.call_tool("verify_vat", {"vat": "IE6388047V"})
            print(r.content[0].text)

asyncio.run(main())
```

## Example prompts
- "Is FR40303265045 a valid EU VAT number? Give me the registered name and address."
- "Validate EORI SE5560360793 before I file this customs declaration."
- "Run a KYB check on NO:equinor and tell me if anything needs review."

## Alternative: Apify hosted MCP
Our Apify Actors are also available through Apify's MCP server (uses your Apify account and pay-per-event pricing):
`https://mcp.apify.com/?tools=wsystem/eu-vat-checker,wsystem/european-company-registry,wsystem/sanctions-screening,wsystem/kyb-company-check`
