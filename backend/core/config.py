"""
Configuration management for Rayhan's AI Resume Chatbot
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Pydantic v2 config
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )
    
    # Gemini API (AI Studio fallback)
    google_api_key: str
    
    # Vertex AI (Production)
    google_cloud_project: Optional[str] = None
    google_application_credentials_json: Optional[str] = None

    # Gemini models (Google retires model IDs; change these, not the code)
    # gemini-2.0-flash was retired on Vertex Jun 1 2026, gemini-2.5-* on Oct 20 2026.
    # gemini-3.8-flash was the first choice, but on Oct 25 2026 it took 30-45 s per answer
    # while 3.5 Flash started streaming in about 1 s. Measure before changing these.
    gemini_model: str = "gemini-3.5-flash"                     # answers recruiters
    gemini_fallback_model: str = "gemini-3.5-flash-lite"       # used if the answer model fails or stalls
    gemini_first_token_timeout: float = 10.0                   # seconds before switching to the fallback
    gemini_fast_model: str = "gemini-3.5-flash-lite"           # intent, job parsing, starter prompts
    gemini_thinking_level: str = "low"                # low | medium | high
    # Gemini 3.x models are served from the global endpoint, not us-central1
    gemini_location: str = "global"
    
    # Supabase
    supabase_url: Optional[str] = None
    supabase_key: Optional[str] = None
    
    # Mem0 (optional — memory disabled if absent)
    mem0_api_key: Optional[str] = None
    admin_api_key: Optional[str] = None
    
    # Langfuse (Optional)
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com", 
        validation_alias="LANGFUSE_HOST"
    )
    
    # Integrations
    google_sheet_webhook: Optional[str] = None
    turnstile_secret_key: Optional[str] = None
    
    # App Config
    environment: str = "development"
    frontend_url: str = "http://localhost:5173"
    rate_limit_rpm: int = 10

    # Sessions: /session issues a signed token after Turnstile; /chat requires it.
    # Without SESSION_SECRET a random one is made at startup (tokens die on restart).
    session_secret: Optional[str] = None
    session_ttl_hours: int = 24

    # Quotas, counted from the events table so they survive restarts and replicas
    quota_messages_per_session: int = 40
    quota_messages_per_ip_per_day: int = 80
    quota_sessions_per_ip_per_day: int = 15
    # All conversations, input + output, per UTC day. About 50 turns: it stops the bot politely
    # well before the $10/month GCP spend cap would stop it hard until the 1st.
    quota_daily_token_budget: int = 500_000
    # MCP's share: about 10 answers a day. MCP has no bot check, so it gets its own, smaller budget.
    quota_mcp_daily_token_budget: int = 100_000
    # Visitor chats (Supabase rows and Langfuse traces) are deleted after this many idle days
    data_retention_days: int = 90

    # Verification stamps on every answer (a fast-model JSON pass plus code checks)
    verify_answers: bool = True
    # Optional second opinion from a decision model on OpenRouter's Decisions API.
    # Pinned to a dated snapshot: probabilities drift between versions, so thresholds are per model.
    openrouter_api_key: Optional[str] = None
    decision_model: str = "typesafe/jev-1.14-20261014"
    # Intent gate: "gemini" (the LLM classifier) or "jev" (the decision model above, with the LLM
    # classifier as its fallback). The threshold belongs to the frozen question in services/intent.py
    # and was chosen on the dev half of evals/intent_set.jsonl (evals/intent_bench.py).
    # Off-topic gate: "jev" = TypeSafe's decision model with the profile's names (needs OPENROUTER_API_KEY;
    # falls back to the LLM classifier if it fails); "gemini" = the Flash-Lite classifier. The threshold was
    # chosen on the dev half of evals/intent_set.jsonl and belongs to that model version and question.
    intent_gate: str = "jev"
    intent_jev_threshold: float = 0.75            # first messages (evals/intent_bench.py)
    intent_jev_followup_threshold: float = 0.5    # messages after a turn, which the gate also sees (evals/gate_bench.py)
    intent_jev_timeout: float = 1.0               # p99 was 0.53 s; after that the answer model decides

    # When set, every request except /health* must carry X-Origin-Secret with this value.
    # Cloudflare adds the header once the API is proxied through it, so direct hits are refused.
    origin_secret: Optional[str] = None


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    try:
        return Settings()
    except Exception as e:
        if "google_api_key" in str(e):
            raise SystemExit(
                "\n❌ GOOGLE_API_KEY is required!\n"
                "   1. Get a free key at: https://aistudio.google.com/apikey\n"
                "   2. Copy backend/.env.example to backend/.env\n"
                "   3. Paste your key after GOOGLE_API_KEY=\n"
            )
        raise
