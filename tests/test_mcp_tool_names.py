"""1.1.0: verb_noun tool names, hidden backward-compatible aliases, batch tools, README table == registered tools."""
import asyncio, re
from pathlib import Path
from vatcheck import mcp_server

EXPECTED = {"verify_vat", "verify_vat_batch", "lookup_company", "lookup_company_batch", "screen_sanctions",
            "screen_sanctions_batch", "check_kyb", "verify_eori", "verify_eori_batch", "validate_einvoice", "create_einvoice",
            "get_einvoice_route", "get_due_diligence_report"}
VERBS = {"verify", "lookup", "screen", "check", "validate", "create", "get"}


def _names():
    return {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}


def test_registered_tools_and_verb_noun():
    names = _names()
    assert names == EXPECTED
    for n in names:
        assert n.split("_")[0] in VERBS and "_" in n, n


def test_old_names_are_hidden_aliases():
    names = _names()
    for old, new in mcp_server.TOOL_ALIASES.items():
        assert old not in names and new in names
        assert mcp_server.mcp._tool_manager.get_tool(old) is mcp_server.mcp._tool_manager.get_tool(new)
    r = asyncio.run(mcp_server.mcp.call_tool("verify_batch", {"vats": ["XX123"]}))
    assert "invalid_format" in str(r)


def test_readme_table_matches_registered_tools():
    txt = (Path(mcp_server.__file__).parents[1] / "README.md").read_text()
    sec = txt[txt.index("### Tools ("):txt.index("## Quick answers")]
    n = int(re.search(r"### Tools \((\d+)\)", sec).group(1))
    rows = re.findall(r"^\| `([a-z_]+)` \|", sec, re.M)
    assert set(rows) == _names() and len(rows) == n == len(_names())


def test_disambiguation_in_descriptions():
    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert "get_due_diligence_report" in tools["check_kyb"].description
    assert "check_kyb" in tools["get_due_diligence_report"].description
    assert "check_kyb" in tools["get_einvoice_route"].description
    for t in tools.values():
        d = t.description
        assert "Input" in d and "Output" in d, t.name


def test_batch_tools(monkeypatch):
    seen = {}

    async def fake_many(items, limit=5, concurrency=5):
        seen["items"], seen["limit"] = items, limit
        return [{"query": i["query"], "status": "found"} for i in items]
    monkeypatch.setattr(mcp_server.company, "lookup_many", fake_many)
    r = asyncio.run(mcp_server.lookup_company_batch([{"query": "552032534", "country": "FR"}] * 30, limit=9))
    assert r["count"] == 25 and r["found"] == 25 and seen["limit"] == 5
    monkeypatch.setattr(mcp_server.sanctions, "screen_names",
                        lambda names, threshold=85, **kw: [{"query": n, "status": "no_match"} for n in names])
    r = asyncio.run(mcp_server.screen_sanctions_batch(["A"] * 60))
    assert r["count"] == 50 and r["potential_matches"] == 0
