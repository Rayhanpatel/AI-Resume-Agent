import time
import asyncio
import json
import logging
from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from tenacity import RetryError
from core.timeout import safe_timeout

logger = logging.getLogger(__name__)
from fastapi.responses import StreamingResponse
from models.schemas import ChatRequest, ChatResponse
from api.deps import get_agent_service, get_intent_classifier, get_tracing_service, get_supabase_service, get_rate_limiter

router = APIRouter()

# Constants
MAX_MESSAGE_LENGTH = 4000

# SSE headers for Cloudflare/proxy compatibility
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "X-Accel-Buffering": "no",  # Disable nginx/Cloudflare buffering
    "Connection": "keep-alive",
}

async def get_or_create_session(
    request: Request, 
    session_id: str, 
    user_name: str = "Guest", 
    company: str = None,
    job_posting: str = None
) -> dict:
    """Get session from Supabase or memory fallback (Async)"""
    state = request.app.state
    
    # Try Supabase with timeout
    if hasattr(state, "supabase_service") and state.supabase_service.enabled:
        session = await safe_timeout(
            state.supabase_service.get_session(session_id),
            timeout=3.0,
            label="supabase.get_session"
        )
        if session:
            asyncio.create_task(state.supabase_service.update_session_activity(session_id))
            return session
    
    # Fallback to memory
    if session_id in state.sessions:
        return state.sessions[session_id]
    
    # Create new session
    session_data = {"user_name": user_name, "company": company, "job_posting": job_posting, "id": session_id}
    
    if hasattr(state, "supabase_service") and state.supabase_service.enabled:
        asyncio.create_task(state.supabase_service.create_session(session_id, user_name, company))
    
    state.sessions[session_id] = session_data
    return session_data

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
    """Handle a chat message (Non-streaming) - Standardized"""
    rate_limiter.check(rate_limiter.get_client_ip(request))
    start_time = time.time()
    
    # Input validation
    if len(body.query) > MAX_MESSAGE_LENGTH:
        raise HTTPException(400, f"Message too long (max {MAX_MESSAGE_LENGTH} characters)")
    
    session = await get_or_create_session(
        request, 
        body.session_id, 
        user_name=body.user_name or "Guest", 
        company=body.company,
        job_posting=body.job_posting
    )
    
    trace = None
    if tracing_service:
        trace = tracing_service.trace(
            name="chat_message",
            session_id=body.session_id,
            metadata={"query": body.query[:100]}
        )
    
    # Intent classification with timeout fallback
    intent_result = await safe_timeout(
        intent_classifier.classify(body.query),
        timeout=15.0,
        default={"intent": "job_related", "confidence": 0.5, "reasoning": "Timeout fallback"},
        label="intent_classification"
    )
    
    if intent_result["intent"] == "off_topic":
        response_text = intent_result.get("decline_message") or "I'm just Rayhan's AI assistant..."
        latency_ms = int((time.time() - start_time) * 1000)
        
        if supabase_service and supabase_service.enabled:
            background_tasks.add_task(
                supabase_service.log_event,
                session_id=body.session_id,
                event_type="chat",
                intent="off_topic",
                latency_ms=latency_ms
            )
        
        return ChatResponse(
            response=response_text,
            intent="off_topic",
            session_id=body.session_id,
            latency_ms=latency_ms
        )
    
    try:
        agent_result = await agent_service.respond(
            session_id=body.session_id,
            query=body.query,
            user_name=session.get("user_name", "Guest"),
            company=session.get("company"),
            job_posting=session.get("job_posting") or body.job_posting,
            trace=trace
        )
    except RetryError:
        logger.warning("All retries exhausted for agent_service.respond")
        agent_result = {
            "response": "I apologize, but I'm having trouble responding right now. Please try again or contact Rayhan directly at rayhanbp@umd.edu.",
            "latency_ms": int((time.time() - start_time) * 1000)
        }
    
    if tracing_service: tracing_service.log_generation(trace=trace, name="response", model="gemini-2.0-flash", input_text=body.query, output_text=agent_result["response"])
    
    if supabase_service and supabase_service.enabled:
        background_tasks.add_task(
            supabase_service.log_event,
            session_id=body.session_id,
            event_type="chat",
            intent="job_related",
            tokens_in=agent_result.get("tokens_in"),
            tokens_out=agent_result.get("tokens_out"),
            latency_ms=agent_result.get("latency_ms"),
            metadata={"cost_usd": agent_result.get("cost_usd", 0.0)}
        )
    
    if tracing_service: tracing_service.flush()
    return ChatResponse(
        response=agent_result["response"],
        intent="job_related",
        session_id=body.session_id,
        latency_ms=agent_result.get("latency_ms")
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
    """Stream chat response - FULL FEATURES for Railway deployment"""
    start_time = time.time()
    
    # 1. Rate Limiting
    rate_limiter.check(rate_limiter.get_client_ip(request))
    
    # 2. Input validation
    if len(body.query) > MAX_MESSAGE_LENGTH:
        raise HTTPException(400, f"Message too long (max {MAX_MESSAGE_LENGTH} characters)")
    
    session_id = body.session_id
    
    # 3. Session management
    try:
        session = await get_or_create_session(
            request, session_id, 
            user_name=body.user_name or "Guest", 
            company=body.company,
            job_posting=body.job_posting
        )
    except Exception as e:
        logger.warning(f"Session creation failed: {e}")
        session = {"id": session_id, "user_name": body.user_name or "Guest", "company": body.company, "job_posting": body.job_posting}
    
    # 4. Tracing
    trace = None
    if tracing_service:
        trace = tracing_service.trace(name="stream_chat", session_id=session_id)
    
    # 5. Intent classification
    intent_result = await safe_timeout(
        intent_classifier.classify(body.query),
        timeout=10.0,
        default={"intent": "job_related", "confidence": 0.5, "reasoning": "Timeout fallback"},
        label="stream.intent_classification"
    )
    
    # 6. Handle off-topic
    if intent_result["intent"] == "off_topic":
        async def off_topic_stream():
            msg = intent_result.get("decline_message", "I'm Rayhan's AI assistant. How can I help you learn about his qualifications?")
            yield f"data: {json.dumps({'chunk': msg})}\n\n"
            yield "data: [DONE]\n\n"
        # Log off-topic event (matches non-streaming endpoint behavior)
        if supabase_service and supabase_service.enabled:
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(supabase_service.log_event(
                session_id=session_id,
                event_type="chat_stream",
                intent="off_topic",
                latency_ms=latency_ms
            ))
        if tracing_service: tracing_service.flush()
        return StreamingResponse(off_topic_stream(), media_type="text/event-stream", headers=SSE_HEADERS)
    
    # 7. Stream response using agent service
    async def generate_stream():
        chunks = []
        try:
            async for chunk in agent_service.respond_stream(
                session_id=session_id,
                query=body.query,
                user_name=session.get("user_name", "Guest"),
                company=session.get("company"),
                job_posting=session.get("job_posting") or body.job_posting,
                trace=trace
            ):
                chunks.append(chunk)
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
            yield "data: [DONE]\n\n"
            
            # 8. Background logging
            latency_ms = int((time.time() - start_time) * 1000)
            
            if supabase_service and supabase_service.enabled:
                asyncio.create_task(supabase_service.log_event(
                    session_id=session_id,
                    event_type="chat_stream",
                    intent="job_related",
                    latency_ms=latency_ms
                ))
            
            if tracing_service: tracing_service.flush()
            
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"data: {json.dumps({'chunk': 'Sorry, an error occurred. Please try again.'})}\n\n"
            yield "data: [DONE]\n\n"
    
    return StreamingResponse(generate_stream(), media_type="text/event-stream", headers=SSE_HEADERS)
