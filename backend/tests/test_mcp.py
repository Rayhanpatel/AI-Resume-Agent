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


def test_mcp_calls_count_toward_the_daily_limits():
    from core.mcp_limits import MCPLimits
    from services.quotas import QuotaService

    sub = mcp_server.build_asgi_app()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp_server.mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.quotas = QuotaService(None, messages_per_session=2, messages_per_ip_per_day=99,
                                    sessions_per_ip_per_day=99, daily_token_budget=10_000, salt="s")
    app.mount("/mcp", MCPLimits(sub, app, requests_per_minute=99))
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": ENVELOPE}}
    with TestClient(app) as client:
        for _ in range(2):
            assert client.post("/mcp/", headers={**HEADERS, "mcp-method": "tools/list"}, json=call).status_code == 200
        assert client.post("/mcp/", headers={**HEADERS, "mcp-method": "tools/list"}, json=call).status_code == 429

        app.state.quotas.messages_per_session = 99
        app.state.quotas.record_memory("tokens", "all", 10_000)  # global budget spent
        assert client.post("/mcp/", headers={**HEADERS, "mcp-method": "tools/list"}, json=call).status_code == 429


def test_mcp_answers_are_traced_like_web_chats():
    import asyncio
    from types import SimpleNamespace

    events = []

    class _Tracer:
        def trace(self, **kwargs):
            events.append(("start", kwargs["name"], kwargs["tags"]))
            return "handle"

        def end_trace(self, trace, **kwargs):
            events.append(("end", trace, kwargs.get("error")))

    class _Agent:
        def __init__(self, fail=False, expect="handle"):
            self.fail, self.expect = fail, expect

        async def respond(self, **kwargs):
            assert kwargs["trace"] == self.expect
            if self.fail:
                raise RuntimeError("model down")
            return {"response": "ok"}

    state = SimpleNamespace(tracing_service=_Tracer(), agent_service=_Agent())
    tracer, trace, result = asyncio.run(mcp_server._respond_traced(state, "mcp_ask", "q", session_id="mcp", query="q",
                                                                    user_name="an agent"))
    assert events[0] == ("start", "mcp_ask", ["mcp"]) and trace == "handle" and result["response"] == "ok"

    state.agent_service = _Agent(fail=True)
    try:
        asyncio.run(mcp_server._respond_traced(state, "mcp_ask", "q", session_id="mcp", query="q", user_name="an agent"))
    except RuntimeError:
        pass
    assert events[-1] == ("end", "handle", "RuntimeError")  # a failed call still closes its trace

    no_tracing = SimpleNamespace(agent_service=_Agent(expect=None))
    assert asyncio.run(mcp_server._respond_traced(no_tracing, "mcp_ask", "q", session_id="mcp", query="q",
                                                  user_name="an agent"))[0] is None
