"""
Langfuse v4 tracing (Python SDK v5): one root observation per chat turn carries the
visitor's message and the answer; generations are its children and carry the same
session, user, tags and version. Spans go to an in-memory exporter, never to Langfuse.
"""
import json
import uuid

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from core import telemetry
from services.tracer import TracingService, visitor_id, trace_tags, langfuse_environment
from test_telemetry import otel  # noqa: F401 (the in-memory telemetry fixture)

ROOT_IO = ("langfuse.observation.input", "langfuse.observation.output")


def _value(raw):
    """Strings are stored as is; other values (and metadata values) are JSON-encoded"""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


@pytest.fixture
def langfuse():
    """A real Langfuse client whose spans land in memory"""
    spans = InMemorySpanExporter()
    # The SDK keeps one client per public key; a fresh key per test avoids reusing a shut-down one
    service = TracingService(f"pk-lf-test-{uuid.uuid4().hex[:8]}", "sk-lf-test", host="http://127.0.0.1:9",
                             environment="Production", release="2.7.0", version="abc123",
                             span_exporter=spans)
    assert service.enabled

    def finished():
        service.flush()
        return {s.name: s for s in spans.get_finished_spans()}

    yield service, finished
    service.shutdown()


def _turn(service, **trace_kwargs):
    handle = service.trace("stream_chat", session_id="sess-1", user_id=visitor_id("a" * 24),
                           input="What did he do at 3E?", tags=trace_tags("web", stream=True), **trace_kwargs)
    service.log_generation(handle, name="agent_response_stream", model="gemini-3.5-flash",
                           input_text="What did he do at 3E?", output_text="Agent memory.",
                           tokens_in=1200, tokens_out=40, latency_ms=900, metadata={"fallback": False})
    service.end_trace(handle, output="Agent memory.", metadata={"checked": 2})
    return handle


def test_root_observation_holds_the_turns_input_and_output(langfuse):
    service, finished = langfuse
    _turn(service)
    spans = finished()
    root = spans["stream_chat"]
    assert root.parent is None
    assert _value(root.attributes[ROOT_IO[0]]) == "What did he do at 3E?"
    assert _value(root.attributes[ROOT_IO[1]]) == "Agent memory."


def test_generation_is_a_child_and_inherits_the_propagated_attributes(langfuse):
    service, finished = langfuse
    _turn(service)
    spans = finished()
    root, gen = spans["stream_chat"], spans["agent_response_stream"]
    assert gen.parent is not None and gen.parent.span_id == root.context.span_id
    assert gen.context.trace_id == root.context.trace_id
    assert gen.attributes["langfuse.observation.type"] == "generation"
    for span in (root, gen):
        assert span.attributes["session.id"] == "sess-1"
        assert span.attributes["user.id"] == "visitor-" + "a" * 16
        assert span.attributes["langfuse.trace.name"] == "stream_chat"
        assert set(span.attributes["langfuse.trace.tags"]) == {"web", "stream"}
        assert span.attributes["langfuse.version"] == "abc123"
        assert span.attributes["langfuse.environment"] == "production"
        assert span.attributes["langfuse.release"] == "2.7.0"


def test_end_trace_is_idempotent_and_marks_errors(langfuse):
    service, finished = langfuse
    handle = service.trace("chat_message", session_id="s", input="hi")
    service.end_trace(handle, output=None, error="client disconnected")
    service.end_trace(handle, output="late")  # the stream's finally block: no second end
    root = finished()["chat_message"]
    assert root.attributes["langfuse.observation.level"] == "ERROR"
    assert ROOT_IO[1] not in root.attributes or _value(root.attributes[ROOT_IO[1]]) is None


def test_propagated_metadata_is_short_strings(langfuse):
    service, finished = langfuse
    handle = service.trace("chat_message", session_id="s", metadata={"company": "A" * 500, "n": 3, "none": None})
    service.end_trace(handle, output="x")
    attrs = finished()["chat_message"].attributes
    company = json.loads(attrs["langfuse.trace.metadata.company"])
    assert company == "A" * 150 and json.loads(attrs["langfuse.trace.metadata.n"]) == "3"
    assert "langfuse.trace.metadata.none" not in attrs


def test_visitor_name_never_becomes_the_user_id():
    assert visitor_id("0123456789abcdef0123") == "visitor-0123456789abcdef"
    assert visitor_id(None) is None


def test_environment_is_sanitised_for_langfuse():
    assert langfuse_environment("Production") == "production"
    assert langfuse_environment("my env!") == "my-env-"
    assert langfuse_environment("langfuse-x") is None and langfuse_environment("") is None


def test_off_without_keys():
    service = TracingService("", "")
    assert not service.enabled and service.client is None
    assert service.trace("chat_message", session_id="s", input="hi") is None
    assert service.log_generation(None, "n", "m", "in", "out")["logged"] is False
    service.end_trace(None, output="x")
    service.shutdown()


def test_langfuse_does_not_take_the_global_tracer_provider(langfuse):
    assert isinstance(otel_trace.get_tracer_provider(), otel_trace.ProxyTracerProvider)


def test_coexists_with_telemetry_without_sharing_spans(langfuse, otel):
    """A Langfuse turn inside a telemetry span stays its own trace, and neither exporter sees the other's spans"""
    service, finished = langfuse
    telemetry_spans, _ = otel
    with telemetry.gen_ai_span("gemini-3.5-flash", "answer"):
        _turn(service)
    langfuse_spans, grafana_spans = finished(), telemetry_spans()
    assert langfuse_spans["stream_chat"].parent is None
    assert {s.name for s in grafana_spans} == {"chat gemini-3.5-flash"}
    assert all(s.instrumentation_scope.name == "langfuse-sdk" for s in langfuse_spans.values())
    # Langfuse's session and user attributes never land on the telemetry span
    assert "session.id" not in grafana_spans[0].attributes and "user.id" not in grafana_spans[0].attributes


# ── The stream route: one root per turn, ended even when the visitor leaves ──

class _OnTopic:
    async def classify(self, query, previous=None):
        return {"intent": "job_related"}


class _Agent:
    """Streams two chunks and logs the generation the way AgentService does"""
    model = "gemini-3.5-flash"

    def __init__(self, tracer):
        self.tracer = tracer

    async def respond_stream(self, session_id, query, user_name, company=None, job_info=None,
                             trace=None, history=None, stats=None):
        for chunk in ("Agent ", "memory."):
            yield chunk
        stats.update({"model": self.model, "tokens_in": 1000, "tokens_out": 20, "response": "Agent memory."})
        self.tracer.log_generation(trace, name="agent_response_stream", model=self.model, input_text=query,
                                   output_text="Agent memory.", tokens_in=1000, tokens_out=20)


def _stream_app(service, monkeypatch):
    from fastapi import FastAPI
    from api.routes import chat
    from core.session_tokens import SessionTokens
    from core.ttl_cache import TTLCache
    from middleware.rate_limiter import RateLimiter
    from services.quotas import QuotaService

    async def no_verify(request, answer):
        return None
    monkeypatch.setattr(chat, "_verify", no_verify)

    app = FastAPI()
    app.include_router(chat.router, prefix="/api/v1")
    app.state.session_tokens = SessionTokens("secret")
    app.state.sessions = TTLCache(ttl_seconds=60, max_size=10)
    app.state.supabase_service = type("S", (), {"enabled": False})()
    app.state.quotas = QuotaService(None, messages_per_session=9, messages_per_ip_per_day=9,
                                    sessions_per_ip_per_day=9, daily_token_budget=10**9, salt="s")
    app.dependency_overrides[chat.get_rate_limiter] = lambda: RateLimiter(requests_per_minute=100)
    app.dependency_overrides[chat.get_supabase_service] = lambda: app.state.supabase_service
    app.dependency_overrides[chat.get_intent_classifier] = lambda: _OnTopic()
    app.dependency_overrides[chat.get_tracing_service] = lambda: service
    app.dependency_overrides[chat.get_agent_service] = lambda: _Agent(service)
    sid = str(uuid.uuid4())
    app.state.sessions[sid] = {"id": sid, "user_name": "Jane Recruiter", "preferences": {"ip_hash": "f" * 24}}
    body = {"session_id": sid, "session_token": app.state.session_tokens.issue(sid), "query": "What did he do at 3E?"}
    return app, sid, body


def test_stream_route_traces_the_turn_without_the_visitors_name(langfuse, monkeypatch):
    from fastapi.testclient import TestClient
    service, finished = langfuse
    app, sid, body = _stream_app(service, monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/v1/chat/stream", json=body)
    assert r.status_code == 200 and "[DONE]" in r.text
    spans = finished()
    root, gen = spans["stream_chat"], spans["agent_response_stream"]
    assert _value(root.attributes[ROOT_IO[0]]) == "What did he do at 3E?"
    assert _value(root.attributes[ROOT_IO[1]]) == "Agent memory."
    assert gen.parent.span_id == root.context.span_id
    for span in (root, gen):
        assert span.attributes["session.id"] == sid
        assert span.attributes["user.id"] == "visitor-" + "f" * 16
    assert not any("Jane Recruiter" in str(v) for s in spans.values() for v in s.attributes.values())


def test_stream_root_is_ended_when_the_visitor_leaves_mid_answer(langfuse, monkeypatch):
    import asyncio
    from api.routes import chat
    service, finished = langfuse
    app, sid, body = _stream_app(service, monkeypatch)
    captured = {}
    real_trace = service.trace

    def spy(*args, **kwargs):
        captured["handle"] = real_trace(*args, **kwargs)
        return captured["handle"]
    service.trace = spy

    async def leave_after_first_chunk():
        from starlette.requests import Request
        from models.schemas import ChatRequest
        request = Request({"type": "http", "method": "POST", "path": "/api/v1/chat/stream", "headers": [],
                           "client": ("1.2.3.4", 1), "app": app, "query_string": b""})
        response = await chat.stream_chat(request, ChatRequest(**body), background_tasks=None,
                                          agent_service=_Agent(service), intent_classifier=_OnTopic(),
                                          tracing_service=service, supabase_service=app.state.supabase_service,
                                          rate_limiter=app.dependency_overrides[chat.get_rate_limiter]())
        stream = response.body_iterator
        await stream.__anext__()
        await stream.aclose()  # what Starlette does when the client disconnects

    asyncio.run(leave_after_first_chunk())
    assert captured["handle"].ended
    root = finished()["stream_chat"]
    assert root.attributes["langfuse.observation.level"] == "ERROR"
