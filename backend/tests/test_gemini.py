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


class _NoMemory:
    async def get_semantic_context(self, session_id, query, limit=5):
        return "- user asked about agent memory"


def test_agent_payload_uses_system_instruction_and_no_model_turns():
    agent = AgentService(api_key="k", memory=_NoMemory(), tracer=None, http_client=None, vertex_auth=None)
    payload = asyncio.run(agent._build_payload("s", "What did he do at 3E?", "Dana", "Acme", None))

    assert "Rayhan" in payload["systemInstruction"]["parts"][0]["text"]
    assert "agent memory" in payload["systemInstruction"]["parts"][0]["text"]  # memory goes in the system text
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
