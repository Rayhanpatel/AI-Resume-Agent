"""
Dynamic Prompt Generator - Creates role-specific starter questions using LLM
"""
import json
import logging
import httpx
from typing import List

logger = logging.getLogger(__name__)

DEFAULT_PROMPTS = [
    "Why should we hire Rayhan?",
    "What's his ML experience?",
    "Tell me about his projects",
    "What makes him unique?"
]

GENERATE_PROMPT = """Generate 4 short questions (max 40 chars each) a recruiter would ask to evaluate if a candidate matches this job. Focus on relevant skills and experience.

Job: {role} at {company}
Key Skills: {skills}

Return ONLY a JSON array of exactly 4 strings, no explanation:
["question1", "question2", "question3", "question4"]"""


async def generate_prompts(
    http_client: httpx.AsyncClient,
    vertex_auth,
    api_key: str,
    company: str,
    role: str,
    skills: List[str]
) -> List[str]:
    """
    Generate 4 role-specific starter prompts using LLM.
    
    Args:
        http_client: Async HTTP client
        vertex_auth: Vertex AI auth service (or None)
        api_key: Google API key (fallback)
        company: Company name
        role: Job role/title
        skills: List of required skills
        
    Returns:
        List of 4 prompt strings
    """
    if not role and not company:
        return DEFAULT_PROMPTS
    
    try:
        # Build request
        if vertex_auth and vertex_auth.enabled:
            url = vertex_auth.get_generate_url("gemini-2.0-flash")
            headers = await vertex_auth.get_headers()
        else:
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
            headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        
        skills_str = ", ".join(skills[:5]) if skills else "various technical skills"
        
        payload = {
            "contents": [{
                "role": "user", 
                "parts": [{"text": GENERATE_PROMPT.format(
                    role=role or "the role",
                    company=company or "the company",
                    skills=skills_str
                )}]
            }],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 200,
                "responseMimeType": "application/json"
            }
        }
        
        response = await http_client.post(url, json=payload, headers=headers, timeout=5.0)
        
        if response.status_code != 200:
            logger.warning(f"Prompt generation API error: {response.status_code} - {response.text[:200]}")
            return DEFAULT_PROMPTS
        
        data = response.json()
        
        # Handle response - with responseMimeType: application/json,
        # Gemini may return JSON object directly, not as string in "text"
        part = data["candidates"][0]["content"]["parts"][0]
        
        if "text" in part:
            # Standard response: JSON is in text field as string
            text = part["text"]
            if isinstance(text, list):
                prompts = text
            else:
                # Clean and parse JSON from string
                text = text.strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                text = text.strip()
                prompts = json.loads(text)
        else:
            # responseMimeType: application/json returns object directly
            prompts = list(part.values()) if isinstance(part, dict) else part
        
        # Validate
        if isinstance(prompts, list) and len(prompts) >= 4:
            validated = []
            for p in prompts[:4]:
                if isinstance(p, str) and 5 < len(p) < 60:
                    validated.append(p)
            
            if len(validated) == 4:
                return validated
        
        return DEFAULT_PROMPTS
        
    except json.JSONDecodeError:
        logger.warning("Prompt generation JSON parse error")
        return DEFAULT_PROMPTS
    except Exception as e:
        logger.warning(f"Prompt generation error: {e}")
        return DEFAULT_PROMPTS
