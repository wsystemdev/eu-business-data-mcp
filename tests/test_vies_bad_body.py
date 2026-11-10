"""VIES 200 with a body that is not one JSON object must be a transient upstream fault, not an exception (2026-11-10:
the JSONDecodeError escaped as ValueError and einvoice_route answered bad_input for a valid VAT; the post-deploy gate caught it)."""
import asyncio
import httpx
from vatcheck import core


def test_vies_200_non_json_is_transient(monkeypatch):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, text='{"a": 1} {"b": 2}', headers={"content-type": "application/json"})

    async def nosleep(*a, **k):
        return None
    monkeypatch.setattr(core.asyncio, "sleep", nosleep)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await core.verify("BE0403170701", None, use_cache=False, client=c, fallback=False)
    r = asyncio.run(run())
    assert r["status"] == "member_state_unavailable", r
    assert r["valid"] is None
    assert len(calls) == core.MAX_RETRIES + 1
