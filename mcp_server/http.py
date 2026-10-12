"""Build the ``/mcp`` HTTP endpoint for mounting inside the FastAPI web app.

Starlette's ``Mount`` would redirect ``/mcp`` to ``/mcp/``, so the SDK's ASGI handler
is attached as a plain ``Route`` instead. The parent app's lifespan must run
``mcp.session_manager.run()`` (see ``api/app.py``).
"""

from __future__ import annotations

from collections.abc import Mapping

from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from starlette.routing import Route

from mcp_server.auth import BearerAuthASGIMiddleware

MCP_HTTP_PATH = "/mcp"


def build_http_route(mcp: MCPServer, tokens: Mapping[str, str]) -> Route:
    """Create the session manager and return an authenticated ``Route``.

    Stateless JSON responses keep every call under Heroku's 30 s router limit and
    avoid long-lived SSE streams (cut after 55 s idle).
    """
    # Creates mcp.session_manager as a side effect; the returned Starlette app is
    # not used because its lifespan would not run when nested.
    mcp.streamable_http_app(
        streamable_http_path=MCP_HTTP_PATH,
        json_response=True,
        stateless_http=True,
        host="0.0.0.0",
    )
    endpoint = BearerAuthASGIMiddleware(
        StreamableHTTPASGIApp(mcp.session_manager), tokens
    )
    # POST only: stateless mode has no session to DELETE, and a GET would open a
    # standalone SSE stream that Heroku cuts after 55 s idle (405 is spec-allowed).
    return Route(MCP_HTTP_PATH, endpoint=endpoint, methods=["POST"], name="mcp")
