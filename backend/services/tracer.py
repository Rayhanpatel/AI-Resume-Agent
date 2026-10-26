"""
Langfuse Tracing Service (Python SDK v5, Langfuse v4 data model)
Observability for LLM calls, prompt management and cost tracking.

One chat turn is one trace: a root observation that holds the visitor's message as
input and the answer as output, with the model calls as child generations. Langfuse
v4 is observations-first, so session, user, tags and environment are propagated onto
every observation (propagate_attributes) instead of being set on the trace.

Langfuse gets its own TracerProvider. Without one it claims the global provider, and
core/telemetry.py keeps its providers private too, so neither pipeline sees the
other's spans: message text stays in Langfuse, and Grafana gets counts and timings.
"""
import asyncio
import re
from langfuse import Langfuse, propagate_attributes
from opentelemetry.sdk.trace import TracerProvider
from core import telemetry
from typing import Optional, Any, Dict, List
import logging

logger = logging.getLogger(__name__)

# Gemini pricing (USD per 1M tokens; output includes thinking tokens)
# Source: https://ai.google.dev/gemini-api/docs/pricing (checked Oct 2026)
MODEL_PRICING = {
    # Vertex global endpoint, per 1M tokens. Cached input is $0.15; implicit caching served
    # 8,158 of 9,279 prompt tokens from the second call on (measured Oct 25 2026), so this
    # full-price estimate overstates real cost. Thinking tokens bill as output.
    "gemini-3.5-flash": {
        "input": 1.50,
        "output": 9.00
    },
    "gemini-3.8-flash": {
        "input": 1.50,    # standard from Jan 1 2027 (introductory $0.75 until then)
        "output": 7.50    # standard from Jan 1 2027 (introductory $3.75 until then)
    },
    "gemini-3.5-flash-lite": {
        "input": 0.30,
        "output": 2.50
    },
}

# Propagated values are JSON-encoded and must stay within 200 characters (198 for a string)
PROPAGATED_VALUE_LIMIT = 150


def calculate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Calculate cost in USD for a generation (0.0 for a model not in the table, rather than a wrong guess)"""
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        return 0.0

    input_cost = (tokens_in / 1_000_000) * pricing["input"]
    output_cost = (tokens_out / 1_000_000) * pricing["output"]

    return round(input_cost + output_cost, 6)


def langfuse_environment(value: Optional[str]) -> Optional[str]:
    """Langfuse environments are lowercase [a-z0-9-_], at most 40 characters, and may not start with 'langfuse'"""
    env = re.sub(r"[^a-z0-9_-]", "-", (value or "").strip().lower())[:40]
    if not env or env.startswith("langfuse"):
        return None
    return env


def visitor_id(ip_hash: Optional[str]) -> Optional[str]:
    """The Langfuse user ID: the salted IP hash, never the name the visitor typed"""
    return f"visitor-{ip_hash[:16]}" if ip_hash else None


def trace_tags(channel: str, *, synthetic: bool = False, stream: Optional[bool] = None) -> List[str]:
    """Tags for filtering traces and evaluator rules: channel, probe traffic, streaming or not"""
    tags = [channel]
    if synthetic:
        tags.append("synthetic")
    if stream is not None:
        tags.append("stream" if stream else "sync")
    return tags


class TraceHandle:
    """One chat turn in Langfuse: the root observation and the attributes its children inherit"""

    def __init__(self, name: str, root, propagated: Dict[str, Any]):
        self.name = name
        self.root = root
        self.propagated = propagated
        self.session_id = propagated.get("session_id")
        self.ended = False


class TracingService:
    """Wrapper for Langfuse tracing with SDK v5"""

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        host: str = "https://cloud.langfuse.com",
        environment: Optional[str] = None,
        release: Optional[str] = None,
        version: Optional[str] = None,
        span_exporter=None
    ):
        self._prompt_cache = {}
        self.version = version  # the profile version: answers change when the profile does

        # Skip initialization if keys are empty/missing
        if not public_key or not secret_key:
            logger.warning("Langfuse keys not provided - tracing disabled")
            self.client = None
            self.enabled = False
            return

        try:
            self.client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                base_url=host,
                environment=langfuse_environment(environment),
                release=release,
                # A private provider: otherwise Langfuse sets the OpenTelemetry global one
                tracer_provider=TracerProvider(),
                span_exporter=span_exporter,  # tests only
            )
            self.enabled = True
            logger.info(f"Langfuse initialized with host: {host}")
        except Exception as e:
            logger.warning(f"Langfuse init failed: {e}")
            self.client = None
            self.enabled = False

    def get_prompt(
        self,
        name: str,
        fallback: str = None,
        cache_ttl: int = 300
    ) -> Optional[str]:
        """
        Fetch a prompt from Langfuse prompt management.

        Args:
            name: Name of the prompt in Langfuse
            fallback: Fallback prompt if fetch fails
            cache_ttl: Cache time in seconds (not used yet, for future)

        Returns:
            Prompt text or fallback
        """
        if not self.enabled:
            return fallback

        # Check cache first
        if name in self._prompt_cache:
            return self._prompt_cache[name]

        try:
            prompt = self.client.get_prompt(name)
            if prompt:
                compiled = prompt.compile()
                self._prompt_cache[name] = compiled
                logger.info(f"Loaded prompt '{name}' from Langfuse")
                return compiled
        except Exception as e:
            logger.warning(f"Failed to fetch prompt '{name}': {e}")

        return fallback

    def trace(
        self,
        name: str,
        session_id: str = None,
        user_id: Optional[str] = None,
        input: Optional[Any] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[dict] = None
    ) -> Optional[TraceHandle]:
        """
        Start one chat turn: a root observation with the visitor's message as input.
        session_id, user_id, tags and metadata are propagated to it and to every child
        logged through this handle. End it with end_trace().
        """
        if not self.enabled:
            return None

        propagated = {
            "trace_name": name,
            "session_id": session_id,
            "user_id": user_id,
            "version": self.version,
            "tags": sorted(set(tags or [])) or None,
            "metadata": {k: str(v)[:PROPAGATED_VALUE_LIMIT] for k, v in (metadata or {}).items() if v is not None} or None,
        }
        propagated = {k: v for k, v in propagated.items() if v}
        try:
            # Detached: the root starts its own trace, outside any telemetry span
            with telemetry.detached(), propagate_attributes(**propagated):
                root = self.client.start_observation(name=name, as_type="span", input=input)
            return TraceHandle(name, root, propagated)
        except Exception as e:
            logger.warning(f"Langfuse trace start failed: {e}")
            return None

    def end_trace(
        self,
        trace: Optional[TraceHandle],
        output: Optional[Any] = None,
        metadata: Optional[dict] = None,
        error: Optional[str] = None
    ):
        """Put the answer on the root observation and end it (safe to call twice)"""
        if not self.enabled or not trace or trace.ended:
            return
        trace.ended = True
        try:
            trace.root.update(
                output=output,
                metadata=metadata,
                **({"level": "ERROR", "status_message": str(error)[:200]} if error else {})
            )
            trace.root.end()
        except Exception as e:
            logger.warning(f"Langfuse trace end failed: {e}")

    def log_generation(
        self,
        trace,
        name: str,
        model: str,
        input_text: str,
        output_text: str,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        latency_ms: Optional[int] = None,
        metadata: Optional[dict] = None
    ) -> Dict[str, Any]:
        """
        Log an LLM generation with cost tracking, as a child of the turn's root observation.

        Returns dict with cost info for analytics.
        """
        result = {"logged": False, "cost_usd": 0.0}

        if not self.enabled or not trace:
            return result

        try:
            # Calculate cost if tokens available
            cost_usd = 0.0
            if tokens_in and tokens_out:
                cost_usd = calculate_cost(model, tokens_in, tokens_out)

            # Combine metadata
            full_metadata = metadata.copy() if metadata else {}
            if latency_ms:
                full_metadata["latency_ms"] = latency_ms
            if cost_usd:
                full_metadata["cost_usd"] = cost_usd

            # A child of the root, created inside the same propagation scope so it carries
            # the session and user IDs that Langfuse needs to add its cost to the session
            with telemetry.detached(), propagate_attributes(**trace.propagated):
                gen = trace.root.start_observation(
                    as_type="generation",
                    name=name,
                    model=model,
                    input=input_text,
                    metadata=full_metadata
                )
            gen.update(
                output=output_text,
                usage_details={"input": tokens_in, "output": tokens_out or 0} if tokens_in else None
            )
            gen.end()

            result["logged"] = True
            result["cost_usd"] = cost_usd
            result["tokens_in"] = tokens_in
            result["tokens_out"] = tokens_out

            return result

        except Exception as e:
            logger.warning(f"Langfuse generation log failed: {e}")
            return result

    def log_span(
        self,
        name: str,
        input_text: str = None,
        output_text: str = None,
        metadata: Optional[dict] = None
    ):
        """Log a simple standalone span"""
        if not self.enabled:
            return None

        try:
            with telemetry.detached():
                span = self.client.start_observation(as_type="span", name=name, input=input_text, metadata=metadata)
            if output_text:
                span.update(output=output_text)
            span.end()
            return True
        except Exception as e:
            logger.warning(f"Langfuse span log failed: {e}")
            return None

    def score(
        self,
        trace_id: str,
        name: str,
        value: float,
        comment: str = None
    ):
        """Add a score to a trace for evaluation"""
        if not self.enabled:
            return None

        try:
            self.client.create_score(
                trace_id=trace_id,
                name=name,
                value=value,
                comment=comment
            )
            return True
        except Exception as e:
            logger.warning(f"Langfuse score failed: {e}")
            return None

    def flush(self):
        """Flush pending events (blocks until sent; use aflush from async code)"""
        if self.enabled and self.client:
            try:
                self.client.flush()
            except Exception as e:
                logger.warning(f"Langfuse flush failed: {e}")

    async def aflush(self):
        """Flush on a worker thread, so a slow Langfuse never stalls the event loop"""
        if self.enabled and self.client:
            await asyncio.to_thread(self.flush)

    def shutdown(self):
        """Flush and stop the exporter (end of the app lifespan)"""
        if self.enabled and self.client:
            try:
                self.client.shutdown()
            except Exception as e:
                logger.warning(f"Langfuse shutdown failed: {e}")
