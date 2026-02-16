"""
Recruiter Lead Capture Service
Submits lead data to Google Sheets via webhook with full job intelligence
"""
import time
import httpx
import logging
from typing import Optional, Dict, Any
from core.config import get_settings

logger = logging.getLogger(__name__)


async def submit_recruiter_lead(
    http_client: httpx.AsyncClient,
    name: str,
    company: str,
    job_posting: str,
    session_id: str,
    job_url: Optional[str] = None,
    job_info: Optional[Dict[str, Any]] = None
):
    """
    Submit recruiter lead to Google Sheets via webhook.
    
    Args:
        http_client: Async HTTP client
        name: Recruiter/user name
        company: Company name
        job_posting: Job description text
        session_id: Chat session ID
        job_url: Original job posting URL (if extracted from URL)
        job_info: Structured job data (company, role, skills, etc.)
    """
    settings = get_settings()
    if not settings.google_sheet_webhook:
        return

    try:
        payload = {
            "name": name,
            "company": company,
            "jobPosting": job_posting[:5000] if job_posting else "",  # Limit size
            "sessionId": session_id,
            "jobUrl": job_url,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        
        # Add structured job info if available
        if job_info:
            payload["roleTitle"] = job_info.get("role_title", "")
            payload["keySkills"] = ", ".join(job_info.get("key_skills", []))
            payload["location"] = job_info.get("location", "")
            payload["seniority"] = job_info.get("seniority", "")
            payload["team"] = job_info.get("team", "")
        
        await http_client.post(
            settings.google_sheet_webhook, 
            json=payload, 
            timeout=10.0,
            follow_redirects=True
        )
        logger.info(f"Lead submitted for {name} at {company}")
    except Exception as e:
        logger.warning(f"Failed to submit lead: {e}")
