"""
OpenTelemetry for the backend: traces, metrics and logs over OTLP/HTTP.

Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set. The standard OTEL_* variables pick
the destination (Grafana Cloud now; Datadog or anything that speaks OTLP later),
so changing vendors is configuration, not code:
  OTEL_EXPORTER_OTLP_ENDPOINT   e.g. https://otlp-gateway-prod-us-east-0.grafana.net/otlp
  OTEL_EXPORTER_OTLP_HEADERS    e.g. Authorization=Basic%20<base64 of instance-id:token>
  OTEL_SERVICE_NAME             optional, defaults to rayhan-agent

Nothing a visitor typed leaves through here: spans and metrics carry models, token
counts, outcomes and timings. The conversation traces stay in Langfuse.

The providers are private to this module, not the OpenTelemetry globals, and the
Langfuse SDK gets a private provider of its own (services/tracer.py), so neither
pipeline sees the other's spans.

gen_ai.* names follow the OpenTelemetry GenAI semantic conventions as shipped in
the opentelemetry-semantic-conventions 0.66b1 package. Upstream they are still
"Development" status (moved to open-telemetry/semantic-conventions-genai in June
2026, no release there yet), so a rename is possible; they are kept in one place.
"""
import contextvars
import logging
import os
import time
from contextlib import contextmanager
from typing import Optional

from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

TRACER_NAME = "rayhan.agent"
PROVIDER_VERTEX = "gcp.vertex_ai"    # gen_ai.provider.name values in the 0.66b1 conventions
PROVIDER_AI_STUDIO = "gcp.gemini"
PROVIDER_OPENROUTER = "openrouter"
SYNTHETIC_HEADER = b"x-synthetic-check"
LANGFUSE_SCOPE = "langfuse-sdk"      # its spans hold message text, so they never go to the OTLP backend

SECONDS = [0.25, 0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 45, 60]
TOKENS = [16, 64, 256, 1024, 4096, 16384, 65536]  # powers of 4, as the GenAI conventions suggest

# Set per request by TelemetryMiddleware
_synthetic = contextvars.ContextVar("synthetic", default=False)
_channel = contextvars.ContextVar("channel", default="web")


class _Instruments:
    """Every metric the backend reports; the dashboards and alert rules use these names"""

    def __init__(self, meter):
        self.chat_starts = meter.create_counter(
            "chat.starts", unit="{start}", description="Chat sessions requested, by outcome")
        self.answers = meter.create_counter(
            "chat.answers", unit="{answer}", description="Answers to visitors, by outcome and model")
        self.fallbacks = meter.create_counter(
            "chat.model_fallbacks", unit="{answer}", description="Answers that came from the fallback model")
        self.first_word = meter.create_histogram(
            "chat.time_to_first_word", unit="s", explicit_bucket_boundaries_advisory=SECONDS,
            description="Request received to first streamed word, as the visitor waits for it")
        self.stamps = meter.create_counter(
            "chat.verification.stamps", unit="{sentence}", description="Verification stamps on answers, by kind")
        self.quota_rejections = meter.create_counter(
            "chat.quota.rejections", unit="{request}", description="Requests refused by a quota, by scope")
        self.token_usage = meter.create_histogram(
            "gen_ai.client.token.usage", unit="{token}", explicit_bucket_boundaries_advisory=TOKENS,
            description="Tokens per model call (output includes thinking tokens, as billed)")
        self.operation_duration = meter.create_histogram(
            "gen_ai.client.operation.duration", unit="s", explicit_bucket_boundaries_advisory=SECONDS,
            description="Model call duration")
        self.first_chunk = meter.create_histogram(
            "gen_ai.client.operation.time_to_first_chunk", unit="s", explicit_bucket_boundaries_advisory=SECONDS,
            description="Model request to first streamed chunk")
        self.leads = meter.create_counter(
            "chat.leads", unit="{lead}", description="Recruiter leads sent to the Google Sheet, by outcome")
        self.retention_deleted = meter.create_counter(
            "chat.retention.deleted", unit="{item}",
            description="Rows and traces older than the retention window that were deleted, by store")
        self.retention_runs = meter.create_counter(
            "chat.retention.runs", unit="{run}", description="Retention job runs, by store and outcome")


_noop_meter = metrics.NoOpMeterProvider().get_meter(TRACER_NAME)
_tracer = trace.NoOpTracerProvider().get_tracer(TRACER_NAME)
_meter = _noop_meter
_m = _Instruments(_noop_meter)
_state = {"enabled": False, "tracer_provider": None, "meter_provider": None,
          "logger_provider": None, "log_handler": None}


def enabled() -> bool:
    return _state["enabled"]


def is_synthetic() -> bool:
    return _synthetic.get()


def channel() -> str:
    return _channel.get()


def _labels(**extra) -> dict:
    return {"app.synthetic": is_synthetic(), "app.channel": channel(), **extra}


# ── Setup ────────────────────────────────────────────────────────────

def setup(version: str = "", environment: str = "", *, span_exporter=None, metric_reader=None,
          log_exporter=None) -> bool:
    """
    Turn telemetry on when OTEL_EXPORTER_OTLP_ENDPOINT is set (or when tests pass
    exporters). Returns whether it is on. Safe to call once at import time.
    """
    global _tracer, _meter, _m
    testing = any(x is not None for x in (span_exporter, metric_reader, log_exporter))
    if not (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or testing):
        logger.info("Telemetry off (OTEL_EXPORTER_OTLP_ENDPOINT not set)")
        return False

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.instrumentation.logging.handler import LoggingHandler

    attributes = {"service.version": version, "deployment.environment.name": environment}
    if not os.getenv("OTEL_SERVICE_NAME"):
        attributes["service.name"] = "rayhan-agent"
    resource = Resource.create({k: v for k, v in attributes.items() if v})

    if span_exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        span_exporter = OTLPSpanExporter()
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(_SkipScopes(span_exporter, {LANGFUSE_SCOPE}))

    if metric_reader is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=60_000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])

    if log_exporter is None:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        log_exporter = OTLPLogExporter()
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    # Log records carry the trace ID of the request they came from, so a log line links to its trace
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    handler.addFilter(lambda record: not record.name.startswith("opentelemetry"))
    logging.getLogger().addHandler(handler)

    _tracer = tracer_provider.get_tracer(TRACER_NAME, version)
    _meter = meter_provider.get_meter(TRACER_NAME, version)
    _m = _Instruments(_meter)
    _state.update(enabled=True, tracer_provider=tracer_provider, meter_provider=meter_provider,
                  logger_provider=logger_provider, log_handler=handler)
    logger.info("Telemetry on (OTLP)")
    return True


def instrument_app(app):
    """Server spans and HTTP metrics for every request except the health checks"""
    if not enabled():
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_state["tracer_provider"],
        meter_provider=_state["meter_provider"],
        excluded_urls="api/v1/health",     # polled every few seconds by Railway and the probes
        exclude_spans=["receive", "send"],  # one span per streamed chunk otherwise
        server_request_hook=_tag_server_span,
    )


def instrument_http_client(client):
    """Client spans for the shared httpx client (Vertex, Supabase REST calls made through it, the lead webhook)"""
    if not enabled():
        return
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    HTTPXClientInstrumentor.instrument_client(
        client,
        tracer_provider=_state["tracer_provider"],
        meter_provider=_state["meter_provider"],
        request_hook=_redact_secret_urls_async,  # the shared client is an AsyncClient
    )


def watch_database(supabase_service):
    """chat.database.up: 1 or 0 from the last real query (startup and /health/llm), absent before the first"""
    def observe(options):
        status = getattr(supabase_service, "last_ping", None)
        if status in (None, "off"):
            return []
        return [metrics.Observation(1 if status == "ok" else 0, {"db.system.name": "postgresql"})]

    # No unit: Prometheus naming would turn "1" into a _ratio suffix
    _meter.create_observable_gauge("chat.database.up", callbacks=[observe],
                                   description="Whether the database answered its last check (1 or 0)")

    def observe_rows(options):
        counts = getattr(supabase_service, "row_counts", None) or {}
        return [metrics.Observation(n, {"db.collection.name": table}) for table, n in counts.items()]

    _meter.create_observable_gauge("chat.database.rows", unit="{row}", callbacks=[observe_rows],
                                   description="Rows per table at the last maintenance run (the free plan holds 500 MB)")


def shutdown():
    """Flush and stop the exporters (called at the end of the app lifespan, i.e. on SIGTERM)"""
    if not enabled():
        return
    if _state["log_handler"]:
        logging.getLogger().removeHandler(_state["log_handler"])
    for key in ("tracer_provider", "meter_provider", "logger_provider"):
        try:
            _state[key].shutdown()
        except Exception as e:
            logger.warning(f"Telemetry shutdown ({key}) failed: {e}")


class _SkipScopes(BatchSpanProcessor):
    """A batch span processor that drops spans from some instrumentation scopes"""

    def __init__(self, exporter, skip: set):
        super().__init__(exporter)
        self._skip = skip

    def on_end(self, span):
        if span.instrumentation_scope and span.instrumentation_scope.name in self._skip:
            return
        super().on_end(span)


def _tag_server_span(span, scope):
    if span and span.is_recording():
        headers = dict(scope.get("headers") or [])
        span.set_attribute("app.synthetic", SYNTHETIC_HEADER in headers)


def _redact_secret_urls(span, request):
    """The Google Sheet webhook URL is itself a credential; keep it out of span attributes"""
    if span and span.is_recording() and request.url.host == "script.google.com":
        for key in ("url.full", "http.url"):
            span.set_attribute(key, "https://script.google.com/macros/s/[redacted]/exec")


async def _redact_secret_urls_async(span, request):
    _redact_secret_urls(span, request)


# ── Request tagging ──────────────────────────────────────────────────

def start_outcome(status: int) -> str:
    """POST /session status → chat.starts outcome"""
    if status == 200:
        return "ok"
    if status == 403:
        return "captcha_failed"  # the only 403 /session raises
    if status == 429:
        return "quota"           # daily session quota or the per-minute rate limit
    if status in (400, 422):
        return "invalid"
    return "error" if status >= 500 else "other"


class TelemetryMiddleware:
    """
    Tags each request as synthetic (an X-Synthetic-Check header from the probes) and
    by channel (web chat or MCP), and counts chat starts by outcome from the /session
    status code. A tag, never a bypass: the header changes no check or limit; it only
    keeps probe traffic out of lead capture and labels it in metrics.
    Pure ASGI, like RequestIDMiddleware, so streamed responses aren't buffered.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        synthetic_token = _synthetic.set(SYNTHETIC_HEADER in dict(scope.get("headers") or []))
        channel_token = _channel.set("mcp" if path.startswith("/mcp") else "web")
        is_start = scope.get("method") == "POST" and path.rstrip("/") == "/api/v1/session"
        status = {}

        async def send_with_status(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_with_status if is_start else send)
        except Exception:
            status.setdefault("code", 500)
            raise
        finally:
            if is_start and "code" in status:
                _m.chat_starts.add(1, _labels(outcome=start_outcome(status["code"])))
            _synthetic.reset(synthetic_token)
            _channel.reset(channel_token)


@contextmanager
def detached():
    """Run with no current span, so a Langfuse turn starts its own trace and gets no telemetry attributes"""
    token = otel_context.attach(otel_context.Context())
    try:
        yield
    finally:
        otel_context.detach(token)


# ── Model calls ──────────────────────────────────────────────────────

@contextmanager
def gen_ai_span(model: str, purpose: str, provider: str = PROVIDER_VERTEX,
                attempt: Optional[int] = None, current: bool = True):
    """
    A gen_ai client span around one model call, plus its duration metric.
    `purpose` says which part of the system called (answer, intent, verify, job_parse,
    prompts, decision). Use current=False inside async generators: a span made current
    across a `yield` can't be detached cleanly.
    """
    attrs = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": provider,
             "gen_ai.request.model": model, "app.purpose": purpose, **_labels()}
    if attempt is not None:
        attrs["app.attempt"] = attempt
    start = time.perf_counter()
    span = _tracer.start_span(f"chat {model}", kind=SpanKind.CLIENT, attributes=attrs)
    token = otel_context.attach(trace.set_span_in_context(span)) if current else None
    error_type = None
    try:
        yield span
    except Exception as e:
        error_type = type(e).__name__
        span.set_attribute("error.type", error_type)
        span.record_exception(e)
        span.set_status(Status(StatusCode.ERROR, str(e)[:200]))
        raise
    finally:
        if token is not None:
            otel_context.detach(token)
        error_type = error_type or (getattr(span, "attributes", None) or {}).get("error.type")
        span.end()
        duration_attrs = {k: attrs[k] for k in ("gen_ai.operation.name", "gen_ai.provider.name",
                                                "gen_ai.request.model", "app.purpose")}
        if error_type:
            duration_attrs["error.type"] = error_type
        _m.operation_duration.record(time.perf_counter() - start, duration_attrs)


def provider_for(vertex_auth) -> str:
    return PROVIDER_VERTEX if vertex_auth and getattr(vertex_auth, "enabled", False) else PROVIDER_AI_STUDIO


def mark_error(span, error_type: str, message: str = ""):
    """Mark a model call failed without raising (e.g. a 429 the caller handles itself)"""
    if span is not None and span.is_recording():
        span.set_attribute("error.type", error_type)
        span.set_status(Status(StatusCode.ERROR, message[:200] or error_type))


def record_response(span, model: str, purpose: str, response, provider: str = PROVIDER_VERTEX):
    """Status and token usage of a non-streaming model response, onto the span and metrics"""
    if response.status_code != 200:
        mark_error(span, str(response.status_code), f"HTTP {response.status_code}")
        return
    try:
        usage = response.json().get("usageMetadata")
    except Exception:
        return
    record_usage(span, model, purpose, usage, provider)


def record_usage(span, model: str, purpose: str, usage: Optional[dict], provider: str = PROVIDER_VERTEX):
    """Token counts from Gemini's usageMetadata onto the span and the token metric"""
    if not usage:
        return
    tokens_in = usage.get("promptTokenCount") or 0
    tokens_out = (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
    if span is not None and span.is_recording():
        span.set_attribute("gen_ai.usage.input_tokens", tokens_in)
        span.set_attribute("gen_ai.usage.output_tokens", tokens_out)
    base = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": provider,
            "gen_ai.request.model": model, "app.purpose": purpose}
    _m.token_usage.record(tokens_in, {**base, "gen_ai.token.type": "input"})
    _m.token_usage.record(tokens_out, {**base, "gen_ai.token.type": "output"})


def record_first_chunk(model: str, seconds: float, provider: str = PROVIDER_VERTEX):
    _m.first_chunk.record(seconds, {"gen_ai.operation.name": "chat", "gen_ai.provider.name": provider,
                                    "gen_ai.request.model": model})


# ── Answers, stamps, quotas ──────────────────────────────────────────

def answer_outcome(response: Optional[str], error: Optional[str] = None, apology: str = "") -> str:
    """ok, apology (every model attempt failed and the visitor got the apology) or error (no answer at all)"""
    if apology and response and response.strip() == apology.strip():
        return "apology"
    if error or not response:
        return "error"
    return "ok"


def record_answer(outcome: str, model: Optional[str] = None):
    _m.answers.add(1, _labels(outcome=outcome, **({"gen_ai.response.model": model} if model else {})))


def record_fallback(from_model: str, to_model: str):
    _m.fallbacks.add(1, _labels(**{"app.from_model": from_model, "app.to_model": to_model}))


def record_first_word(seconds: float):
    _m.first_word.record(seconds, _labels())


def record_stamps(summary: Optional[dict]):
    """chat.verification.stamps by kind: checked, inferred, unknown, unverified, numbers_unverified"""
    for kind, count in (summary or {}).items():
        if count:
            _m.stamps.add(count, _labels(stamp=kind))


def record_quota_rejection(scope: str):
    _m.quota_rejections.add(1, _labels(scope=scope))


def record_lead(outcome: str):
    """chat.leads: ok (the sheet accepted it) or error (an HTTP error, an error page, or no answer)"""
    _m.leads.add(1, {"outcome": outcome})


def record_retention(store: str, outcome: str, deleted: int = 0):
    """chat.retention.runs and chat.retention.deleted, per store (supabase, langfuse)"""
    _m.retention_runs.add(1, {"store": store, "outcome": outcome})
    if deleted:
        _m.retention_deleted.add(deleted, {"store": store})
