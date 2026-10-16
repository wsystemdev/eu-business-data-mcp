"""Single public process: REST API at / and MCP streamable HTTP at /mcp."""
import contextlib
from .api import app
from .mcp_server import mcp

mcp.settings.streamable_http_path = "/"
_mcp_app = mcp.streamable_http_app()

@contextlib.asynccontextmanager
async def _lifespan(a):
    async with mcp.session_manager.run():
        yield

app.router.lifespan_context = _lifespan
app.mount("/mcp", _mcp_app)
