from fastapi.testclient import TestClient
from vatcheck.api import app

def test_cors_preflight_and_header():
    c = TestClient(app)
    r = c.options("/verify?vat=HU33119856", headers={"Origin": "https://wsystem.dev", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] in ("*", "https://wsystem.dev")
    r = c.get("/health", headers={"Origin": "https://wsystem.dev"})
    assert r.headers["access-control-allow-origin"] == "*"
