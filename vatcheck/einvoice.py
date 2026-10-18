"""EU e-invoice validation (EN 16931, XRechnung) and creation (UBL 2.1).

Rule artefacts are NOT bundled in this repo. They are downloaded once from the official
KoSIT release (Apache-2.0; it ships the CEN EN 16931 XSLT under EUPL-1.2, unmodified):
  https://github.com/itplr-kosit/validator-configuration-xrechnung
and cached in $EINVOICE_ARTIFACTS (default /tmp/einvoice-artifacts).

Peppol BIS 3 rules are deliberately NOT run: OpenPeppol has not given a licence that
allows use in a paid hosted service (see /workspace/growth/EINVOICE_LICENCES.md).
"""
from __future__ import annotations
import io, os, re, threading, zipfile, hashlib, time
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

import httpx
from lxml import etree

XR_VERSION = "2026-08-31"
XR_URL = ("https://github.com/itplr-kosit/validator-configuration-xrechnung/releases/download/"
          f"v{XR_VERSION}/xrechnung-3.0.2-validator-configuration-{XR_VERSION}.zip")
XR_SHA256 = os.getenv("EINVOICE_XR_SHA256", "")  # optional pin
ART_DIR = os.getenv("EINVOICE_ARTIFACTS", "/tmp/einvoice-artifacts")
MAX_BYTES = int(os.getenv("EINVOICE_MAX_BYTES", 5 * 1024 * 1024))

NS = {
    "ubl_inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "ubl_cn": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
    "cii": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "svrl": "http://purl.oclc.org/dsdl/svrl",
}
XRECHNUNG_ID = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
PEPPOL_ID = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
EN16931_ID = "urn:cen.eu:en16931:2017"
DISCLAIMER = ("Technical check against the official EN 16931 / XRechnung rule sets. A technically valid file is not tax or legal advice. "
              "Peppol BIS 3 specific rules (PEPPOL-EN16931-*) are not checked yet.")
SOURCES = {"EN16931": "CEN/TC 434 EN 16931 validation artefacts (EUPL-1.2) https://github.com/ConnectingEurope/eInvoicing-EN16931",
           "XRechnung": "KoSIT XRechnung validator configuration (Apache-2.0) https://github.com/itplr-kosit/validator-configuration-xrechnung"}

_lock = threading.Lock()
_state: dict = {}


class InputError(ValueError):
    pass


# ---------------------------------------------------------------- artefacts
def _root() -> str:
    return os.path.join(ART_DIR, XR_VERSION)


def ensure_artifacts() -> str:
    root = _root()
    if os.path.exists(os.path.join(root, ".ok")):
        return root
    with _lock:
        if os.path.exists(os.path.join(root, ".ok")):
            return root
        data = httpx.get(XR_URL, follow_redirects=True, timeout=60).content
        if XR_SHA256 and hashlib.sha256(data).hexdigest() != XR_SHA256:
            raise RuntimeError("artefact checksum mismatch")
        os.makedirs(root, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(data)).extractall(root)
        open(os.path.join(root, ".ok"), "w").write(XR_URL)
    return root


def _saxon():
    if "proc" not in _state:
        from saxonche import PySaxonProcessor
        _state["proc"] = PySaxonProcessor(license=False)
    return _state["proc"]


XSL = {("ubl", "EN16931"): "resources/ubl/2.1/xsl/EN16931-UBL-validation.xsl",
       ("cii", "EN16931"): "resources/cii/16b/xsl/EN16931-CII-validation.xsl",
       ("ubl", "XRechnung"): "resources/xrechnung/3.0.2/xsl/XRechnung-UBL-validation.xsl",
       ("cii", "XRechnung"): "resources/xrechnung/3.0.2/xsl/XRechnung-CII-validation.xsl"}
XSD = {"ubl_inv": "resources/ubl/2.1/xsd/maindoc/UBL-Invoice-2.1.xsd",
       "ubl_cn": "resources/ubl/2.1/xsd/maindoc/UBL-CreditNote-2.1.xsd",
       "cii": "resources/cii/16b/xsd/CrossIndustryInvoice_100pD16B.xsd"}


def _xslt(syntax, ruleset):
    key = ("xsl", syntax, ruleset)
    if key not in _state:
        p = _saxon().new_xslt30_processor()
        _state[key] = p.compile_stylesheet(stylesheet_file=os.path.join(ensure_artifacts(), XSL[(syntax, ruleset)]))
    return _state[key]


def _xsd(doctype):
    key = ("xsd", doctype)
    if key not in _state:
        _state[key] = etree.XMLSchema(etree.parse(os.path.join(ensure_artifacts(), XSD[doctype])))
    return _state[key]


def warm():
    try:
        with _lock_run:
            for k in XSL:
                _xslt(*k)
            for d in XSD:
                _xsd(d)
    except Exception:
        pass


_lock_run = threading.RLock()

# ---------------------------------------------------------------- input
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_blank_text=False)
PDF_XML_NAMES = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "zugferd_invoice.xml", "order-x.xml")


def extract_xml(data: bytes) -> tuple[bytes, dict]:
    """Return (xml_bytes, meta). Accepts raw XML or a PDF with an embedded e-invoice (ZUGFeRD / Factur-X)."""
    if not data:
        raise InputError("empty body: send the invoice XML or PDF as the request body")
    if len(data) > MAX_BYTES:
        raise InputError(f"file too large (max {MAX_BYTES // 1024 // 1024} MB)")
    if data.lstrip()[:5] == b"%PDF-":
        from pypdf import PdfReader
        try:
            r = PdfReader(io.BytesIO(data))
            files = {}
            for name, contents in (r.attachments or {}).items():
                files[name] = contents[0] if isinstance(contents, list) else contents
        except Exception as e:
            raise InputError(f"could not read PDF: {e}")
        if not files:
            raise InputError("PDF has no embedded files: this is not a ZUGFeRD/Factur-X/XRechnung hybrid invoice")
        pick = next((n for n in files if n.lower() in PDF_XML_NAMES), None) or next((n for n in files if n.lower().endswith(".xml")), None)
        if not pick:
            raise InputError(f"PDF has embedded files but none is XML: {sorted(files)}")
        return files[pick], {"container": "pdf", "embedded_file": pick}
    return data, {"container": "xml"}


def _parse(xml: bytes):
    if b"<!DOCTYPE" in xml[:2048] or b"<!ENTITY" in xml[:4096]:
        raise InputError("DOCTYPE/ENTITY declarations are not allowed in e-invoices")
    try:
        return etree.fromstring(xml, _PARSER)
    except etree.XMLSyntaxError as e:
        raise InputError(f"not well-formed XML: {e}")


def detect(root) -> dict:
    tag = etree.QName(root)
    if tag.namespace == NS["ubl_inv"] and tag.localname == "Invoice":
        syntax, doctype = "ubl", "ubl_inv"
    elif tag.namespace == NS["ubl_cn"] and tag.localname == "CreditNote":
        syntax, doctype = "ubl", "ubl_cn"
    elif tag.namespace == NS["cii"] and tag.localname == "CrossIndustryInvoice":
        syntax, doctype = "cii", "cii"
    else:
        raise InputError(f"unsupported root element {{{tag.namespace}}}{tag.localname}: expected UBL Invoice/CreditNote or UN/CEFACT CII")
    if syntax == "ubl":
        cid = root.findtext("cbc:CustomizationID", namespaces=NS) or ""
    else:
        cid = root.findtext("rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", namespaces=NS) or ""
    cid = cid.strip()
    c = cid.lower()
    if "xrechnung" in c:
        profile = "XRechnung"
    elif "peppol.eu:2017:poacc:billing" in c:
        profile = "Peppol BIS Billing 3.0"
    elif "factur-x.eu" in c or "zugferd" in c:
        profile = "Factur-X/ZUGFeRD " + (c.split(":")[-1].upper() if ":" in c else "")
    elif c.startswith("urn:cen.eu:en16931:2017"):
        profile = "EN 16931" if "#" not in c else "EN 16931 CIUS/extension"
    else:
        profile = "unknown"
    notes = []
    if "minimum" in c or "basicwl" in c:
        notes.append("Factur-X MINIMUM and BASIC WL are not EN 16931 compliant invoices; in DE/FR B2B they are not valid e-invoices on their own.")
    if "extended" in c:
        notes.append("Factur-X EXTENDED goes beyond EN 16931; extra elements are not checked by the EN 16931 rules.")
    return {"syntax": syntax, "document_type": doctype, "customization_id": cid, "profile": profile, "notes": notes}


# ---------------------------------------------------------------- plain-English layer
FIX_HINTS = {
    "BR-01": "Add the specification identifier (UBL cbc:CustomizationID / CII GuidelineSpecifiedDocumentContextParameter).",
    "BR-02": "Add an invoice number.",
    "BR-03": "Add the invoice issue date (YYYY-MM-DD).",
    "BR-05": "Add the invoice currency code (e.g. EUR).",
    "BR-06": "Add the seller's legal name.",
    "BR-07": "Add the buyer's name.",
    "BR-08": "Add the seller's postal address.",
    "BR-09": "Add the seller's country code (ISO 3166-1 alpha-2).",
    "BR-10": "Add the buyer's postal address.",
    "BR-11": "Add the buyer's country code.",
    "BR-16": "Add at least one invoice line.",
    "BR-CO-10": "Sum of line net amounts must equal the document line total: recompute LineExtensionAmount.",
    "BR-CO-13": "Total without VAT must be line total minus allowances plus charges.",
    "BR-CO-15": "Total with VAT must be total without VAT plus total VAT.",
    "BR-CO-16": "Amount due must be total with VAT minus prepaid amount plus rounding.",
    "BR-CO-17": "VAT amount per category must be taxable amount x rate, rounded to 2 decimals.",
    "BR-CO-26": "Give the seller a VAT ID, a legal registration ID or another identifier.",
    "BR-S-08": "VAT breakdown for standard rate must match the sum of the S-rated lines.",
    "BR-AE-02": "Reverse charge (AE) needs the seller's VAT ID and the buyer's VAT ID.",
    "BR-DE-1": "XRechnung needs payment instructions (payment means).",
    "BR-DE-2": "XRechnung needs a seller contact (name, phone and e-mail).",
    "BR-DE-15": "XRechnung needs a Buyer reference (Leitweg-ID for German public buyers).",
    "BR-DE-16": "XRechnung needs the seller VAT ID, tax number or tax representative.",
    "PEPPOL-EN16931-R020": "Add the seller electronic address (EndpointID with schemeID).",
}


def _clean(text: str, rid: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"^\[[^\]]+\]\s*-?\s*", "", t)
    return t


def _svrl(out: str, ruleset: str) -> list[dict]:
    doc = etree.fromstring(out.encode("utf-8"))
    res = []
    for fa in doc.iter("{%s}failed-assert" % NS["svrl"], "{%s}successful-report" % NS["svrl"]):
        rid = fa.get("id") or ""
        flag = (fa.get("flag") or fa.get("role") or "fatal").lower()
        text = "".join(fa.find("svrl:text", NS).itertext()) if fa.find("svrl:text", NS) is not None else ""
        bts = sorted(set(re.findall(r"\b(B[TG]-\d+)\b", text)))
        e = {"rule_id": rid, "severity": "warning" if flag in ("warning", "information", "info") else "error",
             "ruleset": ruleset, "message": _clean(text, rid), "location": fa.get("location"), "business_terms": bts}
        if rid in FIX_HINTS:
            e["fix"] = FIX_HINTS[rid]
        res.append(e)
    return res


def _xsd_errors(root, doctype) -> list[dict]:
    s = _xsd(doctype)
    if s.validate(etree.ElementTree(root)):
        return []
    out = []
    for e in list(s.error_log)[:50]:
        msg = re.sub(r"\{[^}]+\}", "", e.message)
        out.append({"rule_id": "XSD", "severity": "error", "ruleset": "XML Schema (UBL 2.1 / CII D16B)",
                    "message": f"Line {e.line}: {msg}", "location": e.path,
                    "fix": "The XML structure is wrong (unknown element, wrong order or bad value). Fix this first; business rules may be unreliable until it passes."})
    return out


def validate(data: bytes, rules: str = "auto") -> dict:
    t0 = time.time()
    xml, meta = extract_xml(data)
    root = _parse(xml)
    d = detect(root)
    rules = (rules or "auto").lower()
    sets = ["EN16931"]
    if rules == "xrechnung" or (rules == "auto" and d["profile"] == "XRechnung"):
        sets.append("XRechnung")
    elif rules not in ("auto", "en16931"):
        raise InputError("rules must be auto, en16931 or xrechnung (peppol is not available yet)")
    with _lock_run:
        issues = _xsd_errors(root, d["document_type"])
        node = _saxon().parse_xml(xml_text=etree.tostring(root, encoding="unicode"))
        for rs in sets:
            issues += _svrl(_xslt(d["syntax"], rs).transform_to_string(xdm_node=node), rs)
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    checked = {"xsd": True, "EN16931": True, "XRechnung": "XRechnung" in sets, "PeppolBIS3": False}
    return {"valid": not errors, "error_count": len(errors), "warning_count": len(warnings),
            **meta, **{k: v for k, v in d.items() if k != "notes"}, "notes": d["notes"],
            "checked": checked, "errors": errors, "warnings": warnings,
            "rule_versions": {"EN16931": "1.3.15+ via KoSIT config", "XRechnung": f"3.0.2 config {XR_VERSION}"},
            "sources": SOURCES, "disclaimer": DISCLAIMER, "ms": int((time.time() - t0) * 1000)}


# ---------------------------------------------------------------- create (JSON -> UBL)
D2 = Decimal("0.01")
EXEMPT_DEFAULTS = {"AE": ("VATEX-EU-AE", "Reverse charge"), "K": ("VATEX-EU-IC", "Intra-Community supply"),
                   "G": ("VATEX-EU-G", "Export outside the EU"), "O": ("VATEX-EU-O", "Not subject to VAT"),
                   "E": (None, "Exempt from VAT")}
# Peppol participant scheme by country, used when deriving an EndpointID from a VAT ID.
VAT_SCHEME = {"AT": "9915", "BE": "0208", "DE": "9930", "DK": "0184", "ES": "9920", "FR": "9957", "GR": "9933",
              "HU": "9910", "IE": "9935", "IT": "0211", "LU": "9938", "NL": "9944", "PL": "9945", "PT": "9946",
              "RO": "9947", "SE": "0007", "SI": "9949", "SK": "9950", "CZ": "9929", "EE": "9931", "FI": "0216",
              "HR": "9934", "LT": "9937", "LV": "9939", "MT": "9943", "BG": "9926", "CY": "9928", "NO": "0192"}


def _dec(v, field) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        raise InputError(f"{field} must be a number")


def _a(v: Decimal) -> str:
    return str(v.quantize(D2, ROUND_HALF_UP))


def _num(v: Decimal) -> str:
    s = format(v.normalize(), "f")
    return s if s not in ("-0",) else "0"


def _x(v) -> str:
    return escape(str(v))


def _endpoint(p: dict, fmt: str, role: str):
    if p.get("endpoint_id"):
        return p["endpoint_id"], p.get("endpoint_scheme") or ("EM" if "@" in p["endpoint_id"] else "0088")
    vat = (p.get("vat_id") or "").replace(" ", "").upper()
    if fmt == "peppol-bis3":
        cc = vat[:2]
        if cc == "BE" and vat[2:].isdigit():
            return vat[2:], "0208"
        if cc in VAT_SCHEME and vat:
            return vat, VAT_SCHEME[cc]
        raise InputError(f"{role}.endpoint_id and endpoint_scheme are required for Peppol (could not derive from VAT ID)")
    if p.get("email"):
        return p["email"], "EM"
    if vat:
        return None, None
    return None, None


def _party(p: dict, role: str, fmt: str, cur: str) -> str:
    for f in ("name", "country"):
        if not p.get(f):
            raise InputError(f"{role}.{f} is required")
    cc = p["country"].upper()
    eid, esch = _endpoint(p, fmt, role)
    parts = []
    if eid:
        parts.append(f'<cbc:EndpointID schemeID="{_x(esch)}">{_x(eid)}</cbc:EndpointID>')
    if p.get("trading_name"):
        parts.append(f"<cac:PartyName><cbc:Name>{_x(p['trading_name'])}</cbc:Name></cac:PartyName>")
    addr = "<cac:PostalAddress>"
    if p.get("street"):
        addr += f"<cbc:StreetName>{_x(p['street'])}</cbc:StreetName>"
    if p.get("city"):
        addr += f"<cbc:CityName>{_x(p['city'])}</cbc:CityName>"
    if p.get("postal_code"):
        addr += f"<cbc:PostalZone>{_x(p['postal_code'])}</cbc:PostalZone>"
    addr += f"<cac:Country><cbc:IdentificationCode>{_x(cc)}</cbc:IdentificationCode></cac:Country></cac:PostalAddress>"
    parts.append(addr)
    if p.get("vat_id"):
        parts.append(f"<cac:PartyTaxScheme><cbc:CompanyID>{_x(p['vat_id'].replace(' ', '').upper())}</cbc:CompanyID>"
                     "<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>")
    if p.get("tax_number"):
        parts.append(f"<cac:PartyTaxScheme><cbc:CompanyID>{_x(p['tax_number'])}</cbc:CompanyID>"
                     "<cac:TaxScheme><cbc:ID>FC</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>")
    le = f"<cac:PartyLegalEntity><cbc:RegistrationName>{_x(p['name'])}</cbc:RegistrationName>"
    if p.get("company_id"):
        le += f"<cbc:CompanyID>{_x(p['company_id'])}</cbc:CompanyID>"
    parts.append(le + "</cac:PartyLegalEntity>")
    if p.get("contact_name") or p.get("phone") or p.get("email"):
        c = "<cac:Contact>"
        if p.get("contact_name"):
            c += f"<cbc:Name>{_x(p['contact_name'])}</cbc:Name>"
        if p.get("phone"):
            c += f"<cbc:Telephone>{_x(p['phone'])}</cbc:Telephone>"
        if p.get("email"):
            c += f"<cbc:ElectronicMail>{_x(p['email'])}</cbc:ElectronicMail>"
        parts.append(c + "</cac:Contact>")
    return "<cac:Party>" + "".join(parts) + "</cac:Party>"


def _taxcat(cat: str, rate: Decimal, tag="cac:ClassifiedTaxCategory") -> str:
    s = f"<{tag}><cbc:ID>{_x(cat)}</cbc:ID>"
    if cat != "O":
        s += f"<cbc:Percent>{_num(rate)}</cbc:Percent>"
    return s + f"<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></{tag}>"


def build_ubl(j: dict) -> tuple[str, dict]:
    if not isinstance(j, dict):
        raise InputError("body must be a JSON object")
    fmt = (j.get("format") or "en16931").lower()
    if fmt not in ("en16931", "xrechnung", "peppol-bis3"):
        raise InputError("format must be en16931, xrechnung or peppol-bis3")
    is_cn = str(j.get("type_code", "380")) in ("381", "396", "81", "83", "532")
    for f in ("invoice_number", "issue_date", "seller", "buyer", "lines"):
        if not j.get(f):
            raise InputError(f"{f} is required")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(j["issue_date"])):
        raise InputError("issue_date must be YYYY-MM-DD")
    cur = (j.get("currency") or "EUR").upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        raise InputError("currency must be an ISO 4217 code like EUR")
    lines_xml, groups, total = [], {}, Decimal(0)
    if not isinstance(j["lines"], list) or len(j["lines"]) > 500:
        raise InputError("lines must be a list of up to 500 items")
    for i, ln in enumerate(j["lines"], 1):
        if not isinstance(ln, dict) or not ln.get("description"):
            raise InputError(f"lines[{i-1}].description is required")
        q = _dec(ln.get("quantity", 1), f"lines[{i-1}].quantity")
        price = _dec(ln.get("unit_price"), f"lines[{i-1}].unit_price")
        if price < 0:
            raise InputError(f"lines[{i-1}].unit_price must not be negative (use a negative quantity)")
        cat = (ln.get("vat_category") or "S").upper()
        if cat == "S" and ln.get("vat_rate") is None:
            raise InputError(f"lines[{i-1}].vat_rate is required for VAT category S")
        rate = _dec(ln.get("vat_rate") or 0, f"lines[{i-1}].vat_rate")
        net = (q * price).quantize(D2, ROUND_HALF_UP)
        total += net
        key = (cat, rate.normalize() if cat != "O" else Decimal(0))
        groups[key] = groups.get(key, Decimal(0)) + net
        unit = ln.get("unit") or "C62"
        qtag = "cbc:CreditedQuantity" if is_cn else "cbc:InvoicedQuantity"
        ltag = "cac:CreditNoteLine" if is_cn else "cac:InvoiceLine"
        item = f"<cac:Item>"
        if ln.get("item_description"):
            item += f"<cbc:Description>{_x(ln['item_description'])}</cbc:Description>"
        item += f"<cbc:Name>{_x(ln['description'][:200])}</cbc:Name>"
        if ln.get("seller_item_id"):
            item += f"<cac:SellersItemIdentification><cbc:ID>{_x(ln['seller_item_id'])}</cbc:ID></cac:SellersItemIdentification>"
        item += _taxcat(cat, rate) + "</cac:Item>"
        lines_xml.append(f"<{ltag}><cbc:ID>{i}</cbc:ID><{qtag} unitCode=\"{_x(unit)}\">{_num(q)}</{qtag}>"
                         f"<cbc:LineExtensionAmount currencyID=\"{cur}\">{_a(net)}</cbc:LineExtensionAmount>{item}"
                         f"<cac:Price><cbc:PriceAmount currencyID=\"{cur}\">{_num(price)}</cbc:PriceAmount></cac:Price></{ltag}>")
    subt, vat_total = [], Decimal(0)
    for (cat, rate), base in groups.items():
        tax = Decimal(0) if cat in ("O", "E", "AE", "K", "G", "Z") else (base * rate / 100).quantize(D2, ROUND_HALF_UP)
        vat_total += tax
        s = f"<cac:TaxSubtotal><cbc:TaxableAmount currencyID=\"{cur}\">{_a(base)}</cbc:TaxableAmount><cbc:TaxAmount currencyID=\"{cur}\">{_a(tax)}</cbc:TaxAmount><cac:TaxCategory><cbc:ID>{cat}</cbc:ID>"
        if cat != "O":
            s += f"<cbc:Percent>{_num(rate)}</cbc:Percent>"
        if cat in EXEMPT_DEFAULTS:
            code, text = EXEMPT_DEFAULTS[cat]
            code = (j.get("vat_exemption_code") or {}).get(cat, code) if isinstance(j.get("vat_exemption_code"), dict) else code
            text = (j.get("vat_exemption_reason") or {}).get(cat, text) if isinstance(j.get("vat_exemption_reason"), dict) else text
            if code:
                s += f"<cbc:TaxExemptionReasonCode>{_x(code)}</cbc:TaxExemptionReasonCode>"
            s += f"<cbc:TaxExemptionReason>{_x(text)}</cbc:TaxExemptionReason>"
        s += "<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal>"
        subt.append(s)
    incl = total + vat_total
    prepaid = _dec(j.get("prepaid_amount", 0), "prepaid_amount")
    payable = incl - prepaid
    cid = {"en16931": EN16931_ID, "xrechnung": XRECHNUNG_ID, "peppol-bis3": PEPPOL_ID}[fmt]
    root_tag, root_ns = ("CreditNote", NS["ubl_cn"]) if is_cn else ("Invoice", NS["ubl_inv"])
    x = [f'<?xml version="1.0" encoding="UTF-8"?>\n<{root_tag} xmlns="{root_ns}" xmlns:cac="{NS["cac"]}" xmlns:cbc="{NS["cbc"]}">',
         f"<cbc:CustomizationID>{cid}</cbc:CustomizationID>"]
    if fmt in ("peppol-bis3", "xrechnung"):
        x.append("<cbc:ProfileID>urn:fdc:peppol.eu:2017:poacc:billing:01:1.0</cbc:ProfileID>")
    x.append(f"<cbc:ID>{_x(j['invoice_number'])}</cbc:ID><cbc:IssueDate>{_x(j['issue_date'])}</cbc:IssueDate>")
    if j.get("due_date") and not is_cn:
        x.append(f"<cbc:DueDate>{_x(j['due_date'])}</cbc:DueDate>")
    x.append(f"<cbc:{'CreditNoteTypeCode' if is_cn else 'InvoiceTypeCode'}>{_x(j.get('type_code', '381' if is_cn else '380'))}</cbc:{'CreditNoteTypeCode' if is_cn else 'InvoiceTypeCode'}>")
    if j.get("note"):
        x.append(f"<cbc:Note>{_x(j['note'])}</cbc:Note>")
    x.append(f"<cbc:DocumentCurrencyCode>{cur}</cbc:DocumentCurrencyCode>")
    bref = j.get("buyer_reference") or (None if j.get("order_reference") or fmt == "en16931" else j["invoice_number"] if fmt == "peppol-bis3" else None)
    if bref:
        x.append(f"<cbc:BuyerReference>{_x(bref)}</cbc:BuyerReference>")
    if j.get("order_reference"):
        x.append(f"<cac:OrderReference><cbc:ID>{_x(j['order_reference'])}</cbc:ID></cac:OrderReference>")
    if j.get("preceding_invoice"):
        x.append(f"<cac:BillingReference><cac:InvoiceDocumentReference><cbc:ID>{_x(j['preceding_invoice'])}</cbc:ID></cac:InvoiceDocumentReference></cac:BillingReference>")
    x.append(f"<cac:AccountingSupplierParty>{_party(j['seller'], 'seller', fmt, cur)}</cac:AccountingSupplierParty>")
    x.append(f"<cac:AccountingCustomerParty>{_party(j['buyer'], 'buyer', fmt, cur)}</cac:AccountingCustomerParty>")
    if j.get("delivery_date"):
        x.append(f"<cac:Delivery><cbc:ActualDeliveryDate>{_x(j['delivery_date'])}</cbc:ActualDeliveryDate></cac:Delivery>")
    pay = j.get("payment") or {}
    if pay.get("iban") or pay.get("means_code"):
        pm = f"<cac:PaymentMeans><cbc:PaymentMeansCode>{_x(pay.get('means_code', '58' if pay.get('iban') else '1'))}</cbc:PaymentMeansCode>"
        if pay.get("reference"):
            pm += f"<cbc:PaymentID>{_x(pay['reference'])}</cbc:PaymentID>"
        if pay.get("iban"):
            pm += f"<cac:PayeeFinancialAccount><cbc:ID>{_x(pay['iban'].replace(' ', ''))}</cbc:ID>"
            if pay.get("account_name"):
                pm += f"<cbc:Name>{_x(pay['account_name'])}</cbc:Name>"
            if pay.get("bic"):
                pm += f"<cac:FinancialInstitutionBranch><cbc:ID>{_x(pay['bic'])}</cbc:ID></cac:FinancialInstitutionBranch>"
            pm += "</cac:PayeeFinancialAccount>"
        x.append(pm + "</cac:PaymentMeans>")
    if pay.get("terms"):
        x.append(f"<cac:PaymentTerms><cbc:Note>{_x(pay['terms'])}</cbc:Note></cac:PaymentTerms>")
    x.append(f"<cac:TaxTotal><cbc:TaxAmount currencyID=\"{cur}\">{_a(vat_total)}</cbc:TaxAmount>{''.join(subt)}</cac:TaxTotal>")
    x.append(f"<cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID=\"{cur}\">{_a(total)}</cbc:LineExtensionAmount>"
             f"<cbc:TaxExclusiveAmount currencyID=\"{cur}\">{_a(total)}</cbc:TaxExclusiveAmount>"
             f"<cbc:TaxInclusiveAmount currencyID=\"{cur}\">{_a(incl)}</cbc:TaxInclusiveAmount>"
             + (f"<cbc:PrepaidAmount currencyID=\"{cur}\">{_a(prepaid)}</cbc:PrepaidAmount>" if prepaid else "")
             + f"<cbc:PayableAmount currencyID=\"{cur}\">{_a(payable)}</cbc:PayableAmount></cac:LegalMonetaryTotal>")
    x += lines_xml
    x.append(f"</{root_tag}>")
    totals = {"currency": cur, "line_total": _a(total), "vat_total": _a(vat_total), "total_with_vat": _a(incl), "payable": _a(payable)}
    return "".join(x), totals


def create(j: dict) -> dict:
    xml, totals = build_ubl(j)
    fmt = (j.get("format") or "en16931").lower()
    v = validate(xml.encode("utf-8"), "xrechnung" if fmt == "xrechnung" else "en16931")
    notes = []
    if fmt == "peppol-bis3":
        notes.append("Built to the Peppol BIS Billing 3.0 structure and checked against EN 16931 only; Peppol-specific rules are not run yet.")
    return {"valid": v["valid"], "format": fmt, "syntax": "UBL 2.1", "document_type": v["document_type"],
            "totals": totals, "xml": xml, "errors": v["errors"], "warnings": v["warnings"], "checked": v["checked"],
            "notes": notes, "disclaimer": DISCLAIMER}
