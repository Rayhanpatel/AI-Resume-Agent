import time
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.config import get_settings
from services.gemini import check_llm

logger = logging.getLogger(__name__)
router = APIRouter()

APP_VERSION = "2.6.0"
LLM_CHECK_TTL_SECONDS = 600         # a passing check is reused for 10 minutes
LLM_CHECK_FAILURE_TTL_SECONDS = 60  # a failing one is retried after a minute


@router.get("/health")
async def health_check(request: Request):
    """Liveness check (config flags only). Railway's healthcheck uses this, so it never calls the model."""
    state = request.app.state
    return {
        "status": "healthy",
        "version": APP_VERSION,
        "services": {
            "vertex_ai": hasattr(state, "vertex_auth") and state.vertex_auth.enabled,
            "intent": getattr(state, "intent_classifier", None) is not None,
            "agent": getattr(state, "agent_service", None) is not None,
            "quotas_persistent": hasattr(state, "quotas") and state.quotas.persistent,
            "tracing": hasattr(state, "tracing_service") and state.tracing_service.enabled,
            "supabase": hasattr(state, "supabase_service") and state.supabase_service.enabled
        }
    }


@router.get("/health/llm")
async def llm_health_check(request: Request):
    """
    Readiness check: does the answer model actually respond, and does the database?
    Returns 503 when the model doesn't, so an uptime monitor can alert; the
    database state is reported in the body (the uptime workflow checks it).
    Cached so the public endpoint can't be used to run up the bill.
    """
    state = request.app.state
    cached = getattr(state, "llm_check_cache", None)
    ttl = LLM_CHECK_TTL_SECONDS if cached and cached["ok"] else LLM_CHECK_FAILURE_TTL_SECONDS
    if cached and time.time() - cached["checked_at"] < ttl:
        result = cached
    else:
        settings = get_settings()
        result = await check_llm(state.http_client, state.vertex_auth, settings.google_api_key, settings.gemini_model)
        # Quotas, history and lead logging depend on the database; the chat still answers without it
        result["database"] = await state.supabase_service.ping() if hasattr(state, "supabase_service") else "off"
        result["checked_at"] = time.time()
        state.llm_check_cache = result
        if not result["ok"]:
            logger.error(f"[HEALTH] LLM check failed: {result['error']}")

    body = {
        "status": "ok" if result["ok"] else "failing",
        "model": result["model"],
        "api_mode": result["api_mode"],
        "latency_ms": result["latency_ms"],
        "database": result.get("database", "off"),
        "checked_seconds_ago": int(time.time() - result["checked_at"]),
    }
    if not result["ok"]:
        # Only the status line goes out publicly; the full error is in the logs
        body["error"] = (result["error"] or "").split(":")[0]
    return JSONResponse(status_code=200 if result["ok"] else 503, content=body)
