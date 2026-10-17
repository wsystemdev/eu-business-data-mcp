# MCP setup: Claude Desktop, Cursor and other clients

Remote MCP endpoint (streamable HTTP, no signup, 20 free tool calls per day per IP):

```
https://api.wsystem.dev/mcp/
```

Tools: `verify_vat`, `verify_batch`, `lookup_company`, `screen_sanctions`, `kyb_check`, `verify_eori`, `verify_eori_batch`.

## Cursor
`~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project):
```json
{
  "mcpServers": {
    "eu-business-data": { "url": "https://api.wsystem.dev/mcp/" }
  }
}
```

## Claude Desktop
Claude Desktop (paid plans) can add remote servers in **Settings → Connectors → Add custom connector** with the URL above.
On any plan you can bridge the remote server to stdio with [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) (needs Node.js) in `claude_desktop_config.json`:
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
Restart Claude Desktop after editing.

## Claude Code
```bash
claude mcp add --transport http eu-business-data https://api.wsystem.dev/mcp/
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
