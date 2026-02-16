import time
import uuid
import asyncio
import logging
from fastapi import APIRouter, Request, Depends, HTTPException
from models.schemas import SessionRequest, SessionResponse
from api.deps import get_tracing_service, get_supabase_service, get_rate_limiter
from services.leads import submit_recruiter_lead
from services.job_extractor import extract_from_url, is_url
from services.job_parser import parse_job_description
from services.prompt_generator import generate_prompts, DEFAULT_PROMPTS
from core.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/session", response_model=SessionResponse)
async def create_session(
    request: Request,
    body: SessionRequest,
    tracing_service = Depends(get_tracing_service),
    supabase_service = Depends(get_supabase_service),
    rate_limiter = Depends(get_rate_limiter)
):
    """Create a new chat session with optional job context extraction"""
    rate_limiter.check(rate_limiter.get_client_ip(request))
    
    settings = get_settings()
    http_client = request.app.state.http_client
    
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
                    "remoteip": rate_limiter.get_client_ip(request),
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
    vertex_auth = request.app.state.vertex_auth
    
    # --- Job Description Handling ---
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
    
    # --- Parse Job Info (async LLM call) ---
    job_info = None
    prompts = DEFAULT_PROMPTS
    
    if job_text:
        try:
            parsed = await parse_job_description(
                http_client, vertex_auth, settings.google_api_key, job_text
            )
            job_info = parsed.to_dict() if parsed.company_name or parsed.role_title else None
            
            # Generate dynamic prompts
            prompts = await generate_prompts(
                http_client, vertex_auth, settings.google_api_key,
                parsed.company_name or body.company or "",
                parsed.role_title,
                parsed.key_skills or []
            )
        except Exception as e:
            logger.warning(f"Job parsing/prompt generation failed: {e}")
    
    # --- Determine Company Name ---
    company_name = None
    if job_info and job_info.get("company_name"):
        company_name = job_info["company_name"]
    elif body.company:
        company_name = body.company
    
    # --- Store Session ---
    request.app.state.sessions[session_id] = {
        "user_name": body.user_name,
        "company": company_name,
        "job_posting": job_text,
        "job_info": job_info,
        "created_at": time.time()
    }
    
    if supabase_service and supabase_service.enabled:
        asyncio.create_task(supabase_service.create_session(
            session_id, body.user_name, company_name
        ))
    
    # --- Lead Capture (with full job intel) ---
    # Capture lead if we have extracted text OR a URL (even if extraction failed)
    lead_job_text = job_text or body.job_url or body.job_posting
    if lead_job_text and hasattr(request.app.state, "http_client"):
        asyncio.create_task(submit_recruiter_lead(
            http_client,
            name=body.user_name,
            company=company_name or "",
            job_posting=lead_job_text,
            session_id=session_id,
            job_url=job_url or body.job_url,
            job_info=job_info
        ))
    
    # --- Tracing ---
    if tracing_service:
        tracing_service.trace(
            name="session_start",
            session_id=session_id,
            metadata={
                "user_name": body.user_name,
                "company": company_name,
                "has_job_posting": bool(job_text),
                "from_url": bool(job_url),
                "extraction_error": extraction_error
            }
        )
    
    # --- Build Welcome Message ---
    if job_info and job_info.get("company_name") and job_info.get("role_title"):
        welcome = f"Hi {body.user_name}! 👋 I see you're exploring the **{job_info['role_title']}** role at **{job_info['company_name']}**. I'll tailor my responses to show how Rayhan's experience aligns with this position. What would you like to know?"
    elif job_text:
        company_text = f" from {company_name}" if company_name else ""
        welcome = f"Hi {body.user_name}{company_text}! 👋 I've reviewed the job description and will highlight relevant experience. What would you like to know?"
    else:
        company_text = f" from {body.company}" if body.company else ""
        welcome = f"Hi {body.user_name}{company_text}! 👋 I'm Rayhan's AI assistant. Ask me anything about his skills, experience, or projects!"
    
    return SessionResponse(
        session_id=session_id,
        welcome_message=welcome,
        suggested_prompts=prompts,
        job_info=job_info,
        extraction_error=extraction_error
    )
