"""
The chat route counts every message in memory, so the quota fallback has data when
Supabase is configured but not answering (Oct 2026: the host stopped resolving and
every limit silently read zero).
"""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import chat
from core.session_tokens import SessionTokens
from core.ttl_cache import TTLCache
from middleware.rate_limiter import RateLimiter
from services.quotas import QuotaService


class _DownSupabase:
    """Configured (enabled) but every call fails, like a project whose host no longer resolves"""
    enabled = True

    @property
    def client(self):
        raise OSError("[Errno -2] Name or service not known")

    async def get_session(self, session_id): return None
    async def update_session_activity(self, session_id): return False
    async def update_preferences(self, session_id, prefs): return False
    async def log_event(self, **kwargs): return False


class _OffTopic:
    async def classify(self, query, previous=None):
        return {"intent": "off_topic", "decline_message": "I only answer questions about Rayhan."}


def test_messages_are_limited_while_supabase_is_unreachable():
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/v1")
    down = _DownSupabase()
    app.state.session_tokens = SessionTokens("secret")
    app.state.sessions = TTLCache(ttl_seconds=60, max_size=10)
    app.state.supabase_service = down
    app.state.quotas = QuotaService(down, messages_per_session=2, messages_per_ip_per_day=99,
                                    sessions_per_ip_per_day=99, daily_token_budget=10**9, salt="s")
    app.dependency_overrides[chat.get_rate_limiter] = lambda: RateLimiter(requests_per_minute=100)
    app.dependency_overrides[chat.get_supabase_service] = lambda: down
    app.dependency_overrides[chat.get_intent_classifier] = lambda: _OffTopic()
    app.dependency_overrides[chat.get_tracing_service] = lambda: None
    app.dependency_overrides[chat.get_agent_service] = lambda: None

    sid = str(uuid.uuid4())
    app.state.sessions[sid] = {"id": sid, "user_name": "Test", "preferences": {"ip_hash": "abc"}}
    body = {"session_id": sid, "session_token": app.state.session_tokens.issue(sid), "query": "What's the weather?"}
    client = TestClient(app)
    assert client.post("/api/v1/chat", json=body).status_code == 200
    assert client.post("/api/v1/chat", json=body).status_code == 200
    third = client.post("/api/v1/chat", json=body)
    assert third.status_code == 429, third.text
