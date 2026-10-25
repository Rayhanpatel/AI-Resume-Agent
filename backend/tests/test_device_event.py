"""The on-device telemetry endpoint accepts counts only, and only with a valid session token."""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import chat
from core.session_tokens import SessionTokens
from middleware.rate_limiter import RateLimiter


def _client():
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/v1")
    app.state.session_tokens = SessionTokens("secret")
    app.state.supabase_service = type("S", (), {"enabled": False})()
    app.dependency_overrides[chat.get_rate_limiter] = lambda: RateLimiter(requests_per_minute=100)
    app.dependency_overrides[chat.get_supabase_service] = lambda: app.state.supabase_service
    return app, TestClient(app)


def test_device_event_requires_a_real_token_and_only_counts():
    app, client = _client()
    sid = str(uuid.uuid4())
    good = app.state.session_tokens.issue(sid)
    body = {"session_id": sid, "session_token": good, "outcome": "answered", "latency_ms": 820,
            "quotes": 3, "quotes_verified": 3, "blocks": 6, "chars": 12000}
    assert client.post("/api/v1/chat/device-event", json=body).status_code == 200
    assert client.post("/api/v1/chat/device-event", json={**body, "session_token": good[:-2] + "zz"}).status_code == 403
    # free text is not accepted anywhere in the payload
    assert client.post("/api/v1/chat/device-event", json={**body, "outcome": "the user asked about salary"}).status_code == 422
