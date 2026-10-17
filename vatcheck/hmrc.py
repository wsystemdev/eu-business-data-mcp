"""UK (GB) VAT number check via HMRC 'Check a UK VAT number' API v2.0.

HMRC retired the unauthenticated v1 endpoint; v2.0 is application-restricted and needs an OAuth 2.0
client_credentials token from an app registered on the HMRC Developer Hub (developer.service.hmrc.gov.uk).
Set HMRC_CLIENT_ID and HMRC_CLIENT_SECRET (optional HMRC_BASE_URL, default production
https://api.service.hmrc.gov.uk; sandbox is https://test-api.service.hmrc.gov.uk; HMRC_REQUESTER_VRN for a
consultation number). Without credentials, GB numbers get a local format + checksum result marked `not_configured`.
XI (Northern Ireland) numbers are checked in VIES, not here.
"""
from __future__ import annotations
import os, re, time, datetime as dt
import httpx

BASE = os.getenv("HMRC_BASE_URL", "https://api.service.hmrc.gov.uk")
_tok: dict = {}


def configured() -> bool:
    return bool(os.getenv("HMRC_CLIENT_ID") and os.getenv("HMRC_CLIENT_SECRET"))


def gb_checksum_ok(num: str) -> bool | None:
    """HMRC mod-97 / mod-9755 check for 9-digit VRNs (first 9 digits of 12-digit branch numbers)."""
    if not re.fullmatch(r"\d{9}(\d{3})?", num):
        return None  # GD/HA government numbers: no checksum
    d = [int(x) for x in num[:9]]
    s = sum(w * x for w, x in zip(range(8, 1, -1), d[:7])) + d[7] * 10 + d[8]
    return s % 97 == 0 or (s + 55) % 97 == 0


async def _token(c: httpx.AsyncClient) -> str:
    if _tok.get("exp", 0) > time.time() + 60:
        return _tok["t"]
    r = await c.post(f"{BASE}/oauth/token", data={"grant_type": "client_credentials", "client_id": os.environ["HMRC_CLIENT_ID"],
                                                 "client_secret": os.environ["HMRC_CLIENT_SECRET"], "scope": "read:vat"}, timeout=15)
    r.raise_for_status()
    j = r.json()
    _tok.update(t=j["access_token"], exp=time.time() + int(j.get("expires_in", 3600)))
    return _tok["t"]


async def verify_gb(num: str, client: httpx.AsyncClient | None = None) -> dict:
    base = {"vat": f"GB{num}", "country_code": "GB", "vat_number": num, "consultation_number": None,
            "source": "HMRC Check a UK VAT number API v2.0", "fallback": False}
    ck = gb_checksum_ok(num)
    if not configured():
        return {**base, "status": "not_configured" if ck is not False else "invalid_format",
                "valid": None if ck is not False else False, "checksum_valid": ck,
                "source": "Local check only (HMRC API credentials not configured)",
                "error": "GB validation needs HMRC Developer Hub OAuth credentials (HMRC_CLIENT_ID/SECRET)." if ck is not False
                         else "GB VAT number fails the HMRC check-digit test"}
    own = client is None
    c = client or httpx.AsyncClient()
    try:
        tok = await _token(c)
        path = f"/organisations/vat/check-vat-number/lookup/{num[:9]}"
        if (rq := os.getenv("HMRC_REQUESTER_VRN")):
            path += f"/{rq}"
        r = await c.get(BASE + path, headers={"Accept": "application/vnd.hmrc.2.0+json", "Authorization": f"Bearer {tok}"}, timeout=15)
        if r.status_code == 404:
            return {**base, "status": "invalid", "valid": False, "name": None, "address": None,
                    "request_date": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"}
        r.raise_for_status()
        j = r.json()
        t = j.get("target") or {}
        a = t.get("address") or {}
        return {**base, "status": "valid", "valid": True, "name": t.get("name"),
                "address": ", ".join(v for k, v in a.items() if v and k != "countryCode") or None,
                "request_date": j.get("processingDate"), "consultation_number": j.get("consultationNumber")}
    except Exception as e:
        return {**base, "status": "error", "valid": None, "error": f"HMRC: {type(e).__name__}: {e}"[:200]}
    finally:
        if own:
            await c.aclose()
