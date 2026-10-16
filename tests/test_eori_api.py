import os
os.environ.setdefault("FREE_PER_DAY", "1000")
from fastapi.testclient import TestClient
from vatcheck.api import app
from vatcheck import eori as eori_mod

c = TestClient(app)

def test_eori_format_offline():
    assert eori_mod.normalize(" se-5560.360793 ") == "SE5560360793"
    assert eori_mod.is_valid_format("SE5560360793")
    assert not eori_mod.is_valid_format("xx")

def test_eori_get_live():
    r = c.get("/eori", params={"eori": "SE5560360793"}).json()
    assert r["status"] == "valid" and r["valid"] is True
    assert r["name"] and "SAAB" in r["name"].upper()

def test_eori_batch():
    r = c.post("/eori/batch", json={"eoris": ["SE5560360793", "xx"]}).json()
    assert r["count"] == 2
    assert r["results"][0]["status"] == "valid"
    assert r["results"][1]["status"] == "invalid_format"

def test_x402_eori_routes_declared():
    import vatcheck.x402 as x
    src = open(x.__file__).read()
    assert '"GET /eori"' in src and '"POST /eori/batch"' in src and "/eori" in x.PAID_PREFIXES
