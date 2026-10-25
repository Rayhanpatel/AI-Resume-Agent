"""
The MCP interface mounts on the app and answers a stateless tools/list.
Uses the real MCPServer with the real profile tool; the agent tools are not
attached (they need the running services).
"""
import contextlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

import mcp_server


def _app():
    sub = mcp_server.build_asgi_app()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp_server.mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", sub)
    return app


HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "MCP-Protocol-Version": "2026-07-28"}

# 2026-07-28 is stateless: there is no initialize handshake, so every request carries
# the protocol version and client capabilities in params._meta.
ENVELOPE = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}}


def test_tools_list_is_stateless_and_read_only():
    with TestClient(_app()) as client:
        # The transport also wants the method in an mcp-method header
        r = client.post("/mcp/", headers={**HEADERS, "mcp-method": "tools/list"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": ENVELOPE}})
        assert r.status_code == 200, r.text
        tools = r.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "get_profile" in names
        for t in tools:
            assert t["annotations"]["readOnlyHint"] is True

        # tools/call also wants the tool name in an mcp-name header (lets proxies route without parsing bodies)
        r2 = client.post("/mcp/", headers={**HEADERS, "mcp-method": "tools/call", "mcp-name": "get_profile"},
                         json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "get_profile", "arguments": {"section": "faq"}, "_meta": ENVELOPE}})
        assert r2.status_code == 200, r2.text
        body = r2.json()["result"]
        assert "OPT" in str(body)
        assert "+1 (" not in str(body)  # the profile holds no phone number
