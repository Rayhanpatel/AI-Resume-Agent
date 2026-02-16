from fastapi import Request, HTTPException, Header, Depends
import secrets
from core.config import get_settings, Settings
from services.supabase_service import SupabaseService
from middleware.rate_limiter import get_rate_limiter as _get_rate_limiter, RateLimiter

def get_settings_dep() -> Settings:
    return get_settings()

def get_supabase_service(request: Request) -> SupabaseService:
    service = getattr(request.app.state, "supabase_service", None)
    if not service:
        # Service not initialized at all (should rarely happen if main.py is correct)
        # But if it is None, we can't return it as SupabaseService type safely ideally, 
        # yet the app handles None in some places. 
        # However, main.py ensures it is at least an instance (possibly disabled).
        # SAFEGUARD: If truly missing, we allow 503 or return None if type allows.
        # But 'getattr(..., None)' suggests it can be None.
        # Let's return False-y or None if missing, or raise if critical. 
        # Original code raised 503 if not service. We keep that part.
        raise HTTPException(status_code=503, detail="Supabase service not initialized")
    return service

async def verify_admin(
    x_admin_key: str = Header(..., alias="X-Admin-Key"), 
    settings: Settings = Depends(get_settings_dep)
):
    # Use constant-time comparison to prevent timing attacks
    if not settings.admin_api_key or not secrets.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return True

def get_agent_service(request: Request):
    return request.app.state.agent_service

def get_intent_classifier(request: Request):
    return request.app.state.intent_classifier

def get_memory_service(request: Request):
    return request.app.state.memory_service

def get_tracing_service(request: Request):
    return request.app.state.tracing_service

def get_rate_limiter() -> RateLimiter:
    """Get rate limiter with config-based RPM"""
    return _get_rate_limiter()
