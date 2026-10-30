"""
The decision-model intent gate: a probability compared with a threshold, a zero-data-retention
request, and two fallbacks (the LLM classifier, then allowing the message), so a failure of the
decision model can never block a real question.
"""
import asyncio
import json

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

    async def classify(self, query):
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
    assert _intent_metadata({"intent": "off_topic", "gate": "jev", "p_on_topic": 0.12345}) == {"intent_gate": "jev", "p_on_topic": 0.123}
    assert _intent_metadata({"intent": "job_related", "confidence": 0.9}) == {"intent_gate": "gemini"}
