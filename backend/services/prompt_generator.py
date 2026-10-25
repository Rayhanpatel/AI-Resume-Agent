"""
Dynamic Prompt Generator - Creates role-specific starter questions using LLM
"""
import json
import logging
import httpx
from typing import List

from .gemini import get_endpoint, generation_config, extract_text
from core import telemetry

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
    skills: List[str],
    model: str = "gemini-3.5-flash-lite"
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
        model: Gemini model ID (the fast model from settings)

    Returns:
        List of 4 prompt strings
    """
    if not role and not company:
        return DEFAULT_PROMPTS

    try:
        url, headers = await get_endpoint(vertex_auth, api_key, model)

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
            "generationConfig": generation_config(max_output_tokens=1024, json_mode=True)
        }

        provider = telemetry.provider_for(vertex_auth)
        with telemetry.gen_ai_span(model, "prompts", provider) as span:
            response = await http_client.post(url, json=payload, headers=headers, timeout=8.0)
            telemetry.record_response(span, model, "prompts", response, provider)

        if response.status_code != 200:
            logger.warning(f"Prompt generation API error: {response.status_code} - {response.text[:200]}")
            return DEFAULT_PROMPTS

        data = response.json()

        # With responseMimeType: application/json, the JSON array is a string in the text parts
        text = extract_text(data).strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        prompts = json.loads(text.strip())
        
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
