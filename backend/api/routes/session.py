import time
import uuid
import asyncio
import logging
from fastapi import APIRouter, Request, Depends, HTTPException
from models.schemas import SessionRequest, SessionResponse
from api.deps import get_supabase_service, get_rate_limiter
from services.leads import submit_recruiter_lead
from services.job_extractor import extract_from_url, is_url
from services.job_parser import parse_job_description
from services.prompt_generator import generate_prompts, DEFAULT_PROMPTS
from services.quotas import QuotaExceeded, hash_ip
from core.config import get_settings
from core import telemetry

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/session", response_model=SessionResponse)
async def create_session(
    request: Request,
    body: SessionRequest,
    supabase_service = Depends(get_supabase_service),
    rate_limiter = Depends(get_rate_limiter)
):
    """Create a new chat session with optional job context extraction"""
    client_ip = rate_limiter.get_client_ip(request)
    rate_limiter.check(client_ip)

    settings = get_settings()
    state = request.app.state
    http_client = state.http_client
    ip_hash = hash_ip(client_ip, state.quotas.salt)

    try:
        await state.quotas.check_new_session(ip_hash)
    except QuotaExceeded as e:
        raise HTTPException(status_code=429, detail={"error": e.message, "scope": e.scope})

    # --- Cloudflare Turnstile Verification ---
    if settings.turnstile_secret_key:
        if not body.turnstile_token:
            raise HTTPException(status_code=403, detail="CAPTCHA verification required")

        # Check for Test Token (always starts with 1x000...)
        # CRITICAL: Only allow this in non-production environments to prevent bypass
        secret_key = settings.turnstile_secret_key
        if body.turnstile_token.startswith("1x000") and settings.environment == "development":
            logger.info("Using Turnstile Test Secret for Development Token")
            secret_key = "1x0000000000000000000000000000000AA"

        try:
            verify_resp = await http_client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": secret_key,
                    "response": body.turnstile_token,
                    "remoteip": client_ip,
                },
                timeout=5.0,
            )
            result = verify_resp.json()
            if not result.get("success"):
                logger.warning(f"Turnstile failed: {result.get('error-codes', [])}")
                raise HTTPException(status_code=403, detail="CAPTCHA verification failed")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Turnstile verification error: {e}")
            # Fail open — don't block users if Cloudflare is unreachable

    session_id = str(uuid.uuid4())
    vertex_auth = state.vertex_auth

    # --- Job Description Handling ---
    # The raw text is read once by the extraction call below and then discarded.
    # Only the extracted schema is kept, so the persona model never sees the page.
    job_text = None
    job_url = None
    extraction_error = None

    # Priority: URL > pasted text
    if body.job_url:
        job_text, extraction_error = await extract_from_url(http_client, body.job_url)
        if job_text:
            job_url = body.job_url
        elif body.job_posting:
            # Fallback to pasted text if URL fails
            job_text = body.job_posting
            extraction_error = None
    elif body.job_posting:
        # Auto-detect if user pasted URL in text field
        if is_url(body.job_posting):
            job_text, extraction_error = await extract_from_url(http_client, body.job_posting.strip())
            if job_text:
                job_url = body.job_posting.strip()
            elif not extraction_error:
                extraction_error = "Could not extract from URL"
        else:
            job_text = body.job_posting

    # --- Parse Job Info (async LLM call, no tools) ---
    job_info = None
    prompts = DEFAULT_PROMPTS

    if job_text:
        try:
            parsed = await parse_job_description(
                http_client, vertex_auth, settings.google_api_key, job_text,
                model=settings.gemini_fast_model
            )
            job_info = parsed.to_dict() if parsed.company_name or parsed.role_title else None

            # Generate dynamic prompts
            prompts = await generate_prompts(
                http_client, vertex_auth, settings.google_api_key,
                parsed.company_name or body.company or "",
                parsed.role_title,
                parsed.key_skills or [],
                model=settings.gemini_fast_model
            )
        except Exception as e:
            logger.warning(f"Job parsing/prompt generation failed: {e}")
        if job_info is None and job_text:
            extraction_error = extraction_error or "Couldn't read a job post in that text, so the chat won't be tailored to it."

    # --- Determine Company Name ---
    company_name = None
    if job_info and job_info.get("company_name"):
        company_name = job_info["company_name"]
    elif body.company:
        company_name = body.company

    # --- Store Session (server-side; history accumulates here) ---
    session_record = {
        "id": session_id,
        "user_name": body.user_name,
        "company": company_name,
        "preferences": {"job_info": job_info, "history": [], "ip_hash": ip_hash},
        "created_at": time.time()
    }
    state.sessions[session_id] = session_record

    state.quotas.record_memory("sessions_ip", ip_hash)  # the fallback count, kept even when Supabase is configured
    if supabase_service and supabase_service.enabled:
        created = await supabase_service.create_session(session_id, body.user_name, company_name)
        if created is not None:
            await supabase_service.update_preferences(session_id, session_record["preferences"])
        asyncio.create_task(supabase_service.log_event(
            session_id=session_id, event_type="session_start", metadata={"ip_hash": ip_hash, "has_job": bool(job_info)}
        ))

    # --- Lead Capture (structured fields and the URL; not the raw page). Probe traffic is not a lead. ---
    if (job_info or job_url) and hasattr(state, "http_client") and not telemetry.is_synthetic():
        asyncio.create_task(submit_recruiter_lead(
            http_client,
            name=body.user_name,
            company=company_name or "",
            job_posting=(job_info or {}).get("summary", ""),
            session_id=session_id,
            job_url=job_url or body.job_url,
            job_info=job_info
        ))

    # Langfuse traces start with the first message (chat routes). A session start is not a
    # trace: the v3 wrapper's session_start trace never reached Langfuse, and a new trace type
    # here would be scored by every unfiltered trace-level evaluator.

    # --- Build Welcome Message (the AI disclosure is here, at the first interaction) ---
    disclosure = "You're chatting with an AI that Rayhan built; it answers only from his public profile."
    if job_info and job_info.get("company_name") and job_info.get("role_title"):
        welcome = f"Hi {body.user_name}! {disclosure} I see you're looking at the **{job_info['role_title']}** role at **{job_info['company_name']}**, so I'll map his experience to it, gaps included. What would you like to know?"
    elif job_info:
        welcome = f"Hi {body.user_name}! {disclosure} I've read the job post you shared and will point at the relevant experience. What would you like to know?"
    else:
        company_text = f" from {body.company}" if body.company else ""
        welcome = f"Hi {body.user_name}{company_text}! {disclosure} Ask about his work, projects or research, or paste a job post and I'll map his experience to it."

    return SessionResponse(
        session_id=session_id,
        session_token=state.session_tokens.issue(session_id),
        welcome_message=welcome,
        suggested_prompts=prompts,
        job_info=job_info,
        extraction_error=extraction_error
    )
