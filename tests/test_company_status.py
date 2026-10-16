"""Offline unit tests for status_normalized mapping (fixtures shaped like real API payloads)."""
from vatcheck.company import (norm_fr, norm_no, norm_fi, norm_cz, norm_ee, norm_gleif, pl_flags, _rec, _fr, _no, _fi, _cz, _lei,
                           STATUS_VALUES)


def test_rec_defaults():
    r = _rec("XX", "1", "n")
    assert r["status_normalized"] == "unknown" and r["status_raw"] is None and "active" in r and "status" in r


def test_fr():
    assert norm_fr("A") == "active" and norm_fr("C") == "dissolved" and norm_fr(None) == "unknown"
    r = _fr({"siren": "775758493", "etat_administratif": "C", "date_fermeture": "1987-02-11"})
    assert r["status_normalized"] == "dissolved" and r["active"] is False and r["status"] == "C"
    assert r["status_raw"]["etat_administratif"] == "C"


def test_no():
    b = {"organisasjonsnummer": "935567645", "konkurs": True}
    assert norm_no(b) == "bankrupt"
    assert norm_no({"organisasjonsnummer": "1", "underAvvikling": True}) == "in_liquidation"
    assert norm_no({"organisasjonsnummer": "1", "underTvangsavviklingEllerTvangsopplosning": True}) == "in_liquidation"
    assert norm_no({"organisasjonsnummer": "1", "slettedato": "2020-01-01", "konkurs": True}) == "dissolved"
    assert norm_no({"organisasjonsnummer": "1", "konkurs": False}) == "active"
    r = _no(b); assert r["status_raw"]["konkurs"] is True and r["active"] is False


def test_fi():
    base = {"businessId": {"value": "0105409-4"}}
    assert norm_fi({**base, "companySituations": [{"type": "KONK"}]}) == "bankrupt"
    assert norm_fi({**base, "companySituations": [{"type": "SELTILA"}]}) == "in_liquidation"
    assert norm_fi({**base, "companySituations": [{"type": "SANE"}]}) == "active"
    assert norm_fi({**base, "companySituations": [{"type": "KONK", "endDate": "2000-01-01"}]}) == "active"
    assert norm_fi({**base, "endDate": "2001-06-15"}) == "dissolved"
    r = _fi({**base, "companySituations": [{"type": "SANE"}]})
    assert r["status_raw"]["reorganisation"] is True and r["status"] == "Company re-organisation"


def test_cz():
    reg = lambda vr="AKTIVNI", ir="NEEXISTUJICI": {"stavZdrojeVr": vr, "stavZdrojeRos": vr, "stavZdrojeIr": ir}
    assert norm_cz({"obchodniJmeno": "Škoda Auto a.s.", "seznamRegistraci": reg()}) == "active"
    assert norm_cz({"datumZaniku": "2020-01-01", "seznamRegistraci": reg()}) == "dissolved"
    assert norm_cz({"obchodniJmeno": "X s.r.o. v likvidaci", "seznamRegistraci": reg("ZANIKLY")}) == "dissolved"
    assert norm_cz({"obchodniJmeno": "X a.s. v likvidaci", "seznamRegistraci": reg(ir="AKTIVNI")}) == "bankrupt"
    assert norm_cz({"obchodniJmeno": "X s.r.o., v likvidaci", "seznamRegistraci": reg()}) == "in_liquidation"
    assert norm_cz({"obchodniJmeno": "X"}) == "unknown"
    assert _cz({"ico": "1", "seznamRegistraci": reg(ir="AKTIVNI")})["status_raw"]["stavZdrojeIr"] == "AKTIVNI"


def test_pl():
    o = lambda name, d6=None, hdr=None: {"naglowekA": hdr or {}, "dane": {"dzial1": {"danePodmiotu": {"nazwa": name}}, "dzial6": d6 or {}}}
    assert pl_flags(o("ORLEN SPÓŁKA AKCYJNA"))[0] == "active"
    open_b = {"postepowanieUpadlosciowe": [{"informacjaOOgloszeniuUpadlosci": {"data": "20.07.2023"}, "opisZakonczeniaProcesuUpadlosci": {}}]}
    assert pl_flags(o("GETIN", open_b))[0] == "bankrupt"
    done_b = {"postepowanieUpadlosciowe": [{"informacjaOOgloszeniuUpadlosci": {"data": "12.09.2005"},
                                           "opisZakonczeniaProcesuUpadlosci": {"dataZakonczeniaPostepowania": "25.11.2005"}}]}
    assert pl_flags(o("SZOK", done_b))[0] == "active"
    empty_b = {"postepowanieUpadlosciowe": [{"informacjaOOgloszeniuUpadlosci": {}, "opisZakonczeniaProcesuUpadlosci": {}}]}
    assert pl_flags(o("FX", empty_b))[0] == "active"
    assert pl_flags(o("ABC SP. Z O.O. W LIKWIDACJI"))[0] == "in_liquidation"
    assert pl_flags(o("ABC", {"likwidacja": [{"likwidatorzy": [{"nazwisko": "X"}]}]}))[0] == "in_liquidation"
    assert pl_flags(o("ABC S.A. W UPADŁOŚCI"))[0] == "bankrupt"
    assert pl_flags(o("ABC", hdr={"dataWykresleniaZRejestru": "01.01.2020"}))[0] == "dissolved"


def test_ee_gleif():
    assert [norm_ee(c) for c in "RLNK"] == ["active", "in_liquidation", "bankrupt", "dissolved"] and norm_ee("?") == "unknown"
    assert norm_gleif("ACTIVE") == "active" and norm_gleif("INACTIVE") == "dissolved" and norm_gleif(None) == "unknown"
    x = {"attributes": {"lei": "L", "entity": {"status": "ACTIVE"}, "registration": {"status": "LAPSED"}}}
    r = _lei(x); assert r["status_normalized"] == "active" and r["status_raw"]["registration_status"] == "LAPSED"


def test_values_closed_set():
    assert set(STATUS_VALUES) == {"active", "in_liquidation", "bankrupt", "dissolved", "unknown"}
