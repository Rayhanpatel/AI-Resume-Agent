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

    def __init__(self, api_key: str, http_client: httpx.AsyncClient, vertex_auth: "VertexAuthService" = None):
        self.api_key = api_key
        self.http_client = http_client
        self.vertex_auth = vertex_auth
        self.model = "gemini-2.0-flash"
        
        # Determine which API to use
        if vertex_auth and vertex_auth.enabled:
            self.use_vertex = True
            logger.info("IntentClassifier using Vertex AI (production)")
        else:
            self.use_vertex = False
            self.headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
            self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            logger.info("IntentClassifier using AI Studio (fallback)")

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
                "generationConfig": {
                    "temperature": 0.1,
                    "topP": 0.95,
                    "maxOutputTokens": 256,
                    "responseMimeType": "application/json"
                }
            }
            
            # Get URL and headers based on API mode
            if self.use_vertex:
                url = self.vertex_auth.get_generate_url(self.model)
                headers = await self.vertex_auth.get_headers()
            else:
                url = self.api_url
                headers = self.headers
            
            response = await self.http_client.post(
                url,
                json=payload,
                headers=headers,
                timeout=10.0
            )

            # Check for HTTP errors
            if response.status_code != 200:
                logger.error(f"[INTENT] API Error {response.status_code}: {response.text}")
                raise Exception(f"Gemini API Error: {response.status_code}")
            
            data = response.json()
            
            # Extract text from response structure
            # { "candidates": [ { "content": { "parts": [ { "text": "..." } ] } } ] }
            try:
                text_response = data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
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
