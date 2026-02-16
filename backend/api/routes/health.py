from fastapi import APIRouter, Request
from typing import Dict

router = APIRouter()

@router.get("/health")
async def health_check(request: Request):
    """Health check endpoint"""
    state = request.app.state
    return {
        "status": "healthy",
        "version": "2.3.1-vertex",
        "services": {
            "vertex_ai": hasattr(state, "vertex_auth") and state.vertex_auth.enabled,
            "intent": getattr(state, "intent_classifier", None) is not None,
            "agent": getattr(state, "agent_service", None) is not None,
            "memory": hasattr(state, "memory_service") and state.memory_service.enabled,
            "tracing": hasattr(state, "tracing_service") and state.tracing_service.enabled,
            "supabase": hasattr(state, "supabase_service") and state.supabase_service.enabled
        }
    }
