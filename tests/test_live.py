"""Live tests against real VIES. Run: .venv/bin/pytest -q"""
import asyncio, os, tempfile
os.environ["VATCHECK_CACHE"] = tempfile.mktemp(suffix=".sqlite")
from fastapi.testclient import TestClient
from vatcheck import core
from vatcheck.api import app
c = TestClient(app)

def test_hu_own_company_with_proof():
    r = c.get("/verify", params={"vat": "HU33119856", "nocache": True}).json()
    assert r["valid"] is True and "W SYSTEM" in r["name"]
    assert r["consultation_number"]
    assert r["hu"]["legal_form"] == "Kft." and r["hu"]["checksum_valid"]

def test_de_and_cache():
    r1 = c.get("/verify", params={"vat": "DE 811 193 231"}).json()
    assert r1["status"] in ("valid", "member_state_unavailable")
    if r1["status"] == "valid":
        assert c.get("/verify", params={"vat": "DE811193231"}).json()["cached"] is True

def test_invalid_and_format():
    assert c.get("/verify", params={"vat": "HU12345676"}).json()["status"] == "invalid"
    assert c.get("/verify", params={"vat": "XX123"}).json()["status"] == "invalid_format"

def test_batch():
    r = c.post("/verify/batch", json={"vats": ["HU33119856", "IE6388047V", "bad"]}).json()
    assert r["count"] == 3 and r["results"][2]["status"] == "invalid_format"

def test_outage_handling(monkeypatch):
    monkeypatch.setattr(core, "VIES_URL", "https://httpbin.org/status/503")
    monkeypatch.setattr(core, "MAX_RETRIES", 1)
    r = asyncio.run(core.verify("HU33119856", None, use_cache=False, fallback=False))
    assert r["status"] == "member_state_unavailable" and r["valid"] is None

def test_free_tier_then_402(monkeypatch):
    from vatcheck import x402
    import importlib
    monkeypatch.setenv("X402_ENABLED", "1"); monkeypatch.setenv("X402_PAY_TO", "0x000000000000000000000000000000000000dEaD")
    monkeypatch.setenv("FREE_PER_DAY", "1")
    importlib.reload(x402)
    from fastapi import FastAPI
    a = FastAPI(); a.add_middleware(x402.X402Middleware)
    @a.get("/verify")
    async def v(): return {"ok": True}
    tc = TestClient(a, headers={"x-forwarded-for": f"10.{__import__("random").randint(0,255)}.{__import__("random").randint(0,255)}.7"})
    assert tc.get("/verify").status_code == 200
    r = tc.get("/verify")
    assert r.status_code == 402, r.text
