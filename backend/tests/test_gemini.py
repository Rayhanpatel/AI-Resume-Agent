"""
Request-shape tests for the Gemini 3.x migration.
No network: these pin down the things that broke in production
(retired model IDs, regional endpoint, prefilled model turns).
"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.vertex_auth import VertexAuthService
from services.gemini import generation_config, extract_text, get_endpoint
from services.agent import AgentService


def _vertex(location):
    auth = VertexAuthService(credentials_json=None, project_id="demo-project", location=location)
    auth.enabled = True  # skip real credentials; only URL building is under test
    return auth


def test_global_endpoint_has_no_region_prefix():
    url = _vertex("global").get_generate_url("gemini-3.8-flash")
    assert url == (
        "https://aiplatform.googleapis.com/v1/projects/demo-project/locations/global/"
        "publishers/google/models/gemini-3.8-flash:generateContent"
    )


def test_regional_endpoint_keeps_region_prefix():
    url = _vertex("us-central1").get_streaming_url("gemini-3.8-flash")
    assert url.startswith("https://us-central1-aiplatform.googleapis.com/")
    assert url.endswith(":streamGenerateContent?alt=sse")


def test_ai_studio_endpoint_when_vertex_disabled():
    url, headers = asyncio.run(get_endpoint(None, "key-123", "gemini-3.5-flash-lite", stream=True))
    assert url.endswith("/gemini-3.5-flash-lite:streamGenerateContent?alt=sse")
    assert headers["x-goog-api-key"] == "key-123"


def test_generation_config_drops_deprecated_sampling_params():
    config = generation_config(max_output_tokens=2048, thinking_level="low", json_mode=True)
    assert "temperature" not in config and "topP" not in config
    assert config["thinkingConfig"] == {"thinkingLevel": "low"}
    assert config["responseMimeType"] == "application/json"


def test_extract_text_skips_thoughts_and_joins_parts():
    data = {"candidates": [{"content": {"parts": [
        {"text": "planning...", "thought": True},
        {"text": "Rayhan built "},
        {"text": "EvoRank.", "thoughtSignature": "abc"},
    ]}}]}
    assert extract_text(data) == "Rayhan built EvoRank."


def test_extract_text_empty_cases():
    assert extract_text({}) == ""
    assert extract_text({"candidates": [{"finishReason": "SAFETY"}]}) == ""


def test_agent_payload_uses_system_instruction_and_no_model_turns():
    agent = AgentService(api_key="k", tracer=None, http_client=None, vertex_auth=None)
    job = {"role_title": "ML Engineer", "company_name": "Acme", "key_skills": ["evals", "Python"]}
    payload = asyncio.run(agent._build_payload("s", "What did he do at 3E?", "Dana", "Acme", job))

    system_text = payload["systemInstruction"]["parts"][0]["text"]
    assert "Rayhan" in system_text
    assert "Role: ML Engineer" in system_text  # the job reaches the model only as the schema
    assert [turn["role"] for turn in payload["contents"]] == ["user"]
    assert payload["contents"][-1]["parts"][0]["text"] == "What did he do at 3E?"


def test_llm_health_is_cached_and_returns_503_on_failure(monkeypatch):
    from api.routes import health

    calls = []

    async def fake_check(http_client, vertex_auth, api_key, model):
        calls.append(model)
        return {"ok": False, "api_mode": "Vertex AI", "model": model, "latency_ms": 5,
                "error": "HTTP 403: billing disabled for project 123"}

    monkeypatch.setattr(health, "check_llm", fake_check)
    monkeypatch.setenv("GOOGLE_API_KEY", "test")

    app = FastAPI()
    app.include_router(health.router, prefix="/api/v1")
    app.state.http_client = None
    app.state.vertex_auth = None

    with TestClient(app) as client:
        first = client.get("/api/v1/health/llm")
        second = client.get("/api/v1/health/llm")

    assert first.status_code == 503 and second.status_code == 503
    assert first.json()["error"] == "HTTP 403"  # no provider error text leaks publicly
    assert len(calls) == 1  # second request served from cache


def test_llm_health_retries_a_failure_after_a_minute(monkeypatch):
    from api.routes import health

    calls = []
    clock = [1000.0]

    async def fake_check(http_client, vertex_auth, api_key, model):
        calls.append(model)
        ok = len(calls) > 1  # first probe fails, the retry passes
        return {"ok": ok, "api_mode": "Vertex AI", "model": model, "latency_ms": 5,
                "error": None if ok else "ReadTimeout: "}

    monkeypatch.setattr(health, "check_llm", fake_check)
    monkeypatch.setattr(health.time, "time", lambda: clock[0])
    monkeypatch.setenv("GOOGLE_API_KEY", "test")

    app = FastAPI()
    app.include_router(health.router, prefix="/api/v1")
    app.state.http_client = None
    app.state.vertex_auth = None

    with TestClient(app) as client:
        first = client.get("/api/v1/health/llm")
        clock[0] += 61
        second = client.get("/api/v1/health/llm")
        clock[0] += 300
        third = client.get("/api/v1/health/llm")

    assert first.status_code == 503 and first.json()["error"] == "ReadTimeout"
    assert second.status_code == 200
    assert third.status_code == 200 and len(calls) == 2  # a pass is cached for 10 minutes


# ── Answer-model fallback ─────────────────────────────────────────────

import json as _json
import httpx


def _sse(text):
    body = "data: " + _json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}) + "\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _agent(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AgentService(api_key="k", tracer=None, http_client=client,
                        vertex_auth=None, model="primary-model", fallback_model="fallback-model",
                        first_token_timeout=0.2)


async def _collect(agent):
    return "".join([c async for c in agent.respond_stream("s", "What did he build?", "Dana")])


def test_stream_falls_back_when_the_answer_model_errors():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(503, text="overloaded") if "primary-model" in request.url.path else _sse("From the fallback.")

    assert asyncio.run(_collect(_agent(handler))) == "From the fallback."
    assert any("primary-model" in p for p in seen) and any("fallback-model" in p for p in seen)


def test_stream_falls_back_when_the_answer_model_stalls():
    def handler(request):
        if "primary-model" in request.url.path:
            raise httpx.ReadTimeout("no first token", request=request)
        return _sse("Fast answer.")

    assert asyncio.run(_collect(_agent(handler))) == "Fast answer."


def test_stream_apologizes_when_every_model_fails():
    def handler(request):
        return httpx.Response(500, text="down")

    assert "rayhanbp@umd.edu" in asyncio.run(_collect(_agent(handler)))


def test_non_streaming_falls_back_and_reports_the_model():
    def handler(request):
        if "primary-model" in request.url.path:
            return httpx.Response(429, text="quota")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "OK from fallback"}]}}]})

    result = asyncio.run(_agent(handler).respond("s", "q", "Dana"))
    assert result["response"] == "OK from fallback" and result["model"] == "fallback-model"


def test_stream_retries_the_fallback_when_it_returns_no_text():
    calls = {"fallback": 0}

    def handler(request):
        if "primary-model" in request.url.path:
            return httpx.Response(429, text="resource exhausted")
        calls["fallback"] += 1
        if calls["fallback"] == 1:  # first fallback answer is empty (MALFORMED_RESPONSE)
            body = "data: " + _json.dumps({"candidates": [{"finishReason": "MALFORMED_RESPONSE"}]}) + "\n\n"
            return httpx.Response(200, text=body)
        return _sse("Second try worked.")

    assert asyncio.run(_collect(_agent(handler))) == "Second try worked."
    assert calls["fallback"] == 2
