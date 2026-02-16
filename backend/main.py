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
from core.middleware import RequestIDMiddleware
from core.ttl_cache import TTLCache
from api.router import api_router
from services import (
    IntentClassifier, 
    AgentService, 
    MemoryService, 
    TracingService, 
    SupabaseService,
    init_vertex_auth
)

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
    
    # Initialize Vertex AI Auth (production) - falls back to AI Studio if not configured
    app.state.vertex_auth = init_vertex_auth(
        credentials_json=settings.google_application_credentials_json,
        project_id=settings.google_cloud_project,
        location=settings.google_cloud_location
    )
    
    # Initialize Services
    app.state.supabase_service = await SupabaseService.create(
        url=settings.supabase_url or "",
        key=settings.supabase_key or ""
    )
    app.state.intent_classifier = IntentClassifier(
        api_key=settings.google_api_key,
        http_client=app.state.http_client,
        vertex_auth=app.state.vertex_auth
    )
    app.state.memory_service = MemoryService(api_key=settings.mem0_api_key or "")
    app.state.tracing_service = TracingService(
        public_key=settings.langfuse_public_key or "",
        secret_key=settings.langfuse_secret_key or "",
        host=settings.langfuse_host
    )
    app.state.agent_service = AgentService(
        api_key=settings.google_api_key,
        memory=app.state.memory_service,
        tracer=app.state.tracing_service,
        http_client=app.state.http_client,
        vertex_auth=app.state.vertex_auth
    )
    
    # In-memory sessions storage with TTL and size limit to prevent memory leaks
    app.state.sessions = TTLCache(ttl_seconds=86400, max_size=10000)  # 24h TTL, 10k max
    
    # Test Gemini API connection at startup (uses active API mode)
    logger.warning("Testing Gemini API connection...")
    try:
        if app.state.vertex_auth.enabled:
            url = app.state.vertex_auth.get_generate_url("gemini-2.0-flash")
            headers = await app.state.vertex_auth.get_headers()
            api_mode = "Vertex AI"
        else:
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
            headers = {"x-goog-api-key": settings.google_api_key}
            api_mode = "AI Studio"
        
        payload = {"contents": [{"role": "user", "parts": [{"text": "Say hello in 3 words"}]}]}
        response = await app.state.http_client.post(url, json=payload, headers=headers, timeout=10.0)
        if response.status_code == 200:
            text = response.json()['candidates'][0]['content']['parts'][0]['text']
            logger.warning(f"✅ Gemini API ({api_mode}) works: {text}")
        else:
            logger.error(f"❌ Gemini API ({api_mode}) Error: {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"❌ Gemini API FAILED at startup: {str(e)}")
    
    # Log service status for debugging
    logger.warning("=" * 50)
    logger.warning("SERVICE STATUS:")
    logger.warning(f"  {'✓' if app.state.vertex_auth.enabled else '✗'} Vertex AI: {'Enabled (production)' if app.state.vertex_auth.enabled else 'Disabled (using AI Studio)'}")
    logger.warning(f"  ✓ Intent Classifier: Ready")
    logger.warning(f"  ✓ Agent Service: Ready")
    logger.warning(f"  {'✓' if app.state.memory_service.enabled else '✗'} Memory (Mem0): {'Enabled' if app.state.memory_service.enabled else 'Disabled'}")
    logger.warning(f"  {'✓' if app.state.tracing_service.enabled else '✗'} Tracing (Langfuse): {'Enabled' if app.state.tracing_service.enabled else 'Disabled'}")
    logger.warning(f"  {'✓' if app.state.supabase_service.enabled else '✗'} Database (Supabase): {'Enabled' if app.state.supabase_service.enabled else 'Disabled'}")
    logger.warning("=" * 50)
    
    logger.info("All services initialized and attached to state")
    yield
    
    # Cleanup
    if hasattr(app.state, "tracing_service") and app.state.tracing_service:
        app.state.tracing_service.flush()

    if hasattr(app.state, "supabase_service") and app.state.supabase_service:
        await app.state.supabase_service.close()
    
    if hasattr(app.state, "http_client"):
        await app.state.http_client.aclose()
        
    logger.info("Services shut down")

app = FastAPI(
    title="Rayhan's AI Resume Chatbot",
    description="Production-grade modular API for AI resume interactions",
    version="2.3.1-vertex",
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"https://ai-resume-agent.*\.vercel\.app",  # Allow Vercel previews
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
