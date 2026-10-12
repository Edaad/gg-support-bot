"""MCP (Model Context Protocol) server exposing dashboard data to AI clients.

Served from the FastAPI web dyno at ``/mcp`` (Streamable HTTP, bearer token) and
locally over stdio via ``run_mcp.py``. See ``docs/MCP.md``.
"""
