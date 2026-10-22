# Integration guides

| Guide | For |
|---|---|
| [MCP: Claude Desktop, Cursor and other clients](mcp-claude-cursor.md) | AI assistants and agents that speak MCP |
| [REST from Python and JavaScript](rest-python-js.md) | Backends, scripts, checkouts |
| [n8n](n8n.md) | n8n workflows (HTTP Request node or Apify node) |
| [Make](make.md) | Make (Integromat) scenarios |
| [Zapier via Apify](zapier.md) | Zapier Zaps using the Apify app |
| [x402 agents](x402-agents.md) | Autonomous agents paying per call in USDC on Base |
| [Validate EORI numbers (official EOS API)](eori-validation.md) | Customs, freight and marketplace onboarding; DIY or hosted |

All examples use the public API at `https://api.wsystem.dev` (REST: 20 free calls per day per IP, then x402; MCP: 20 free tool calls per day per session, see [mcp-claude-cursor.md](mcp-claude-cursor.md)) or our Apify Actors (`wsystem/...`, pay per event).
Built and operated by W System (maker disclosure). Data comes live from public EC services (VIES, EOS), public registers and official sanctions lists; not affiliated with the European Commission; not legal or tax advice.
