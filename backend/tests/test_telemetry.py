"""
OpenTelemetry stays off without its environment variable; when on, the synthetic
tag, outcomes and gen_ai spans land where the dashboards and alerts expect them.
Exporters are in-memory here, so nothing leaves the machine.
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

from core import telemetry


@pytest.fixture
def otel():
    """Telemetry on with in-memory exporters; the module's previous state is restored afterwards"""
    saved = (telemetry._tracer, telemetry._meter, telemetry._m, dict(telemetry._state))
    spans, reader = InMemorySpanExporter(), InMemoryMetricReader()
    assert telemetry.setup("test", "test", span_exporter=spans, metric_reader=reader,
                           log_exporter=InMemoryLogRecordExporter())

    def finished_spans():
        telemetry._state["tracer_provider"].force_flush()
        return spans.get_finished_spans()

    def points(name):
        data = reader.get_metrics_data()
        out = []
        for rm in (data.resource_metrics if data else []):
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    if metric.name == name:
                        out += list(metric.data.data_points)
        return out

    yield finished_spans, points
    telemetry.shutdown()
    telemetry._tracer, telemetry._meter, telemetry._m = saved[:3]
    telemetry._state.clear(); telemetry._state.update(saved[3])


def _attrs(point):
    return dict(point.attributes)


# ── Off by default ───────────────────────────────────────────────────

def test_off_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert telemetry.setup("x", "y") is False
    assert not telemetry.enabled()
    with telemetry.gen_ai_span("gemini-3.5-flash", "answer") as span:
        assert not span.is_recording()  # a no-op span: nothing is created or exported
    telemetry.record_answer("ok", "gemini-3.5-flash")  # no-op, must not raise
    app = FastAPI()
    telemetry.instrument_app(app)
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)


# ── Pure helpers ─────────────────────────────────────────────────────

def test_outcome_classification():
    apology = "I apologize, but I'm having trouble responding right now."
    assert telemetry.answer_outcome("He interned at 3E.") == "ok"
    assert telemetry.answer_outcome(apology, "Gemini API Error 404", apology) == "apology"
    assert telemetry.answer_outcome(apology + "  ", None, apology) == "apology"
    assert telemetry.answer_outcome("", None) == "error"
    assert telemetry.answer_outcome(None, "boom") == "error"
    assert [telemetry.start_outcome(s) for s in (200, 403, 429, 422, 500, 302)] == \
        ["ok", "captcha_failed", "quota", "invalid", "error", "other"]


# ── With telemetry on ────────────────────────────────────────────────

def _tagged_app():
    app = FastAPI()

    @app.get("/api/v1/whoami")
    def whoami():
        telemetry.record_answer("ok", "gemini-3.5-flash")
        return {"synthetic": telemetry.is_synthetic(), "channel": telemetry.channel()}

    @app.get("/mcp/whoami")
    def mcp_whoami():
        return {"channel": telemetry.channel()}

    @app.post("/api/v1/session")
    def session(code: int = 200):
        from fastapi import HTTPException
        if code != 200:
            raise HTTPException(code, "nope")
        return {"ok": True}

    app.add_middleware(telemetry.TelemetryMiddleware)
    telemetry.instrument_app(app)
    return TestClient(app)


def test_synthetic_header_is_a_tag_on_spans_and_metrics(otel):
    finished_spans, points = otel
    client = _tagged_app()
    assert client.get("/api/v1/whoami", headers={"X-Synthetic-Check": "B1"}).json() == {"synthetic": True, "channel": "web"}
    assert client.get("/api/v1/whoami").json() == {"synthetic": False, "channel": "web"}
    assert client.get("/mcp/whoami").json() == {"channel": "mcp"}

    server = [s for s in finished_spans() if s.kind.name == "SERVER" and "whoami" in s.name]
    assert sorted(s.attributes.get("app.synthetic") for s in server) == [False, False, True]
    answers = points("chat.answers")
    assert {(_attrs(p)["app.synthetic"], p.value) for p in answers} == {(True, 1), (False, 1)}


def test_chat_starts_counted_by_outcome(otel):
    _, points = otel
    client = _tagged_app()
    for code in (200, 200, 403, 429):
        client.post(f"/api/v1/session?code={code}")
    counts = {_attrs(p)["outcome"]: p.value for p in points("chat.starts")}
    assert counts == {"ok": 2, "captcha_failed": 1, "quota": 1}


def test_gen_ai_span_records_usage_and_errors(otel):
    finished_spans, points = otel
    with telemetry.gen_ai_span("gemini-3.5-flash", "answer", attempt=1) as span:
        telemetry.record_usage(span, "gemini-3.5-flash", "answer",
                               {"promptTokenCount": 16000, "candidatesTokenCount": 300, "thoughtsTokenCount": 100})
    with pytest.raises(RuntimeError):
        with telemetry.gen_ai_span("gemini-3.5-flash-lite", "answer", attempt=2):
            raise RuntimeError("Gemini API Error 429")

    ok, failed = sorted(finished_spans(), key=lambda s: s.attributes["app.attempt"])
    assert ok.name == "chat gemini-3.5-flash"
    assert ok.attributes["gen_ai.provider.name"] == "gcp.vertex_ai"
    assert ok.attributes["gen_ai.usage.input_tokens"] == 16000
    assert ok.attributes["gen_ai.usage.output_tokens"] == 400  # thinking tokens are billed as output
    assert failed.attributes["error.type"] == "RuntimeError" and failed.status.status_code.name == "ERROR"

    tokens = {_attrs(p)["gen_ai.token.type"]: p.sum for p in points("gen_ai.client.token.usage")}
    assert tokens == {"input": 16000, "output": 400}
    durations = {_attrs(p).get("error.type") for p in points("gen_ai.client.operation.duration")}
    assert durations == {None, "RuntimeError"}


def test_langfuse_spans_never_reach_the_otlp_backend(otel):
    finished_spans, _ = otel
    provider = telemetry._state["tracer_provider"]
    provider.get_tracer(telemetry.LANGFUSE_SCOPE).start_span("generation with the visitor's message").end()
    provider.get_tracer("rayhan.agent").start_span("kept").end()
    assert [s.name for s in finished_spans()] == ["kept"]


def test_webhook_url_is_redacted():
    class _Span:
        def __init__(self): self.attributes = {}
        def is_recording(self): return True
        def set_attribute(self, k, v): self.attributes[k] = v

    class _Req:
        class url:
            host = "script.google.com"

    span = _Span()
    telemetry._redact_secret_urls(span, _Req())
    assert span.attributes["url.full"] == "https://script.google.com/macros/s/[redacted]/exec"


def test_stamps_and_quota_rejections_counted(otel):
    _, points = otel
    telemetry.record_stamps({"checked": 3, "inferred": 1, "unknown": 0, "numbers_unverified": 0, "unverified": 0})
    from services.quotas import QuotaService, QuotaExceeded
    q = QuotaService(None, messages_per_session=0, messages_per_ip_per_day=9, sessions_per_ip_per_day=9,
                     daily_token_budget=9, salt="s")
    import asyncio
    with pytest.raises(QuotaExceeded):
        asyncio.run(q.check_message("s", "ip"))
    assert {_attrs(p)["stamp"]: p.value for p in points("chat.verification.stamps")} == {"checked": 3, "inferred": 1}
    assert {_attrs(p)["scope"]: p.value for p in points("chat.quota.rejections")} == {"session": 1}


def test_database_gauge_follows_the_last_ping(otel):
    _, points = otel

    class _Db:
        last_ping = None

    db = _Db()
    telemetry.watch_database(db)
    assert points("chat.database.up") == []  # nothing reported before the first real check
    db.last_ping = "unreachable"
    assert [p.value for p in points("chat.database.up")] == [0]
    db.last_ping = "ok"
    assert [p.value for p in points("chat.database.up")] == [1]


# ── Probe traffic is not a lead ──────────────────────────────────────

def test_synthetic_session_skips_lead_capture(monkeypatch):
    import httpx
    from api.routes import session as session_route
    from core.session_tokens import SessionTokens
    from core.ttl_cache import TTLCache
    from middleware.rate_limiter import RateLimiter
    from services.job_parser import JobInfo
    from services.quotas import QuotaService

    leads = []

    async def fake_lead(*args, **kwargs):
        leads.append(kwargs.get("session_id"))

    async def fake_parse(*args, **kwargs):
        return JobInfo(company_name="Acme", role_title="ML Engineer", key_skills=["Python"])

    async def fake_prompts(*args, **kwargs):
        return ["q1", "q2", "q3", "q4"]

    monkeypatch.setattr(session_route, "submit_recruiter_lead", fake_lead)
    monkeypatch.setattr(session_route, "parse_job_description", fake_parse)
    monkeypatch.setattr(session_route, "generate_prompts", fake_prompts)

    class _NoDb:
        enabled = False

    app = FastAPI()
    app.include_router(session_route.router, prefix="/api/v1")
    app.add_middleware(telemetry.TelemetryMiddleware)
    app.state.http_client = httpx.AsyncClient()
    app.state.vertex_auth = None
    app.state.sessions = TTLCache(ttl_seconds=60, max_size=10)
    app.state.session_tokens = SessionTokens("secret")
    app.state.quotas = QuotaService(None, messages_per_session=9, messages_per_ip_per_day=9,
                                    sessions_per_ip_per_day=9, daily_token_budget=9, salt="s")
    app.dependency_overrides[session_route.get_rate_limiter] = lambda: RateLimiter(requests_per_minute=100)
    app.dependency_overrides[session_route.get_supabase_service] = lambda: _NoDb()

    body = {"user_name": "Probe", "company": "Acme", "job_posting": "We are hiring an ML engineer " * 5}
    client = TestClient(app)
    assert client.post("/api/v1/session", json=body, headers={"X-Synthetic-Check": "A2"}).status_code == 200
    assert leads == []
    assert client.post("/api/v1/session", json=body).status_code == 200
    assert len(leads) == 1


# ── Leads, retention and table sizes ─────────────────────────────────

def test_lead_outcomes_counted(otel, monkeypatch):
    import asyncio
    import httpx
    from types import SimpleNamespace
    from services import leads

    monkeypatch.setattr(leads, "get_settings", lambda: SimpleNamespace(google_sheet_webhook="https://script.google.com/x"))
    replies = iter([
        httpx.Response(200, json={"result": "success"}),
        httpx.Response(500, text="boom"),
        httpx.Response(200, headers={"content-type": "text/html"}, text="<html><title>Error</title>TypeError</html>"),
        httpx.ConnectError("down"),
        httpx.Response(200, json={"result": "error", "error": "sheet not found"}),
    ])

    def handler(request):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def send_all():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for _ in range(5):
                await leads.submit_recruiter_lead(client, name="A", company="B", job_posting="", session_id="s")
        monkeypatch.setattr(leads, "get_settings", lambda: SimpleNamespace(google_sheet_webhook=""))
        await leads.submit_recruiter_lead(None, name="A", company="B", job_posting="", session_id="s")

    asyncio.run(send_all())
    _, points = otel
    by_outcome = {_attrs(p)["outcome"]: p.value for p in points("chat.leads")}
    assert by_outcome == {"ok": 1, "error": 4, "skipped": 1}


def test_row_count_gauge(otel):
    _, points = otel

    class _Db:
        last_ping = "ok"
        row_counts = None

    db = _Db()
    telemetry.watch_database(db)
    assert points("chat.database.rows") == []
    db.row_counts = {"sessions": 3, "events": 10}
    assert {_attrs(p)["db.collection.name"]: p.value for p in points("chat.database.rows")} == {"sessions": 3, "events": 10}


def test_retention_counted(otel):
    _, points = otel
    telemetry.record_retention("supabase", "ok", 5)
    telemetry.record_retention("langfuse", "error")
    assert {(_attrs(p)["store"], _attrs(p)["outcome"]): p.value for p in points("chat.retention.runs")} == \
        {("supabase", "ok"): 1, ("langfuse", "error"): 1}
    assert [(_attrs(p)["store"], p.value) for p in points("chat.retention.deleted")] == [("supabase", 5)]


def test_supabase_purge_deletes_idle_sessions_and_old_events():
    import asyncio
    import datetime as dt
    from types import SimpleNamespace
    from services.supabase_service import SupabaseService

    calls = []

    class _Query:
        def __init__(self, table):
            self.table = table

        def delete(self, count=None, returning=None):
            assert (count, returning) == ("exact", "minimal")  # counted, not downloaded
            return self

        def lt(self, column, cutoff):
            calls.append((self.table, column, cutoff))
            return self

        async def execute(self):
            return SimpleNamespace(data=[], count=2 if self.table == "sessions" else 1)

    db = SupabaseService()
    db.enabled, db.client = True, SimpleNamespace(table=_Query)
    assert asyncio.run(db.purge_older_than(90)) == 3
    assert [(t, c) for t, c, _ in calls] == [("sessions", "last_active"), ("events", "created_at")]
    cutoff = dt.datetime.fromisoformat(calls[0][2])
    assert abs((dt.datetime.now(dt.timezone.utc) - cutoff).days - 90) <= 1


def test_supabase_purge_raises_when_a_query_fails():
    import asyncio
    from types import SimpleNamespace
    from services.supabase_service import SupabaseService

    class _Broken:
        def __init__(self, table):
            pass

        def delete(self, count=None, returning=None):
            return self

        def lt(self, *args):
            return self

        async def execute(self):
            raise RuntimeError("down")

    db = SupabaseService()
    db.enabled, db.client = True, SimpleNamespace(table=_Broken)
    with pytest.raises(RuntimeError):
        asyncio.run(db.purge_older_than(90))


def test_langfuse_purge_deletes_old_traces_page_by_page():
    from types import SimpleNamespace
    from services.tracer import TracingService

    pages = {None: ([SimpleNamespace(trace_id="t1"), SimpleNamespace(trace_id="t2"), SimpleNamespace(trace_id="t1")], "c2"),
             "c2": ([SimpleNamespace(trace_id="t3")], None)}
    deleted, asked = [], []

    def get_many(**kwargs):
        asked.append(kwargs)
        data, cursor = pages[kwargs.get("cursor")]
        return SimpleNamespace(data=data, meta=SimpleNamespace(cursor=cursor))

    api = SimpleNamespace(observations=SimpleNamespace(get_many=get_many),
                          trace=SimpleNamespace(delete_multiple=lambda trace_ids: deleted.append(list(trace_ids))))
    tracer = TracingService.__new__(TracingService)
    tracer.enabled, tracer.client = True, SimpleNamespace(api=api)
    assert tracer.purge_traces_older_than(90) == 3
    assert deleted == [["t1", "t2", "t3"]]  # one batch, each trace once
    assert [a.get("cursor") for a in asked] == [None, "c2"]  # followed the cursor, no re-read of page one
    assert asked[0]["is_root_observation"] is True and asked[0]["limit"] == 1000


def test_mcp_events_carry_the_caller_and_quotas_count_by_it():
    """events.session_id is a foreign key to sessions, so MCP events have no session and the
    caller goes in metadata; the per-caller count must filter on that (Oct 2026: every MCP insert
    failed, so the MCP budget read 0)"""
    import asyncio
    from types import SimpleNamespace
    from services.quotas import QuotaService

    filters = []

    class _Query:
        def select(self, *a, **k): return self
        def gte(self, *a): return self
        def eq(self, column, value):
            filters.append((column, value)); return self
        async def execute(self): return SimpleNamespace(count=0, data=[])

    supabase = SimpleNamespace(enabled=True, client=SimpleNamespace(table=lambda name: _Query()))
    q = QuotaService(supabase, messages_per_session=5, messages_per_ip_per_day=50, sessions_per_ip_per_day=5,
                     daily_token_budget=10_000, salt="s", mcp_daily_token_budget=1_000)
    asyncio.run(q.check_mcp("mcp:abc", "iphash"))
    assert ("metadata->>caller", "mcp:abc") in filters and ("event_type", "mcp") in filters
    assert ("session_id", "mcp:abc") not in filters

