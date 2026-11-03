"""Regression (2026-11-03): verify_eori's argument 'eori' shadowed the eori module -> "'str' object has no attribute 'validate_one'"."""
import asyncio
from vatcheck import mcp_server, eori as eori_module


def test_verify_eori_tool_calls_module(monkeypatch):
    seen = {}

    async def fake_one(x):
        seen["one"] = x
        return {"eori": x, "status": "valid", "valid": True}

    async def fake_many(xs):
        seen["many"] = list(xs)
        return [{"eori": x, "status": "valid", "valid": True} for x in xs]

    monkeypatch.setattr(eori_module, "validate_one", fake_one)
    monkeypatch.setattr(eori_module, "validate_many", fake_many)
    assert asyncio.run(mcp_server.verify_eori("SE5560360793"))["valid"] is True
    assert seen["one"] == "SE5560360793"
    assert len(asyncio.run(mcp_server.verify_eori_batch(["SE5560360793", "IE6388047V"]))) == 2


def test_mcp_tool_args_do_not_shadow_used_modules():
    import inspect, re
    import types
    src = inspect.getsource(mcp_server)
    modules = {n for n, v in vars(mcp_server).items() if isinstance(v, types.ModuleType)}
    modules |= set(re.findall(r"from \. import (\w+)", src))
    for m in re.finditer(r"async def (\w+)\(([^)]*)\)[^:]*:(.*?)(?=\n@mcp\.tool|\Z)", src, re.S):
        args = [a.split(":")[0].strip() for a in m.group(2).split(",") if a.strip()]
        for a in (x for x in args if x in modules):
            assert not re.search(rf"\b{a}\.\w+\(", m.group(3)), f"{m.group(1)}: argument '{a}' is used like a module"
