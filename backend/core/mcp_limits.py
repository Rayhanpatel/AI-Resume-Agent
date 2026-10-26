"""
Limits for /mcp.

The MCP tools call the model without a chat session, so the session route's
Turnstile check and quotas never ran for them. This wrapper applies the same
per-IP rate limit, per-IP daily limit and global daily token budget to every
POST before it reaches the MCP app. Each call counts once, whatever its method:
simpler than trusting the client's mcp-method header, and the limits are generous
enough for tools/list and initialize to fit.
"""
import asyncio
import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from middleware.rate_limiter import RateLimiter
from services.quotas import QuotaExceeded, hash_ip

logger = logging.getLogger(__name__)


class MCPLimits:
    def __init__(self, app, host, requests_per_minute: int):
        self.app = app
        self.host = host  # the FastAPI app; its state is read per request, after the lifespan has run
        self.limiter = RateLimiter(requests_per_minute=requests_per_minute)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)

        state = self.host.state
        request = Request(scope)
        ip = self.limiter.get_client_ip(request)
        ip_hash = hash_ip(ip, state.quotas.salt)
        caller = f"mcp:{ip_hash}"
        try:
            self.limiter.check(ip)
            await state.quotas.check_mcp(caller, ip_hash)
        except HTTPException as e:
            return await JSONResponse(status_code=e.status_code, content={"error": "Rate limit exceeded"})(scope, receive, send)
        except QuotaExceeded as e:
            logger.warning(f"MCP quota reached ({e.scope})")
            return await JSONResponse(status_code=429, content={"error": e.message})(scope, receive, send)

        state.quotas.record_memory("messages_session", caller)
        state.quotas.record_memory("messages_ip", ip_hash)
        supabase = getattr(state, "supabase_service", None)
        if supabase and supabase.enabled:
            asyncio.create_task(supabase.log_event(session_id=caller, event_type="mcp", metadata={"ip_hash": ip_hash}))
        return await self.app(scope, receive, send)


def record_tool_tokens(app_state, result: dict):
    """Count an MCP tool's model tokens toward the global daily budget"""
    tokens_in, tokens_out = result.get("tokens_in") or 0, result.get("tokens_out") or 0
    app_state.quotas.record_memory("tokens", "all", tokens_in + tokens_out)
    app_state.quotas.record_memory("tokens", "mcp_tokens", tokens_in + tokens_out)
    supabase = getattr(app_state, "supabase_service", None)
    if supabase and supabase.enabled:
        asyncio.create_task(supabase.log_event(session_id="mcp", event_type="mcp_tokens", tokens_in=tokens_in, tokens_out=tokens_out))
