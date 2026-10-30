import time
import asyncio
import json
import logging
from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from core.timeout import safe_timeout
from models.schemas import ChatRequest, ChatResponse, DeviceEventRequest
from api.deps import get_agent_service, get_intent_classifier, get_tracing_service, get_supabase_service, get_rate_limiter
from services.quotas import QuotaExceeded, hash_ip
from services.verifier import verify_answer
from services.tracer import visitor_id, trace_tags
from core.config import get_settings
from core import telemetry

logger = logging.getLogger(__name__)
router = APIRouter()

# Constants
MAX_MESSAGE_LENGTH = 4000
HISTORY_TURNS = 10  # recent messages kept per session; matches MAX_HISTORY_TURNS in the agent

# SSE headers for Cloudflare/proxy compatibility
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "X-Accel-Buffering": "no",  # Disable nginx/Cloudflare buffering
    "Connection": "keep-alive",
}

SESSION_INVALID = {"error": "This chat session has expired. Please start a new chat.", "code": "SESSION_INVALID"}


async def load_session(request: Request, body: ChatRequest) -> dict:
    """
    Return the server-side session, or 403.
    The token must have been issued by /session for this exact session ID; the
    old code created a session for any UUID it was handed.
    """
    state = request.app.state
    if not state.session_tokens.verify(body.session_token, body.session_id):
        raise HTTPException(status_code=403, detail=SESSION_INVALID)

    session = None
    if hasattr(state, "supabase_service") and state.supabase_service.enabled:
        session = await safe_timeout(state.supabase_service.get_session(body.session_id), timeout=3.0, label="supabase.get_session")
        if session:
            asyncio.create_task(state.supabase_service.update_session_activity(body.session_id))
    if session is None:
        session = state.sessions.get(body.session_id)
    if session is None:
        raise HTTPException(status_code=403, detail=SESSION_INVALID)
    if not session.get("preferences"):
        session["preferences"] = {}
    return session


async def remember_turn(request: Request, session: dict, query: str, answer: str):
    """Append the exchange to the server-side history (last HISTORY_TURNS messages)"""
    if not answer:
        return
    prefs = session.get("preferences") or {}
    history = list(prefs.get("history") or [])
    history += [{"role": "user", "content": query[:4000]}, {"role": "assistant", "content": answer[:4000]}]
    history = history[-HISTORY_TURNS:]
    prefs["history"] = history
    session["preferences"] = prefs
    state = request.app.state
    if session["id"] in state.sessions:
        state.sessions[session["id"]]["preferences"] = prefs
    if hasattr(state, "supabase_service") and state.supabase_service.enabled:
        await state.supabase_service.update_preferences(session["id"], {"history": history})


async def gate(request: Request, body: ChatRequest, session: dict, rate_limiter) -> str:
    """Rate limit, size check, quotas. Returns the IP hash used in event metadata."""
    client_ip = rate_limiter.get_client_ip(request)
    rate_limiter.check(client_ip)
    if len(body.query) > MAX_MESSAGE_LENGTH:
        raise HTTPException(400, f"Message too long (max {MAX_MESSAGE_LENGTH} characters)")
    ip_hash = (session.get("preferences") or {}).get("ip_hash") or hash_ip(client_ip, request.app.state.quotas.salt)
    try:
        await request.app.state.quotas.check_message(body.session_id, ip_hash)
    except QuotaExceeded as e:
        raise HTTPException(status_code=429, detail={"error": e.message, "scope": e.scope})
    return ip_hash


async def _verify(request: Request, answer: str):
    """Stamp the answer's sentences; None when verification is off or the answer is the apology"""
    state = request.app.state
    if not get_settings().verify_answers or not answer or "trouble responding right now" in answer:
        return None
    return await verify_answer(answer, state.verifier, getattr(state, "decision_gate", None))


def _intent_metadata(intent_result) -> dict:
    """Which gate decided and, for the decision model, its probability (to review refusals in Langfuse)"""
    out = {"intent_gate": intent_result.get("gate", "gemini")}
    if intent_result.get("p_on_topic") is not None:
        out["p_on_topic"] = round(intent_result["p_on_topic"], 3)
    return out


def _verification_metadata(verification) -> dict:
    if not verification:
        return {}
    s = verification["summary"]
    claims = s["checked"] + s["inferred"] + s["unknown"] + s["unverified"]
    return {"claims": claims, "checked": s["checked"], "inferred": s["inferred"], "unknown": s["unknown"],
            "numbers_unverified": s["numbers_unverified"], "verify_ms": verification["latency_ms"],
            "quote_support_rate": round(s["checked"] / claims, 3) if claims else None}


def _count_in_memory(request: Request, session_id: str, ip_hash: str, tokens: int = 0):
    """Bookkeeping for the quota fallback, done on every message: Supabase can be configured and still not answer"""
    quotas = request.app.state.quotas
    quotas.record_memory("messages_session", session_id)
    quotas.record_memory("messages_ip", ip_hash)
    if tokens:
        quotas.record_memory("tokens", "all", tokens)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    body: ChatRequest,
    background_tasks: BackgroundTasks,
    agent_service = Depends(get_agent_service),
    intent_classifier = Depends(get_intent_classifier),
    tracing_service = Depends(get_tracing_service),
    supabase_service = Depends(get_supabase_service),
    rate_limiter = Depends(get_rate_limiter)
):
    """Handle a chat message (Non-streaming)"""
    start_time = time.time()
    session = await load_session(request, body)
    ip_hash = await gate(request, body, session, rate_limiter)
    prefs = session.get("preferences") or {}

    trace = None
    if tracing_service:
        trace = tracing_service.trace(
            name="chat_message",
            session_id=body.session_id,
            user_id=visitor_id(ip_hash),
            input=body.query,
            tags=trace_tags("web", synthetic=telemetry.is_synthetic(), stream=False)
        )

    # Intent classification with timeout fallback
    intent_result = await safe_timeout(
        intent_classifier.classify(body.query),
        timeout=15.0,
        default={"intent": "job_related", "confidence": 0.5, "reasoning": "Timeout fallback"},
        label="intent_classification"
    )

    if intent_result["intent"] == "off_topic":
        response_text = intent_result.get("decline_message") or "I'm Rayhan's AI assistant. How can I help you learn about his work?"
        latency_ms = int((time.time() - start_time) * 1000)

        _count_in_memory(request, body.session_id, ip_hash)
        if supabase_service and supabase_service.enabled:
            background_tasks.add_task(
                supabase_service.log_event,
                session_id=body.session_id,
                event_type="chat",
                intent="off_topic",
                latency_ms=latency_ms,
                metadata={"ip_hash": ip_hash}
            )
        if tracing_service:
            tracing_service.end_trace(trace, output=response_text, metadata={"intent": "off_topic", **_intent_metadata(intent_result)})
            await tracing_service.aflush()

        return ChatResponse(
            response=response_text,
            intent="off_topic",
            session_id=body.session_id,
            latency_ms=latency_ms
        )

    agent_result = await agent_service.respond(
        session_id=body.session_id,
        query=body.query,
        user_name=session.get("user_name", "Guest"),
        company=session.get("company"),
        job_info=prefs.get("job_info"),
        trace=trace,
        history=prefs.get("history") or []
    )

    verification = None
    if "error" not in agent_result:
        background_tasks.add_task(remember_turn, request, session, body.query, agent_result["response"])
        verification = await _verify(request, agent_result["response"])

    tokens = (agent_result.get("tokens_in") or 0) + (agent_result.get("tokens_out") or 0)
    _count_in_memory(request, body.session_id, ip_hash, tokens)
    if supabase_service and supabase_service.enabled:
        background_tasks.add_task(
            supabase_service.log_event,
            session_id=body.session_id,
            event_type="chat",
            intent="job_related",
            tokens_in=agent_result.get("tokens_in"),
            tokens_out=agent_result.get("tokens_out"),
            latency_ms=agent_result.get("latency_ms"),
            metadata={"cost_usd": agent_result.get("cost_usd", 0.0), "model": agent_result.get("model"), "ip_hash": ip_hash,
                      **_verification_metadata(verification)}
        )

    if tracing_service:
        tracing_service.end_trace(trace, output=agent_result["response"],
                                  metadata={"intent": "job_related", **_intent_metadata(intent_result), **_verification_metadata(verification)},
                                  error=agent_result.get("error"))
        await tracing_service.aflush()
    return ChatResponse(
        response=agent_result["response"],
        intent="job_related",
        session_id=body.session_id,
        latency_ms=agent_result.get("latency_ms"),
        verification=verification
    )


@router.post("/chat/stream")
async def stream_chat(
    request: Request,
    body: ChatRequest,
    background_tasks: BackgroundTasks,
    agent_service = Depends(get_agent_service),
    intent_classifier = Depends(get_intent_classifier),
    tracing_service = Depends(get_tracing_service),
    supabase_service = Depends(get_supabase_service),
    rate_limiter = Depends(get_rate_limiter)
):
    """Stream chat response over SSE"""
    start_time = time.time()
    session = await load_session(request, body)
    ip_hash = await gate(request, body, session, rate_limiter)
    prefs = session.get("preferences") or {}
    session_id = body.session_id

    trace = None
    if tracing_service:
        trace = tracing_service.trace(
            name="stream_chat",
            session_id=session_id,
            user_id=visitor_id(ip_hash),
            input=body.query,
            tags=trace_tags("web", synthetic=telemetry.is_synthetic(), stream=True)
        )

    intent_result = await safe_timeout(
        intent_classifier.classify(body.query),
        timeout=10.0,
        default={"intent": "job_related", "confidence": 0.5, "reasoning": "Timeout fallback"},
        label="stream.intent_classification"
    )

    if intent_result["intent"] == "off_topic":
        async def off_topic_stream():
            msg = intent_result.get("decline_message", "I'm Rayhan's AI assistant. How can I help you learn about his qualifications?")
            yield f"data: {json.dumps({'chunk': msg})}\n\n"
            yield "data: [DONE]\n\n"
        _count_in_memory(request, session_id, ip_hash)
        if supabase_service and supabase_service.enabled:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(supabase_service.log_event(
                session_id=session_id, event_type="chat_stream", intent="off_topic",
                latency_ms=latency_ms, metadata={"ip_hash": ip_hash}
            ))
        if tracing_service:
            tracing_service.end_trace(trace, output=intent_result.get("decline_message"),
                                      metadata={"intent": "off_topic", **_intent_metadata(intent_result)})
            await tracing_service.aflush()
        return StreamingResponse(off_topic_stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    async def generate_stream():
        stats = {}
        first_word = True
        try:
            async for chunk in agent_service.respond_stream(
                session_id=session_id,
                query=body.query,
                user_name=session.get("user_name", "Guest"),
                company=session.get("company"),
                job_info=prefs.get("job_info"),
                trace=trace,
                history=prefs.get("history") or [],
                stats=stats
            ):
                if first_word:
                    # What the visitor waits for: request in → first word out (intent check included)
                    telemetry.record_first_word(time.time() - start_time)
                    first_word = False
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"

            # The answer is complete; now stamp it. The client shows "checking" meanwhile.
            verification = None
            if stats.get("response"):
                yield f"data: {json.dumps({'status': 'verifying'})}\n\n"
                verification = await _verify(request, stats["response"])
                if verification:
                    yield f"data: {json.dumps({'verification': verification})}\n\n"
            yield "data: [DONE]\n\n"

            latency_ms = int((time.time() - start_time) * 1000)
            if stats.get("response"):
                asyncio.create_task(remember_turn(request, session, body.query, stats["response"]))
            tokens = (stats.get("tokens_in") or 0) + (stats.get("tokens_out") or 0)
            _count_in_memory(request, session_id, ip_hash, tokens)
            if supabase_service and supabase_service.enabled:
                asyncio.create_task(supabase_service.log_event(
                    session_id=session_id, event_type="chat_stream", intent="job_related",
                    tokens_in=stats.get("tokens_in"), tokens_out=stats.get("tokens_out"), latency_ms=latency_ms,
                    metadata={"model": stats.get("model"), "ip_hash": ip_hash, **_verification_metadata(verification)}
                ))
            if tracing_service:
                tracing_service.end_trace(trace, output=stats.get("response"),
                                          metadata={"intent": "job_related", **_intent_metadata(intent_result),
                                                    **_verification_metadata(verification)})
                await tracing_service.aflush()

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            telemetry.record_answer("error", stats.get("model"))
            if tracing_service:
                tracing_service.end_trace(trace, output=stats.get("response"), error=f"{type(e).__name__}: {e}")
            yield f"data: {json.dumps({'chunk': 'Sorry, an error occurred. Please try again.'})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            # The visitor closed the tab mid-answer: still end the root, or Langfuse never gets it
            if tracing_service:
                tracing_service.end_trace(trace, output=stats.get("response"), error="client disconnected")

    return StreamingResponse(generate_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/chat/device-event")
async def device_event(
    request: Request,
    body: DeviceEventRequest,
    supabase_service = Depends(get_supabase_service),
    rate_limiter = Depends(get_rate_limiter)
):
    """
    The browser reports an answer it produced on the visitor's own machine (or a
    fallback to the cloud and why). Nothing about the question or answer is sent,
    only timing and verification counts, so the on-device share can be measured.
    """
    rate_limiter.check(rate_limiter.get_client_ip(request))
    state = request.app.state
    if not state.session_tokens.verify(body.session_token, body.session_id):
        raise HTTPException(status_code=403, detail=SESSION_INVALID)
    if supabase_service and supabase_service.enabled:
        asyncio.create_task(supabase_service.log_event(
            session_id=body.session_id, event_type="chat_device", intent="job_related",
            latency_ms=body.latency_ms,
            metadata={"outcome": body.outcome, "reason": body.reason, "quotes": body.quotes,
                      "quotes_verified": body.quotes_verified, "blocks": body.blocks, "chars": body.chars}
        ))
    return {"ok": True}
