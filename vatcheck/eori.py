"""EU EORI validation via the official European Commission EOS SOAP API.

Endpoint: POST https://ec.europa.eu/taxation_customs/dds2/eos/validation/services/validation
Max 10 EORIs per request. Status 0 = Valid, 1 = Not valid.
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET

import httpx

EOS_URL = "https://ec.europa.eu/taxation_customs/dds2/eos/validation/services/validation"
SOURCE = "EORI (European Commission EOS)"
SOURCE_URL = "https://ec.europa.eu/taxation_customs/dds2/eos/eori_validation.jsp"
UA = "vatcheck-eori/1.0 (+https://eu-vat-checker.onrender.com)"
NS = {"soap": "http://schemas.xmlsoap.org/soap/envelope/", "eori": "http://eori.ws.eos.dds.s/"}
# After normalize: 2-letter country + 1–15 Basic Latin chars (total length 3–17).
_FORMAT_RE = re.compile(r"^[A-Z]{2}[A-Za-z0-9+]{1,15}$")
_MAX_CHUNK = 10


def normalize(eori: str) -> str:
    """Strip spaces/dots/dashes and uppercase."""
    if eori is None:
        return ""
    return re.sub(r"[\s.\-]", "", str(eori)).upper()


def is_valid_format(eori: str) -> bool:
    """True if normalized EORI matches EC XSD-ish pattern (length 3–17, 2 letters + Latin)."""
    n = normalize(eori)
    if not (3 <= len(n) <= 17):
        return False
    # Reject non-Basic-Latin beyond the pattern (no accents etc. in the ID itself).
    if any(ord(c) > 0x7F for c in n):
        return False
    return bool(_FORMAT_RE.match(n))


def _empty_result(eori: str, status: str, *, valid=None, error=None, status_code=None, status_description=None) -> dict:
    n = normalize(eori) if eori else ""
    return {
        "eori": n or (str(eori).strip() if eori else ""),
        "country_code": n[:2] if len(n) >= 2 and n[:2].isalpha() else None,
        "status": status,
        "valid": valid,
        "name": None,
        "address": None,
        "street": None,
        "postal_code": None,
        "city": None,
        "country": None,
        "request_date": None,
        "status_code": status_code,
        "status_description": status_description,
        "error": error,
        "source": SOURCE,
        "source_url": SOURCE_URL,
    }


def _text(el: ET.Element | None, tag: str) -> str | None:
    if el is None:
        return None
    # Children may be namespaced or not depending on parser; try both.
    child = el.find(f"{{http://eori.ws.eos.dds.s/}}{tag}")
    if child is None:
        child = el.find(tag)
    if child is None:
        # walk local-name
        for c in list(el):
            if c.tag.rsplit("}", 1)[-1] == tag:
                child = c
                break
    if child is None or child.text is None:
        return None
    t = child.text.strip()
    return t or None


def _parse_result(result_el: ET.Element, request_date: str | None) -> dict:
    eori = _text(result_el, "eori") or ""
    raw_status = _text(result_el, "status")
    descr = _text(result_el, "statusDescr")
    try:
        code = int(raw_status) if raw_status is not None else None
    except ValueError:
        code = None
    if code == 0:
        status, valid = "valid", True
    elif code == 1:
        status, valid = "invalid", False
    else:
        status, valid = "error", None
    return {
        "eori": normalize(eori) if eori else eori,
        "country_code": (normalize(eori)[:2] if eori else None),
        "status": status,
        "valid": valid,
        "name": _text(result_el, "name"),
        "address": _text(result_el, "address"),
        "street": _text(result_el, "street"),
        "postal_code": _text(result_el, "postalCode"),
        "city": _text(result_el, "city"),
        "country": _text(result_el, "country"),
        "request_date": request_date,
        "status_code": code,
        "status_description": descr,
        "error": None if status != "error" else f"unexpected EOS status: {raw_status}",
        "source": SOURCE,
        "source_url": SOURCE_URL,
    }


def _build_soap(eoris: list[str]) -> str:
    parts = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
             '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">',
             "<soap:Body>",
             '<validateEORI xmlns="http://eori.ws.eos.dds.s/">']
    for e in eoris:
        parts.append(f"<eori>{e}</eori>")
    parts.extend(["</validateEORI>", "</soap:Body>", "</soap:Envelope>"])
    return "".join(parts)


def _parse_soap(xml_text: str) -> tuple[str | None, list[dict]]:
    root = ET.fromstring(xml_text)
    # Find return element
    ret = None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "return":
            ret = el
            break
    if ret is None:
        raise ValueError("SOAP response missing return element")
    request_date = _text(ret, "requestDate")
    results = []
    for el in list(ret):
        if el.tag.rsplit("}", 1)[-1] == "result":
            results.append(_parse_result(el, request_date))
    return request_date, results


async def _post_chunk(client: httpx.AsyncClient, eoris: list[str]) -> list[dict]:
    body = _build_soap(eoris)
    last_err = None
    for i in range(4):
        try:
            r = await client.post(
                EOS_URL,
                content=body.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8", "User-Agent": UA},
            )
            if r.status_code in (429, 502, 503, 504):
                await asyncio.sleep(1.5 * (i + 1))
                last_err = f"HTTP {r.status_code}"
                continue
            r.raise_for_status()
            _, parsed = _parse_soap(r.text)
            # Map back to requested order; API usually preserves order.
            by_eori = {p["eori"]: p for p in parsed}
            out = []
            for e in eoris:
                if e in by_eori:
                    out.append(by_eori[e])
                else:
                    # try case-insensitive
                    match = next((p for p in parsed if p["eori"] == e), None)
                    if match:
                        out.append(match)
                    else:
                        out.append(_empty_result(e, "error", error="EORI missing from EOS response"))
            return out
        except (httpx.TransportError, httpx.HTTPStatusError, ET.ParseError, ValueError) as ex:
            last_err = str(ex)
            if i == 3:
                break
            await asyncio.sleep(1.5 * (i + 1))
    return [_empty_result(e, "error", error=last_err or "EOS request failed") for e in eoris]


async def validate_one(eori: str, client: httpx.AsyncClient | None = None) -> dict:
    n = normalize(eori)
    if not is_valid_format(n):
        return _empty_result(eori, "invalid_format", valid=False, error="invalid EORI format",
                             status_description="Invalid format")
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        results = await _post_chunk(client, [n])
        return results[0]
    finally:
        if own:
            await client.aclose()


async def validate_many(eoris: list[str], concurrency: int = 4) -> list[dict]:
    """Validate many EORIs. Format failures skip the API. SOAP calls in chunks of ≤10."""
    if not eoris:
        return []
    indexed: list[tuple[int, str]] = [(i, e) for i, e in enumerate(eoris)]
    out: list[dict | None] = [None] * len(eoris)
    to_call: list[tuple[int, str]] = []
    for i, raw in indexed:
        n = normalize(raw)
        if not is_valid_format(n):
            out[i] = _empty_result(raw, "invalid_format", valid=False, error="invalid EORI format",
                                   status_description="Invalid format")
        else:
            to_call.append((i, n))

    # Chunk into groups of 10; run up to `concurrency` chunks in parallel.
    chunks: list[list[tuple[int, str]]] = []
    for j in range(0, len(to_call), _MAX_CHUNK):
        chunks.append(to_call[j:j + _MAX_CHUNK])

    sem = asyncio.Semaphore(max(1, concurrency))

    async with httpx.AsyncClient(timeout=30.0) as client:
        async def run_chunk(chunk: list[tuple[int, str]]):
            async with sem:
                e_list = [e for _, e in chunk]
                results = await _post_chunk(client, e_list)
                for (idx, _), res in zip(chunk, results):
                    out[idx] = res

        await asyncio.gather(*(run_chunk(c) for c in chunks))

    return [r if r is not None else _empty_result(eoris[i], "error", error="internal") for i, r in enumerate(out)]
