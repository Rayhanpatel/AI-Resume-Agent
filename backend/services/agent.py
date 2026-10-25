"""
Main Agent Service
Uses Gemini Flash for responses about Rayhan
Refactored to use raw HTTP (httpx) to bypass SDK threading issues (Feb 2026)
Moved to Gemini 3.x with a real systemInstruction (Oct 2026)
"""
import json
import logging
import httpx
import time
import asyncio
from typing import List, Optional, AsyncGenerator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .memory import MemoryService
from .tracer import TracingService
from .vertex_auth import VertexAuthService
from .gemini import get_endpoint, generation_config, extract_text
from core.prompts import get_system_prompt
from core.timeout import safe_timeout

logger = logging.getLogger(__name__)


class AgentService:
    """Main conversational agent for resume Q&A using raw HTTP calls

    Supports both Vertex AI (production) and AI Studio (fallback)
    """

    def __init__(
        self,
        api_key: str,
        memory: MemoryService,
        tracer: TracingService,
        http_client: httpx.AsyncClient,
        vertex_auth: VertexAuthService = None,
        model: str = "gemini-3.8-flash",
        thinking_level: str = "low"
    ):
        self.api_key = api_key
        self.http_client = http_client
        self.memory = memory
        self.tracer = tracer
        self.vertex_auth = vertex_auth
        self.model = model

        api_mode = "Vertex AI (production)" if vertex_auth and vertex_auth.enabled else "AI Studio (fallback)"
        logger.info(f"AgentService using {api_mode} with {model}")

        # Thinking tokens count against maxOutputTokens, so leave room for both
        self.generation_config = generation_config(max_output_tokens=2048, thinking_level=thinking_level)

    async def _store_memory(self, session_id: str, query: str, response: str):
        """Store messages in memory (non-blocking helper)"""
        await safe_timeout(self.memory.add_message(session_id, "user", query), timeout=3.0, label="mem0.store_user")
        await safe_timeout(self.memory.add_message(session_id, "assistant", response), timeout=3.0, label="mem0.store_assistant")

    async def _build_payload(self, session_id, query, user_name, company, job_posting) -> dict:
        """Build the request: system prompt + memory as systemInstruction, the query as the only user turn.
        Gemini 3.x rejects prefilled model turns, so nothing is put in the model's mouth."""
        # Get semantically relevant context from Mem0 (with timeout)
        history_context = await safe_timeout(
            self.memory.get_semantic_context(session_id, query, limit=5),
            timeout=5.0,
            default="",
            label="mem0.get_context"
        )

        system_prompt = get_system_prompt(
            user_name,
            company or "their company",
            job_posting
        )
        if history_context:
            system_prompt += f"\n\nEARLIER IN THIS CONVERSATION (for continuity; do not repeat yourself):\n{history_context}"

        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": query}]}],
            "generationConfig": self.generation_config,
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))
    )
    async def respond(
        self,
        session_id: str,
        query: str,
        user_name: str,
        company: Optional[str] = None,
        job_posting: Optional[str] = None,
        trace = None
    ) -> dict:
        """Generate a response using raw HTTP request"""
        start_time = time.time()

        payload = await self._build_payload(session_id, query, user_name, company, job_posting)

        try:
            url, headers = await get_endpoint(self.vertex_auth, self.api_key, self.model)

            response = await self.http_client.post(
                url,
                json=payload,
                headers=headers,
                timeout=30.0  # Strict 30s timeout
            )
            
            if response.status_code != 200:
                logger.error(f"[AGENT] API Error {response.status_code}: {response.text}")
                raise Exception(f"Gemini API Error: {response.status_code}")
            
            data = response.json()
            
            # Safe extraction - Gemini may return no candidates (safety filter, quota)
            candidates = data.get("candidates", [])
            if not candidates or "content" not in candidates[0]:
                finish_reason = candidates[0].get("finishReason", "UNKNOWN") if candidates else "NO_CANDIDATES"
                logger.warning(f"[AGENT] No content in response (finishReason={finish_reason})")
                raise Exception(f"Gemini returned no content (reason: {finish_reason})")

            response_text = extract_text(data)
            if not response_text:
                raise Exception("Gemini response missing text content")
            
            # Metrics
            latency_ms = int((time.time() - start_time) * 1000)
            usage = data.get("usageMetadata", {})
            tokens_in = usage.get("promptTokenCount", 0)
            tokens_out = usage.get("candidatesTokenCount", 0)

            # Store in memory (fire and forget - don't block response)
            asyncio.create_task(self._store_memory(session_id, query, response_text))
            
            # Trace
            cost_usd = 0.0
            if trace:
                trace_result = self.tracer.log_generation(
                    trace=trace,
                    name="agent_response",
                    model=self.model,
                    input_text=query,
                    output_text=response_text,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                    metadata={"session_id": session_id, "user_name": user_name}
                )
                cost_usd = trace_result.get("cost_usd", 0.0) if trace_result else 0.0
            
            return {
                "response": response_text,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd
            }

        except (httpx.HTTPError, httpx.TimeoutException):
            raise  # Let tenacity retry these
        except Exception as e:
            logger.warning(f"Agent error (non-retryable): {e}")
            return {
                "response": "I apologize, but I'm having trouble responding right now. Please try again or contact Rayhan directly at rayhanbp@umd.edu.",
                "error": str(e),
                "latency_ms": int((time.time() - start_time) * 1000)
            }

    async def respond_stream(
        self,
        session_id: str,
        query: str,
        user_name: str,
        company: Optional[str] = None,
        job_posting: Optional[str] = None,
        trace = None
    ) -> AsyncGenerator[str, None]:
        """Stream response using raw HTTP with SSE (no retry - incompatible with generators)"""
        start_time = time.time()

        payload = await self._build_payload(session_id, query, user_name, company, job_posting)
        url, headers = await get_endpoint(self.vertex_auth, self.api_key, self.model, stream=True)

        try:
            chunks = []
            tokens_in = 0
            tokens_out = 0
            async with self.http_client.stream("POST", url, json=payload, headers=headers, timeout=60.0) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode(errors="replace")[:500]
                    logger.error(f"[AGENT STREAM] API Error {response.status_code}: {body}")
                    yield "I apologize, but I'm having trouble responding right now. Please try again or contact Rayhan directly at rayhanbp@umd.edu."
                    return

                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if not data_str: continue
                        
                        try:
                            data = json.loads(data_str)
                            
                            # Check for usage metadata in any chunk (usually final)
                            if "usageMetadata" in data:
                                usage = data["usageMetadata"]
                                tokens_in = usage.get("promptTokenCount", 0)
                                tokens_out = usage.get("candidatesTokenCount", 0)

                            # Extract text chunk (thought parts are skipped)
                            chunk_text = extract_text(data)
                            if chunk_text:
                                chunks.append(chunk_text)
                                yield chunk_text
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to decode stream chunk: {data_str}")
                            continue

            full_response = "".join(chunks)
            latency_ms = int((time.time() - start_time) * 1000)
            
            # Store in memory (fire and forget - don't block)
            asyncio.create_task(self._store_memory(session_id, query, full_response))
            
            if trace:
                self.tracer.log_generation(
                    trace=trace,
                    name="agent_response_stream",
                    model=self.model,
                    input_text=query,
                    output_text=full_response,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                    metadata={"session_id": session_id, "user_name": user_name, "streaming": True}
                )
            
            logger.info(f"Streamed response in {latency_ms}ms (Tokens: {tokens_in} in / {tokens_out} out)")

        except Exception as e:
            logger.warning(f"Stream error: {e}")
            yield f"I apologize, but I'm having trouble responding right now. Please try again or contact Rayhan directly at rayhanbp@umd.edu."
