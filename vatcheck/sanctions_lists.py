"""Download + normalise official sanctions lists into one compact index.

Sources (official, free, public):
  EU  Consolidated Financial Sanctions List (European Commission, FSF)
  UN  Security Council Consolidated List
  OFAC Specially Designated Nationals (SDN) list + aliases (US Treasury)
  UK  UK Sanctions List (FCDO)
"""
import csv, gzip, io, json, os, re, time, xml.etree.ElementTree as ET
from collections import defaultdict
import httpx

URLS = {
    "EU": "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
    "UN": "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
    "OFAC_SDN": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV",
    "OFAC_ALT": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV",
    "UK": "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv",
}
LIST_NAMES = {"EU": "EU Consolidated Financial Sanctions List", "UN": "UN Security Council Consolidated List",
              "OFAC": "OFAC SDN List (US Treasury)", "UK": "UK Sanctions List (FCDO)"}
INDEX_PATH = os.getenv("SANCTIONS_INDEX", "/tmp/sanctions-index.json.gz")


def _ent(lst, uid, etype):
    return {"list": lst, "id": uid, "type": etype, "name": None, "aliases": [], "programs": [], "dob": [],
            "nationality": [], "listed_on": None, "remarks": None}


def _add(lst, v):
    v = (v or "").strip()
    if v and v != "-0-" and v not in lst: lst.append(v)


def parse_eu(text):
    ents = {}
    for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff")), delimiter=";"):
        uid = r["Entity_LogicalId"]
        e = ents.get(uid)
        if not e:
            t = {"person": "person", "enterprise": "entity"}.get(r["Entity_SubjectType_ClassificationCode"], r["Entity_SubjectType_ClassificationCode"] or "entity")
            e = ents[uid] = _ent("EU", r["Entity_EU_ReferenceNumber"] or uid, t)
            e["listed_on"] = r["Entity_Regulation_PublicationDate"] or None
            e["remarks"] = (r["Entity_Remark"] or None)
        n = r["NameAlias_WholeName"]
        if n:
            if not e["name"]: e["name"] = n.strip()
            else: _add(e["aliases"], n)
        _add(e["programs"], r["Entity_Regulation_Programme"])
        _add(e["dob"], r["BirthDate_BirthDate"] or r["BirthDate_Year"])
        _add(e["nationality"], r["Citizenship_CountryDescription"])
    return [e for e in ents.values() if e["name"]]


def parse_un(xml):
    root = ET.fromstring(xml); out = []
    for tag, typ in (("INDIVIDUAL", "person"), ("ENTITY", "entity")):
        for x in root.iter(tag):
            g = lambda k: (x.findtext(k) or "").strip()
            e = _ent("UN", g("REFERENCE_NUMBER") or g("DATAID"), typ)
            e["name"] = " ".join(p for p in (g("FIRST_NAME"), g("SECOND_NAME"), g("THIRD_NAME"), g("FOURTH_NAME")) if p)
            for a in x.iter(f"{tag}_ALIAS"): _add(e["aliases"], a.findtext("ALIAS_NAME"))
            _add(e["programs"], g("UN_LIST_TYPE"))
            for d in x.iter("INDIVIDUAL_DATE_OF_BIRTH"): _add(e["dob"], d.findtext("DATE") or d.findtext("YEAR"))
            for n in x.findall("NATIONALITY/VALUE"): _add(e["nationality"], n.text)
            e["listed_on"] = g("LISTED_ON") or None
            e["remarks"] = (g("COMMENTS1") or None) and g("COMMENTS1")[:500]
            if e["name"]: out.append(e)
    return out


def parse_ofac(sdn, alt):
    ents = {}
    for r in csv.reader(io.StringIO(sdn)):
        if len(r) < 12: continue
        e = _ent("OFAC", r[0], "person" if r[2].strip() == "individual" else ("vessel" if r[2].strip() == "vessel" else ("aircraft" if r[2].strip() == "aircraft" else "entity")))
        e["name"] = r[1].strip()
        for p in re.split(r"\]\s*\[|[\[\]]", r[3]): _add(e["programs"], p)
        rem = r[11].strip()
        if rem != "-0-":
            e["remarks"] = rem[:500]
            for m in re.findall(r"DOB (\d{1,2} \w{3} \d{4}|\w{3} \d{4}|\d{4})", rem): _add(e["dob"], m)
            for m in re.findall(r"nationality ([A-Z][\w ]+?)[;.]", rem): _add(e["nationality"], m)
        ents[r[0]] = e
    for r in csv.reader(io.StringIO(alt)):
        if len(r) >= 4 and r[0] in ents: _add(ents[r[0]]["aliases"], r[3])
    return list(ents.values())


def parse_uk(text):
    lines = text.splitlines(True)
    start = next(i for i, l in enumerate(lines) if l.startswith("Last Updated"))
    ents = {}
    for r in csv.DictReader(io.StringIO("".join(lines[start:]))):
        uid = r["Unique ID"]
        n = " ".join(p for p in (r["Name 1"], r["Name 2"], r["Name 3"], r["Name 4"], r["Name 5"], r["Name 6"]) if p and p.strip()).strip()
        e = ents.get(uid)
        if not e:
            t = (r["Designation Type"] or "").lower()
            e = ents[uid] = _ent("UK", uid, "person" if t.startswith("indiv") else ("vessel" if t.startswith("ship") else "entity"))
            e["listed_on"] = r["Date Designated"] or None
        if n:
            if r["Name type"].lower().startswith("primary") and not e["name"]: e["name"] = n
            else: _add(e["aliases"], n)
        _add(e["aliases"], r["Name non-latin script"])
        _add(e["programs"], r["Regime Name"]); _add(e["dob"], r["D.O.B"])
        for nat in re.split(r"[,;]\s*|\(\d+\)\s*", r["Nationality(/ies)"] or ""): _add(e["nationality"], nat)
    out = []
    for e in ents.values():
        if not e["name"] and e["aliases"]: e["name"] = e["aliases"].pop(0)
        if e["name"]: out.append(e)
    return out


def download_all(timeout=180):
    raw, errors = {}, {}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": "sanctions-screening/1.0"}) as c:
        for k, u in URLS.items():
            for i in range(3):
                try:
                    r = c.get(u); r.raise_for_status(); raw[k] = r.content.decode("utf-8", "replace"); break
                except Exception as ex:  # noqa
                    errors[k] = str(ex)[:200]; time.sleep(3 * (i + 1))
            else:
                continue
            errors.pop(k, None)
    ents, meta = [], {}
    for lst, fn in (("EU", lambda: parse_eu(raw["EU"])), ("UN", lambda: parse_un(raw["UN"])),
                    ("OFAC", lambda: parse_ofac(raw["OFAC_SDN"], raw.get("OFAC_ALT", ""))), ("UK", lambda: parse_uk(raw["UK"]))):
        try:
            got = fn(); ents += got; meta[lst] = {"entries": len(got), "name": LIST_NAMES[lst]}
        except Exception as ex:  # noqa
            meta[lst] = {"entries": 0, "error": errors.get(lst) or f"{type(ex).__name__}: {ex}"[:200], "name": LIST_NAMES[lst]}
    return {"built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "lists": meta, "entities": ents}


def save(index, path=INDEX_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f: json.dump(index, f, ensure_ascii=False)


def load(path=INDEX_PATH):
    with gzip.open(path, "rt", encoding="utf-8") as f: return json.load(f)


def age_hours(index):
    t = time.mktime(time.strptime(index["built_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    return (time.time() - t) / 3600


if __name__ == "__main__":
    ix = download_all(); save(ix); print(json.dumps(ix["lists"]), ix["built_at"])
