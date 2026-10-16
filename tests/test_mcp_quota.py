"""MCP HTTP per-IP cap: run server in a subprocess with MCP_FREE_PER_DAY=2."""
import asyncio, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[1]

def test_mcp_http_quota():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, "MCP_FREE_PER_DAY": "2", "VATCHECK_CACHE": tempfile.mktemp(suffix=".sqlite"), "X402_ENABLED": "0"}
    # cwd must be package root so uvicorn can import vatcheck.* regardless of pytest invoke path
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "vatcheck.server:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(4)
        async def go():
            async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp/") as (r, w, _):
                async with ClientSession(r, w) as cs:
                    await cs.initialize()
                    out = [await cs.call_tool("verify_vat", {"vat": "XX1"}) for _ in range(3)]
                    return out
        res = asyncio.run(go())
        assert not res[0].isError and not res[1].isError
        assert res[2].isError and "x402" in res[2].content[0].text and "apify.com" in res[2].content[0].text
    finally:
        p.terminate()
