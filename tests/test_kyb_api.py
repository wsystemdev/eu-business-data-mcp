import os, tempfile
os.environ.setdefault("VATCHECK_CACHE", tempfile.mktemp(suffix=".sqlite"))
from fastapi.testclient import TestClient
from vatcheck.api import app

def test_kyb_vat_pass_or_review():
    c = TestClient(app, headers={"x-forwarded-for": "10.77.1.1"})
    r = c.post("/kyb/check", json={"company": "FR40303265045"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["verdict"] in ("pass", "review") and d["vat"] and d["sanctions"] is not None

def test_kyb_sanctioned_name():
    c = TestClient(app, headers={"x-forwarded-for": "10.77.1.2"})
    d = c.post("/kyb/check", json={"company": {"name": "Sberbank", "country": "RU"}}).json()
    assert d["verdict"] in ("fail", "review")
