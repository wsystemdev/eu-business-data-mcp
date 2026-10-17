# x402 agents: pay per call in USDC on Base

`api.wsystem.dev` speaks [x402](https://x402.org). After the free allowance (20 calls/day/IP), a REST call returns **HTTP 402** with payment requirements (scheme `exact`, network `eip155:8453` = Base mainnet, asset USDC). An x402 client signs an EIP-3009 USDC authorization and retries; no account or API key.

| Route | Price |
|---|---|
| `GET /verify`, `/eori`, `/company`, `/sanctions/screen` | $0.01 |
| `POST /verify/batch`, `/eori/batch`, `/company/batch`, `/sanctions/screen/batch` | $0.10 |
| `POST /kyb/check` | $0.045 |

## Python (official `x402` SDK)
```python
# pip install "x402[requests,evm]" eth-account
import os
from eth_account import Account
from x402 import x402ClientSync
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.http.clients import x402_requests

account = Account.from_key(os.environ["BUYER_PRIVATE_KEY"])  # a wallet holding a little USDC on Base
client = x402ClientSync()
register_exact_evm_client(client, EthAccountSigner(account))

session = x402_requests(client)          # pays automatically on HTTP 402
r = session.get("https://api.wsystem.dev/verify", params={"vat": "IE6388047V"}, timeout=60)
print(r.status_code, r.json()["status"], r.json()["name"])
print("payment receipt:", r.headers.get("PAYMENT-RESPONSE") or r.headers.get("X-PAYMENT-RESPONSE"))
```
Use a dedicated low-balance wallet for agents. While you still have free calls, the session simply gets a 200 without paying.

## TypeScript (`@x402/fetch`)
```ts
// npm i @x402/fetch @x402/evm viem
import { wrapFetchWithPayment, x402Client } from "@x402/fetch";
import { registerExactEvmScheme } from "@x402/evm/exact/client";
import { privateKeyToAccount } from "viem/accounts";

const client = new x402Client();
registerExactEvmScheme(client, { signer: privateKeyToAccount(process.env.BUYER_PRIVATE_KEY as `0x${string}`) });
const fetchWithPay = wrapFetchWithPayment(fetch, client);

const res = await fetchWithPay("https://api.wsystem.dev/eori?eori=SE5560360793");
console.log(res.status, await res.json());
```

## Inspect the 402 yourself
```bash
curl -i "https://api.wsystem.dev/verify?vat=IE6388047V"   # after the free quota: HTTP 402 + PAYMENT-REQUIRED header
```
The service is also listed in the x402 Bazaar. MCP clients use the free MCP endpoint instead (see [mcp-claude-cursor.md](mcp-claude-cursor.md)).
