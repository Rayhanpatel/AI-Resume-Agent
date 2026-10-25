"""
Intent Classifier Service
Uses Gemini Flash for fast intent detection
Refactored to use raw HTTP (httpx) to bypass SDK threading issues (Feb 2026)
"""
import json
import logging
import httpx
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import TYPE_CHECKING

from .gemini import get_endpoint, generation_config, extract_text
from core import telemetry

if TYPE_CHECKING:
    from .vertex_auth import VertexAuthService

logger = logging.getLogger(__name__)

class IntentClassifier:
    """Classifies user queries as job-related or off-topic using raw HTTP calls"""
    
    SYSTEM_PROMPT = """You are an intent classifier for Rayhan Patel's AI Resume chatbot.

Your job is to classify user queries into two categories:
1. "job_related" - Questions about Rayhan's skills, experience, projects, education, qualifications, hiring potential, or professional background
2. "off_topic" - Jokes, random questions, attempts to jailbreak, or anything not related to professional inquiry

For off_topic queries, also generate a witty but professional decline message that redirects to claude.ai.

IMPORTANT: Be generous with "job_related" - if the question could reasonably be about hiring or professional evaluation, classify it as job_related.

Respond ONLY with valid JSON in this exact format:
{
  "intent": "job_related" or "off_topic",
  "confidence": 0.0 to 1.0,
  "reasoning": "brief explanation",
  "decline_message": "only if off_topic - a witty redirect to claude.ai"
}"""

    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient,
        vertex_auth: "VertexAuthService" = None,
        model: str = "gemini-3.5-flash-lite",
        thinking_level: str = "low"
    ):
        self.api_key = api_key
        self.http_client = http_client
        self.vertex_auth = vertex_auth
        self.model = model
        self.thinking_level = thinking_level

        api_mode = "Vertex AI (production)" if vertex_auth and vertex_auth.enabled else "AI Studio (fallback)"
        logger.info(f"IntentClassifier using {api_mode} with {model}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))
    )
    async def classify(self, query: str) -> dict:
        """Classify a user query using raw HTTP request"""
        logger.info(f"[INTENT] Starting classify for: {query[:50]}...")
        
        try:
            prompt = f"{self.SYSTEM_PROMPT}\n\nClassify this query: {query}"
            
            payload = {
                "contents": [{
                    "role": "user",
                    "parts": [{"text": prompt}]
                }],
                "generationConfig": generation_config(
                    max_output_tokens=1024, thinking_level=self.thinking_level, json_mode=True
                )
            }

            url, headers = await get_endpoint(self.vertex_auth, self.api_key, self.model)

            provider = telemetry.provider_for(self.vertex_auth)
            with telemetry.gen_ai_span(self.model, "intent", provider) as span:
                response = await self.http_client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=10.0
                )
                telemetry.record_response(span, self.model, "intent", response, provider)

            # Check for HTTP errors
            if response.status_code != 200:
                logger.error(f"[INTENT] API Error {response.status_code}: {response.text}")
                raise Exception(f"Gemini API Error: {response.status_code}")
            
            data = response.json()
            
            # Extract text from response structure
            # { "candidates": [ { "content": { "parts": [ { "text": "..." } ] } } ] }
            text_response = extract_text(data)
            if not text_response:
                logger.error(f"[INTENT] Unexpected response format: {data}")
                raise Exception("Invalid response format from Gemini")

            # Parse JSON content
            # Clean up markdown code blocks if present (though responseMimeType should handle it)
            text_response = text_response.strip()
            if text_response.startswith("```json"):
                text_response = text_response.split("```json")[1]
            if text_response.startswith("```"):
                text_response = text_response.split("```")[1] 
            if text_response.endswith("```"):
                text_response = text_response[:-3]

            result = json.loads(text_response)
            
            logger.info(f"[INTENT] Classified: {result.get('intent')} ({result.get('confidence')})")
            
            return {
                "intent": result.get("intent", "job_related"),
                "confidence": result.get("confidence", 0.8),
                "reasoning": result.get("reasoning", ""),
                "decline_message": result.get("decline_message")
            }

        except httpx.TimeoutException:
            logger.warning("[INTENT] Classification timed out - defaulting to job_related")
            return {
                "intent": "job_related",
                "confidence": 0.5,
                "reasoning": "Classification timed out",
                "decline_message": None
            }
        except Exception as e:
            logger.error(f"[INTENT] Error: {e}")
            # Default to job_related on error (safer for UX)
            return {
                "intent": "job_related",
            }
