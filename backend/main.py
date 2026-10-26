import os
import time
import logging
from contextlib import asynccontextmanager
import httpx

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from core.config import get_settings
from core import telemetry
from core.middleware import RequestIDMiddleware
from core.ttl_cache import TTLCache
from api.router import api_router
from api.routes.health import APP_VERSION
from services.verifier import PROFILE_VERSION
from core.origin import OriginSecretMiddleware
from core.session_tokens import SessionTokens
from core.mcp_limits import MCPLimits
from services import (
    IntentClassifier,
    AgentService,
    TracingService,
    SupabaseService,
    init_vertex_auth
)
from services.gemini import check_llm
from services.quotas import QuotaService
from services.verifier import GeminiJsonVerifier, DecisionsGate
import mcp_server

# Load environment variables
load_dotenv()

# Setup logging
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup and attach to app state"""
    settings = get_settings()
    
    # Initialize Singleton HTTP Client
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        http2=True,
    )
    telemetry.instrument_http_client(app.state.http_client)
    
    # Initialize Vertex AI Auth (production) - falls back to AI Studio if not configured
    app.state.vertex_auth = init_vertex_auth(
        credentials_json=settings.google_application_credentials_json,
        project_id=settings.google_cloud_project,
        location=settings.gemini_location
    )
    
    # Initialize Services
    app.state.supabase_service = await SupabaseService.create(
        url=settings.supabase_url or "",
        key=settings.supabase_key or ""
    )
    telemetry.watch_database(app.state.supabase_service)
    app.state.intent_classifier = IntentClassifier(
        api_key=settings.google_api_key,
        http_client=app.state.http_client,
        vertex_auth=app.state.vertex_auth,
        model=settings.gemini_fast_model,
        thinking_level=settings.gemini_thinking_level
    )
    app.state.tracing_service = TracingService(
        public_key=settings.langfuse_public_key or "",
        secret_key=settings.langfuse_secret_key or "",
        host=settings.langfuse_host,
        environment=settings.environment,
        release=APP_VERSION,
        version=PROFILE_VERSION
    )
    app.state.agent_service = AgentService(
        api_key=settings.google_api_key,
        tracer=app.state.tracing_service,
        http_client=app.state.http_client,
        vertex_auth=app.state.vertex_auth,
        model=settings.gemini_model,
        thinking_level=settings.gemini_thinking_level,
        fallback_model=settings.gemini_fallback_model,
        first_token_timeout=settings.gemini_first_token_timeout
    )

    # In-memory sessions storage with TTL and size limit to prevent memory leaks
    app.state.sessions = TTLCache(ttl_seconds=86400, max_size=10000)  # 24h TTL, 10k max

    # Signed session tokens and quotas that survive restarts (counted from the events table)
    app.state.session_tokens = SessionTokens(settings.session_secret, settings.session_ttl_hours)
    app.state.quotas = QuotaService(
        app.state.supabase_service,
        messages_per_session=settings.quota_messages_per_session,
        messages_per_ip_per_day=settings.quota_messages_per_ip_per_day,
        sessions_per_ip_per_day=settings.quota_sessions_per_ip_per_day,
        daily_token_budget=settings.quota_daily_token_budget,
        salt=settings.session_secret or "no-secret",
    )

    # Verification stamps: fast-model verdicts + code checks, optional decision-model second opinion
    app.state.verifier = GeminiJsonVerifier(
        app.state.http_client, app.state.vertex_auth, settings.google_api_key, settings.gemini_fast_model
    )
    app.state.decision_gate = (
        DecisionsGate(app.state.http_client, settings.openrouter_api_key, settings.decision_model)
        if settings.openrouter_api_key else None
    )

    # MCP tools that need the running services
    mcp_server.attach_agent_tools(app.state)

    # Test Gemini API connection at startup (uses active API mode)
    logger.warning("Testing Gemini API connection...")
    llm_check = await check_llm(app.state.http_client, app.state.vertex_auth, settings.google_api_key, settings.gemini_model)
    if llm_check["ok"]:
        logger.warning(f"✅ Gemini API ({llm_check['api_mode']}, {settings.gemini_model}) works")
    else:
        logger.error(f"❌ Gemini API ({llm_check['api_mode']}, {settings.gemini_model}) FAILED: {llm_check['error']}")
    
    database = await app.state.supabase_service.ping()

    # Log service status for debugging
    logger.warning("=" * 50)
    logger.warning("SERVICE STATUS:")
    logger.warning(f"  {'✓' if app.state.vertex_auth.enabled else '✗'} Vertex AI: {'Enabled (production)' if app.state.vertex_auth.enabled else 'Disabled (using AI Studio)'}")
    logger.warning(f"  ✓ Intent Classifier: Ready")
    logger.warning(f"  ✓ Agent Service: Ready")
    logger.warning(f"  {'✓' if settings.session_secret else '✗'} Session secret: {'set' if settings.session_secret else 'random (set SESSION_SECRET)'}")
    logger.warning(f"  {'✓' if database == 'ok' else '✗'} Quotas: {'persistent (events table)' if database == 'ok' else 'in memory (database ' + database + ')'}")
    logger.warning(f"  {'✓' if settings.origin_secret else '✗'} Origin secret: {'enforced' if settings.origin_secret else 'off (API reachable directly)'}")
    logger.warning(f"  {'✓' if app.state.decision_gate else '✗'} Decision gate: {settings.decision_model if app.state.decision_gate else 'off (set OPENROUTER_API_KEY)'}")
    logger.warning(f"  {'✓' if app.state.tracing_service.enabled else '✗'} Tracing (Langfuse): {'Enabled' if app.state.tracing_service.enabled else 'Disabled'}")
    logger.warning(f"  {'✓' if database == 'ok' else '✗'} Database (Supabase): {database}")
    logger.warning(f"  {'✓' if telemetry.enabled() else '✗'} Telemetry (OpenTelemetry): {'exporting over OTLP' if telemetry.enabled() else 'off (set OTEL_EXPORTER_OTLP_ENDPOINT)'}")
    logger.warning("=" * 50)
    
    logger.info("All services initialized and attached to state")
    async with mcp_server.mcp.session_manager.run():
        yield
    
    # Cleanup
    if hasattr(app.state, "tracing_service") and app.state.tracing_service:
        app.state.tracing_service.shutdown()

    if hasattr(app.state, "supabase_service") and app.state.supabase_service:
        await app.state.supabase_service.close()
    
    if hasattr(app.state, "http_client"):
        await app.state.http_client.aclose()

    logger.info("Services shut down")
    telemetry.shutdown()  # last, so the shutdown logs and final spans go out

# OpenTelemetry: off unless OTEL_EXPORTER_OTLP_ENDPOINT is set (see core/telemetry.py)
telemetry.setup(version=APP_VERSION, environment=get_settings().environment)

mcp_app = mcp_server.build_asgi_app()

app = FastAPI(
    title="Rayhan's AI Resume Chatbot",
    description="Production-grade modular API for AI resume interactions",
    version=APP_VERSION,
    lifespan=lifespan
)

# Middleware
settings = get_settings()
allowed_origins = [
    "http://localhost:5173",
    "https://ai-resume-agent.vercel.app",
    "https://www.ai-resume-agent.vercel.app",
    "https://chat.rayhanpatel.com",
    "https://rayhanpatel.com",
]

# Add FRONTEND_URL from env if set
FRONTEND_URL = os.getenv("FRONTEND_URL")
if FRONTEND_URL:
    allowed_origins.append(FRONTEND_URL)

logger.info(f"CORS Allowed Origins: {allowed_origins}")

# Innermost, so it only sees requests the origin lock and CORS let through
app.add_middleware(telemetry.TelemetryMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"https://ai-resume-agent.*\.vercel\.app",  # Allow Vercel previews
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(OriginSecretMiddleware, secret=settings.origin_secret)
telemetry.instrument_app(app)

# Exception Handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail if isinstance(exc.detail, str) else "Error",
            "code": f"HTTP_{exc.status_code}",
            "details": exc.detail if isinstance(exc.detail, dict) else None
        }
    )

# Include Routers
app.include_router(api_router, prefix="/api/v1")

# MCP interface (Streamable HTTP, stateless). Built at import time; its session manager runs in the lifespan above.
# Wrapped so the per-IP limits and the daily token budget apply to MCP calls too.
app.mount("/mcp", MCPLimits(mcp_app, app, requests_per_minute=settings.rate_limit_rpm))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
