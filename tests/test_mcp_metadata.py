"""Every MCP tool has a title, MCP annotations and a description for every argument.
Directories score on this and clients use readOnlyHint; a new tool or argument without metadata fails here."""
import asyncio, re
from pathlib import Path
from vatcheck import mcp_server


def _tools():
    return asyncio.run(mcp_server.mcp.list_tools())


def test_every_tool_has_meta_entry():
    names = {t.name for t in _tools()}
    assert names <= set(mcp_server._TOOL_META), names - set(mcp_server._TOOL_META)


def test_titles_annotations_and_arg_descriptions():
    for t in _tools():
        assert t.title, t.name
        a = t.annotations
        assert a is not None and a.title == t.title, t.name
        assert a.readOnlyHint is True and a.destructiveHint is False and a.openWorldHint is not None, t.name
        props = t.inputSchema.get("properties", {})
        assert props, t.name
        for arg, p in props.items():
            assert p.get("description"), f"{t.name}.{arg} has no description"


def test_main_block_is_after_every_tool():
    """mcp.run() blocks: a tool defined below the __main__ block is missing in stdio/http mode."""
    src = Path(mcp_server.__file__).read_text()
    last_tool = max(m.start() for m in re.finditer(r"^@mcp\.tool", src, re.M))
    assert src.index('if __name__ == "__main__":') > last_tool
