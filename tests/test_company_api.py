import os
os.environ.setdefault("FREE_PER_DAY", "1000")
from fastapi.testclient import TestClient
from vatcheck.api import app

c = TestClient(app)

def test_company_get():
    r = c.get("/company", params={"q": "923609016", "country": "NO"}).json()
    assert r["status"] == "found" and "EQUINOR" in r["results"][0]["name"]

def test_company_batch():
    r = c.post("/company/batch", json={"queries": [{"query": "552032534", "country": "FR"}, {"query": "00177041", "country": "CZ"}], "limit": 1}).json()
    assert r["count"] == 2 and r["found"] == 2

def test_x402_routes_declared(monkeypatch):
    import importlib, vatcheck.x402 as x
    src = open(x.__file__).read()
    assert '"GET /company"' in src and '"POST /company/batch"' in src and "/company" in x.PAID_PREFIXES
