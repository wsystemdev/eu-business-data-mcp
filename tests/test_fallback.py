"""VIES-down fallback, last-good cache and GB/HMRC handling."""
import asyncio
from unittest import mock
from vatcheck import core, fallback, hmrc


async def _down(*a, **k):
    return {"_error": "MS_UNAVAILABLE", "_transient": True, "_attempts": 5}


def run(c):
    return asyncio.run(c)


def test_national_fallback_cz():
    with mock.patch.object(core, "_vies_call", _down):
        r = run(core.verify("CZ00177041", None, use_cache=False))
    assert r["fallback"] is True and r["fallback_type"] == "national_source"
    assert r["consultation_number"] is None and "ARES" in r["source"] and r["valid"] is True
    assert r["vies_error"] == "MS_UNAVAILABLE"


def test_last_vies_cache_fallback(tmp_path):
    db = str(tmp_path / "c.sqlite")
    with mock.patch.object(core, "CACHE_PATH", db):
        fallback.remember_vies(db, {"vat": "IE6388047V", "valid": True, "status": "valid", "name": "GOOGLE IRELAND LIMITED",
                                    "consultation_number": "WAPIAAAA"})
        with mock.patch.object(core, "_vies_call", _down):
            r = run(core.verify("IE6388047V", None, use_cache=False))
    assert r["fallback_type"] == "last_vies_result" and r["consultation_number"] is None
    assert r["original_consultation_number"] == "WAPIAAAA" and "cached result from" in r["source"]


def test_no_fallback_flag():
    with mock.patch.object(core, "_vies_call", _down):
        r = run(core.verify("DE811193231", None, use_cache=False, fallback=False))
    assert r["status"] == "member_state_unavailable" and r["valid"] is None


def test_gb_without_credentials():
    assert hmrc.gb_checksum_ok("220430231") is True
    r = run(core.verify("GB 220 4302 31"))
    assert r["country_code"] == "GB" and r["status"] in ("not_configured", "valid")
    assert run(core.verify("GB123456789"))["status"] in ("invalid_format", "invalid")


def test_vies_status():
    s = run(core.vies_status())
    assert len(s["countries"]) >= 28 and any(c["national_fallback"] for c in s["countries"])
