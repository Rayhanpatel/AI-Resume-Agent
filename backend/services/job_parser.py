"""
Job Parser - Extracts structured data from job descriptions using LLM
"""
import json
import logging
import httpx
from typing import Optional
from dataclasses import dataclass, field

from .gemini import get_endpoint, generation_config, extract_text

logger = logging.getLogger(__name__)


@dataclass
class JobInfo:
    """Structured job information extracted from description"""
    company_name: str = ""
    role_title: str = ""
    key_skills: list = field(default_factory=list)
    team: str = ""
    location: str = ""
    seniority: str = ""
    summary: str = ""
    
    def to_dict(self) -> dict:
        return {
            "company_name": self.company_name,
            "role_title": self.role_title,
            "key_skills": self.key_skills,
            "team": self.team,
            "location": self.location,
            "seniority": self.seniority,
            "summary": self.summary,
        }


PARSE_PROMPT = """Extract job details from this text. Return ONLY valid JSON, no explanation:
{{"company_name":"...","role_title":"...","key_skills":["skill1","skill2"],"team":"...","location":"...","seniority":"entry|mid|senior|staff","summary":"1 sentence description"}}

If a field is not found, use empty string "" or empty array [].

Text:
{job_text}"""


async def parse_job_description(
    http_client: httpx.AsyncClient,
    vertex_auth,
    api_key: str,
    job_text: str,
    model: str = "gemini-3.5-flash-lite"
) -> JobInfo:
    """
    Parse job description text into structured JobInfo using LLM.

    Args:
        http_client: Async HTTP client
        vertex_auth: Vertex AI auth service (or None for AI Studio)
        api_key: Google API key (fallback)
        job_text: Raw job description text
        model: Gemini model ID (the fast model from settings)

    Returns:
        JobInfo dataclass with extracted fields
    """
    if not job_text or len(job_text) < 50:
        return JobInfo()

    try:
        url, headers = await get_endpoint(vertex_auth, api_key, model)

        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text": PARSE_PROMPT.format(job_text=job_text[:3000])}]
            }],
            "generationConfig": generation_config(max_output_tokens=1024, json_mode=True)
        }

        response = await http_client.post(url, json=payload, headers=headers, timeout=10.0)
        
        if response.status_code != 200:
            logger.warning(f"Job parsing API error: {response.status_code}")
            return JobInfo()
        
        data = response.json()

        # With responseMimeType: application/json, Gemini returns JSON as a string in the text parts
        text = extract_text(data).strip()
        if not text:
            return JobInfo()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text.strip())
        
        # Handle list response (Gemini sometimes returns [{}])
        if isinstance(parsed, list) and len(parsed) > 0:
            parsed = parsed[0]

        if not isinstance(parsed, dict):
            return JobInfo()
        
        return JobInfo(
            company_name=str(parsed.get("company_name", "")).strip(),
            role_title=str(parsed.get("role_title", "")).strip(),
            key_skills=parsed.get("key_skills", []) if isinstance(parsed.get("key_skills"), list) else [],
            team=str(parsed.get("team", "")).strip(),
            location=str(parsed.get("location", "")).strip(),
            seniority=str(parsed.get("seniority", "")).strip(),
            summary=str(parsed.get("summary", "")).strip(),
        )
        
    except json.JSONDecodeError as e:
        logger.warning(f"Job parsing JSON error: {e}")
        return JobInfo()
    except Exception as e:
        logger.warning(f"Job parsing error: {e}")
        return JobInfo()
