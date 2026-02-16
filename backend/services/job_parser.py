"""
Job Parser - Extracts structured data from job descriptions using LLM
"""
import json
import logging
import httpx
from typing import Optional
from dataclasses import dataclass, field

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
    job_text: str
) -> JobInfo:
    """
    Parse job description text into structured JobInfo using LLM.
    
    Args:
        http_client: Async HTTP client
        vertex_auth: Vertex AI auth service (or None for AI Studio)
        api_key: Google API key (fallback)
        job_text: Raw job description text
        
    Returns:
        JobInfo dataclass with extracted fields
    """
    if not job_text or len(job_text) < 50:
        return JobInfo()
    
    try:
        # Build request based on API mode
        if vertex_auth and vertex_auth.enabled:
            url = vertex_auth.get_generate_url("gemini-2.0-flash")
            headers = await vertex_auth.get_headers()
        else:
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
            headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        
        payload = {
            "contents": [{
                "role": "user", 
                "parts": [{"text": PARSE_PROMPT.format(job_text=job_text[:3000])}]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 400,
                "responseMimeType": "application/json"
            }
        }
        
        response = await http_client.post(url, json=payload, headers=headers, timeout=8.0)
        
        if response.status_code != 200:
            logger.warning(f"Job parsing API error: {response.status_code}")
            return JobInfo()
        
        data = response.json()
        
        # Handle response - with responseMimeType: application/json, 
        # Gemini returns JSON in text field as string
        part = data["candidates"][0]["content"]["parts"][0]
        
        if isinstance(part, dict) and "text" in part:
            text = part["text"]
            
            if isinstance(text, dict):
                parsed = text
            elif isinstance(text, str):
                # Clean and parse JSON from string
                text = text.strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                text = text.strip()
                parsed = json.loads(text)
            else:
                return JobInfo()
        elif isinstance(part, dict):
            parsed = part
        else:
            return JobInfo()
        
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
