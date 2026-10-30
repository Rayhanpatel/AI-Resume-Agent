"""
The decision-model intent gate: a probability compared with a threshold, a zero-data-retention
request, and a fallback (production: allow the message, so the answer model decides), so a failure
of the decision model can never block a real question. Version 3 also sends the previous turn.
"""
import asyncio
import json
from pathlib import Path

import httpx

from services.intent import FIXED_DECLINE, JEV_INTENT_QUESTION, JevIntentGate


def _gate(handler, threshold=0.5, fallback=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JevIntentGate(client, "key", "typesafe/jev-1.14-20261014", threshold=threshold, fallback=fallback, timeout=1.0)


def _answer(p):
    return lambda request: httpx.Response(200, json={
        "answers": {"on_topic": {"type": "noul", "noul": p}},
        "usage": {"input_tokens": 394, "output_tokens": 21, "cost": 1.6e-05}})


def test_threshold_decides_and_off_topic_gets_the_fixed_decline():
    allowed = asyncio.run(_gate(_answer(0.91)).classify("What did Rayhan build at 3E?"))
    assert allowed["intent"] == "job_related" and allowed["gate"] == "jev" and allowed["decline_message"] is None
    assert allowed["p_on_topic"] == 0.91
    blocked = asyncio.run(_gate(_answer(0.04)).classify("Write me a poem"))
    assert blocked["intent"] == "off_topic" and blocked["decline_message"] == FIXED_DECLINE
    assert abs(blocked["confidence"] - 0.96) < 1e-9
    assert asyncio.run(_gate(_answer(0.30), threshold=0.25).classify("borderline"))["intent"] == "job_related"


def test_request_asks_for_zero_data_retention_and_the_frozen_question():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return _answer(0.9)(request)

    asyncio.run(_gate(handler).classify("hello"))
    assert seen["provider"] == {"zdr": True, "data_collection": "deny"}
    assert seen["questions"] == {"on_topic": JEV_INTENT_QUESTION}
    assert seen["state"] == {"visitor_message": "hello"}


class _Fallback:
    def __init__(self, intent):
        self.intent, self.calls = intent, 0

    async def classify(self, query, previous=None):
        self.calls += 1
        return {"intent": self.intent, "confidence": 0.9, "reasoning": "llm", "decline_message": "no"}


def test_failures_fall_back_to_the_llm_classifier():
    for handler in (lambda r: httpx.Response(503, text="overloaded"),
                    lambda r: httpx.Response(200, json={"answers": {}}),
                    lambda r: (_ for _ in ()).throw(httpx.ConnectTimeout("slow"))):
        fallback = _Fallback("off_topic")
        result = asyncio.run(_gate(handler, fallback=fallback).classify("q"))
        assert fallback.calls == 1 and result["gate"] == "fallback" and result["intent"] == "off_topic"


def test_without_a_fallback_a_failure_allows_the_message():
    result = asyncio.run(_gate(lambda r: httpx.Response(500)).classify("q"))
    assert result["intent"] == "job_related" and result["gate"] == "fallback"


def test_decisions_and_fallbacks_are_counted():
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from core import telemetry

    saved = (telemetry._tracer, telemetry._meter, telemetry._m, dict(telemetry._state))
    reader = InMemoryMetricReader()
    assert telemetry.setup("t", "t", span_exporter=InMemorySpanExporter(), metric_reader=reader,
                           log_exporter=InMemoryLogRecordExporter())
    try:
        asyncio.run(_gate(_answer(0.9)).classify("a"))
        asyncio.run(_gate(_answer(0.1)).classify("b"))
        asyncio.run(_gate(lambda r: httpx.Response(500)).classify("c"))
        points = {}
        for rm in reader.get_metrics_data().resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name not in ("chat.intent.decisions", "chat.intent.fallbacks"):
                        continue
                    for dp in m.data.data_points:
                        points.setdefault(m.name, []).append((dict(dp.attributes), dp.value))
        decisions = {(a["gate"], a["intent"]): v for a, v in points["chat.intent.decisions"]}
        assert decisions == {("jev", "job_related"): 1, ("jev", "off_topic"): 1, ("fallback", "job_related"): 1}
        assert sum(v for _, v in points["chat.intent.fallbacks"]) == 1
    finally:
        telemetry.shutdown()
        telemetry._tracer, telemetry._meter, telemetry._m = saved[:3]
        telemetry._state.clear(); telemetry._state.update(saved[3])


def test_names_variant_sends_the_profile_names_and_explains_them():
    from services.intent import CONTEXT_SENTENCE
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return _answer(0.9)(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gate = JevIntentGate(client, "key", "typesafe/jev-1.14-20261014", threshold=0.5, names=["EvoRank", "3E"], timeout=1.0)
    asyncio.run(gate.classify("What is EvoRank?"))
    assert seen["state"] == {"visitor_message": "What is EvoRank?", "names_in_rayhans_profile": ["EvoRank", "3E"]}
    q = seen["questions"]["on_topic"]
    assert q["instructions"] == JEV_INTENT_QUESTION["instructions"] + CONTEXT_SENTENCE
    assert q["criteria"] == JEV_INTENT_QUESTION["criteria"]


def test_profile_names_come_from_the_real_profile():
    from services.intent import _load_profile_names
    names = _load_profile_names()
    assert {"3E", "EvoRank", "University of Maryland"} <= set(names), names
    assert len(names) == len(set(names))
    # names only: no email address or phone-length run of digits goes to the decision model
    import re
    assert not [n for n in names if "@" in n or re.search(r"\d{7,}", re.sub(r"[\s().+-]", "", n))]


def test_stamp_second_opinion_also_asks_for_zero_data_retention():
    from services.verifier import DecisionsGate
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"answers": {"s0": {"type": "noul", "noul": 0.97}}})

    gate = DecisionsGate(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "key", "typesafe/jev-1.14-20261014")
    assert asyncio.run(gate.probabilities(["Rayhan interned at 3E."])) == {0: 0.97}
    assert seen["provider"] == {"zdr": True, "data_collection": "deny"}


def test_configured_threshold_is_the_one_the_benchmark_chose():
    """The threshold belongs to one model version and one question; it must come from the dev half."""
    from pathlib import Path
    from core.config import Settings
    results = sorted((Path(__file__).resolve().parents[1] / "evals" / "results").glob("intent_bench_????-??-??.json"))
    assert results, "no benchmark result committed"
    summary = json.loads(results[-1].read_text(encoding="utf-8"))["summary"]
    defaults = Settings.model_fields
    assert defaults["intent_gate"].default == "jev"
    assert defaults["intent_jev_threshold"].default == summary["thresholds"]["jev_names"]["threshold"]
    assert defaults["decision_model"].default == summary["models"]["jev"]


def test_trace_metadata_carries_the_gate_and_probability():
    from api.routes.chat import _intent_metadata
    assert _intent_metadata({"intent": "off_topic", "gate": "jev", "p_on_topic": 0.12345, "turn": "followup"}) == \
        {"intent_gate": "jev", "turn": "followup", "p_on_topic": 0.123}
    assert _intent_metadata({"intent": "job_related", "confidence": 0.9}) == {"intent_gate": "gemini", "turn": "first"}


def test_first_messages_get_version_2_unchanged_and_later_ones_the_previous_turn():
    from services.intent import CONTEXT_SENTENCE, TURN_SENTENCE
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return _answer(0.6)(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gate = JevIntentGate(client, "key", "typesafe/jev-1.14-20261014", threshold=0.75, followup_threshold=0.4,
                         names=["3E"], timeout=1.0)
    first = asyncio.run(gate.classify("How long?"))
    prev = {"visitor": "What did Rayhan do at 3E?", "assistant": "He built the memory scoreboard."}
    later = asyncio.run(gate.classify("How long?", prev))
    # first message: exactly version 2's request, version 2's threshold (0.6 < 0.75 is refused)
    assert "previous_turn" not in seen[0]["state"]
    assert seen[0]["questions"]["on_topic"]["instructions"] == JEV_INTENT_QUESTION["instructions"] + CONTEXT_SENTENCE
    assert first["intent"] == "off_topic" and first["turn"] == "first"
    # later message: the previous turn and the extra sentence, judged against its own threshold (0.6 >= 0.4)
    assert seen[1]["state"] == {"previous_turn": prev, "visitor_message": "How long?", "names_in_rayhans_profile": ["3E"]}
    assert seen[1]["questions"]["on_topic"]["instructions"] == JEV_INTENT_QUESTION["instructions"] + CONTEXT_SENTENCE + TURN_SENTENCE
    assert seen[1]["questions"]["on_topic"]["criteria"] == JEV_INTENT_QUESTION["criteria"]
    assert later["intent"] == "job_related" and later["turn"] == "followup"


def test_previous_turn_is_the_last_exchange_cut_to_size():
    from services.intent import previous_turn
    assert previous_turn(None) is None and previous_turn([]) is None
    assert previous_turn([{"role": "user", "content": "hi"}]) is None
    history = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "old answer"},
               {"role": "user", "content": "q" * 2000}, {"role": "assistant", "content": "a" * 2000}]
    assert previous_turn(history) == {"visitor": "q" * 1000, "assistant": "a" * 600}


def test_production_fails_open_after_one_second():
    from core.config import Settings
    assert Settings.model_fields["intent_jev_timeout"].default == 1.0
    main = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "fallback=None" in main, "the gate in production should let a message through when the decision model fails"


def test_configured_followup_threshold_is_the_one_the_conversation_benchmark_chose():
    from core.config import Settings
    results = sorted((Path(__file__).resolve().parents[1] / "evals" / "results").glob("gate_bench_????-??-??.json"))
    assert results, "no conversation benchmark committed"
    summary = json.loads(results[-1].read_text(encoding="utf-8"))["summary"]
    assert Settings.model_fields["intent_jev_followup_threshold"].default == summary["thresholds"]["v3"]
    assert summary["ship"]["met"], "the committed result did not meet the preregistered ship rule"


def test_a_declined_message_is_kept_in_the_history():
    """Otherwise the gate would judge the next message against an older answer, not the refusal"""
    import uuid
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes import chat
    from core.session_tokens import SessionTokens
    from core.ttl_cache import TTLCache
    from middleware.rate_limiter import RateLimiter
    from services.quotas import QuotaService

    class _Off:
        def __init__(self): self.previous = []
        async def classify(self, query, previous=None):
            self.previous.append(previous)
            return {"intent": "off_topic", "decline_message": FIXED_DECLINE, "gate": "jev", "p_on_topic": 0.1}

    gate = _Off()
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/v1")
    app.state.session_tokens = SessionTokens("secret")
    app.state.sessions = TTLCache(ttl_seconds=60, max_size=10)
    app.state.quotas = QuotaService(None, messages_per_session=9, messages_per_ip_per_day=99,
                                    sessions_per_ip_per_day=99, daily_token_budget=10**9, salt="s")
    app.dependency_overrides[chat.get_rate_limiter] = lambda: RateLimiter(requests_per_minute=100)
    app.dependency_overrides[chat.get_supabase_service] = lambda: None
    app.dependency_overrides[chat.get_intent_classifier] = lambda: gate
    app.dependency_overrides[chat.get_tracing_service] = lambda: None
    app.dependency_overrides[chat.get_agent_service] = lambda: None
    sid = str(uuid.uuid4())
    app.state.sessions[sid] = {"id": sid, "user_name": "Test", "preferences": {"ip_hash": "abc", "history": [
        {"role": "user", "content": "What did Rayhan do at 3E?"}, {"role": "assistant", "content": "He built the scoreboard."}]}}
    token = app.state.session_tokens.issue(sid)
    client = TestClient(app)
    for path, query in (("/api/v1/chat", "Write me a poem."), ("/api/v1/chat/stream", "Please, just one.")):
        assert client.post(path, json={"session_id": sid, "session_token": token, "query": query}).status_code == 200
    assert gate.previous[0] == {"visitor": "What did Rayhan do at 3E?", "assistant": "He built the scoreboard."}
    assert gate.previous[1] == {"visitor": "Write me a poem.", "assistant": FIXED_DECLINE}


def test_drift_check_reports_the_average_change():
    from services.intent import CANARY, canary_drift
    recorded = {(t, c): p for t, c, p in CANARY}

    def same(request):
        body = json.loads(request.content)
        ctx = next((c for (t, c) in recorded if t == body["state"]["visitor_message"]
                    and (c is None) == ("previous_turn" not in body["state"])), None)
        return _answer(recorded[(body["state"]["visitor_message"], ctx)])(request)

    assert asyncio.run(canary_drift(_gate(same))) < 1e-9
    assert abs(asyncio.run(canary_drift(_gate(lambda r: same(r) if "religion" not in r.content.decode() else _answer(0.95)(r)))) - 0.21 / len(CANARY)) < 1e-9


def test_drift_check_values_are_the_ones_the_benchmarks_recorded():
    from services.intent import CANARY
    results = Path(__file__).resolve().parents[1] / "evals" / "results"
    first = {r["text"]: r["jev_names_p"] for r in json.loads(sorted(results.glob("intent_bench_????-??-??.json"))[-1].read_text())["rows"]}
    later = {(r["text"], r["context"]): r["v3_p"] for r in json.loads(sorted(results.glob("gate_bench_????-??-??.json"))[-1].read_text())["rows"]}
    for text, ctx, p in CANARY:
        assert abs((first[text] if ctx is None else later[(text, ctx)]) - p) < 1e-9, text
