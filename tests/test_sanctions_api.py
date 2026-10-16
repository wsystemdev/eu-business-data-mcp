import os, pytest
os.environ.setdefault("FREE_PER_DAY", "1000")
from fastapi.testclient import TestClient
from vatcheck.api import app
import vatcheck.x402 as x

c = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_x402_module(monkeypatch):
    # other tests reload vatcheck.x402 with temporary env; restore module globals from the real env
    import importlib
    monkeypatch.setenv("FREE_PER_DAY", "1000")
    importlib.reload(x)

def test_screen_get():
    r = c.get("/sanctions/screen", params={"name": "Sberbank"}).json()
    assert r["status"] == "potential_match" and r["top_score"] >= 90 and set(r["lists"]) == {"EU", "UN", "OFAC", "UK"}

def test_screen_clean():
    assert c.get("/sanctions/screen", params={"name": "Zifeng Wei", "threshold": 90}).json()["status"] == "no_match"

def test_screen_batch():
    r = c.post("/sanctions/screen/batch", json={"names": ["Vladimir Putin", "Example Trading Kft"]}).json()
    assert r["count"] == 2 and r["potential_matches"] == 1

def test_x402_defaults():
    if not os.getenv("X402_NETWORK"): assert x.NETWORK == "eip155:8453"
    if not os.getenv("X402_FACILITATOR_URL"): assert x.FACILITATOR == "https://facilitator.payai.network"
    if not os.getenv("X402_PAY_TO"): assert x.PAY_TO == "0xfbbe4924a9d36e1de2b90cf4f8ee6e03f978714c"
    assert "/sanctions" in x.PAID_PREFIXES
