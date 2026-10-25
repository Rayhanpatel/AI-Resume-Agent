"""
Main Agent Service
Uses Gemini Flash for responses about Rayhan
Refactored to use raw HTTP (httpx) to bypass SDK threading issues (Feb 2026)
Moved to Gemini 3.x with a real systemInstruction (Oct 2026); Mem0 dropped for server-side history
"""
import json
import logging
import httpx
import time
import asyncio
from typing import List, Optional, AsyncGenerator

from .tracer import TracingService
from .vertex_auth import VertexAuthService
from .gemini import get_endpoint, generation_config, extract_text
from core.prompts import get_system_prompt
from core.timeout import safe_timeout

logger = logging.getLogger(__name__)

APOLOGY = ("I apologize, but I'm having trouble responding right now. "
           "Please try again or contact Rayhan directly at rayhanbp@umd.edu.")
MAX_HISTORY_TURNS = 10  # recent messages kept as real turns; Mem0 covers anything older


class AgentService:
    """Main conversational agent for resume Q&A using raw HTTP calls

    Supports both Vertex AI (production) and AI Studio (fallback)
    """

    def __init__(
        self,
        api_key: str,
        tracer: TracingService,
        http_client: httpx.AsyncClient,
        vertex_auth: VertexAuthService = None,
        model: str = "gemini-3.5-flash",
        thinking_level: str = "low",
        fallback_model: Optional[str] = None,
        first_token_timeout: float = 10.0
    ):
        self.api_key = api_key
        self.http_client = http_client
        self.tracer = tracer
        self.vertex_auth = vertex_auth
        self.model = model
        # Tried in order: the answer model, then the fallback if it fails or stalls
        self.models = [model] + ([fallback_model] if fallback_model and fallback_model != model else [])
        self.first_token_timeout = first_token_timeout
        # (model, seconds of silence allowed before moving on). The last model gets a second,
        # patient try: Vertex sometimes answers 429 or MALFORMED_RESPONSE once and fine the next time.
        self.attempts = [(m, first_token_timeout * (1 + i * 0.5)) for i, m in enumerate(self.models)]
        self.attempts.append((self.models[-1], 45.0))

        api_mode = "Vertex AI (production)" if vertex_auth and vertex_auth.enabled else "AI Studio (fallback)"
        logger.info(f"AgentService using {api_mode} with {' -> '.join(self.models)}")

        # Thinking tokens count against maxOutputTokens, so leave room for both
        self.generation_config = generation_config(max_output_tokens=2048, thinking_level=thinking_level)

    @staticmethod
    def _history_contents(history: Optional[List[dict]], query: str) -> List[dict]:
        """Turn the frontend's recent messages plus the new query into Gemini contents.
        Leading assistant turns (the welcome message) are dropped and same-role turns merged,
        so the contents alternate and always end with the user's question."""
        contents = []
        for turn in (history or [])[-MAX_HISTORY_TURNS:]:
            role = "model" if turn["role"] == "assistant" else "user"
            text = turn["content"].strip()
            if not text or (not contents and role == "model"):
                continue
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"][0]["text"] += "\n\n" + text
            else:
                contents.append({"role": role, "parts": [{"text": text}]})
        if contents and contents[-1]["role"] == "user":
            contents[-1]["parts"][0]["text"] += "\n\n" + query
        else:
            contents.append({"role": "user", "parts": [{"text": query}]})
        return contents

    async def _build_payload(self, session_id, query, user_name, company, job_info, history=None) -> dict:
        """Build the request: the system prompt as systemInstruction, then the real conversation
        from the server-side session. Gemini 3.x rejects prefilled model turns, so nothing is
        put in the model's mouth."""
        system_prompt = get_system_prompt(user_name, company or "their company", job_info)

        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": self._history_contents(history, query),
            "generationConfig": self.generation_config,
        }

    async def respond(
        self,
        session_id: str,
        query: str,
        user_name: str,
        company: Optional[str] = None,
        job_info: Optional[dict] = None,
        trace = None,
        history: Optional[List[dict]] = None
    ) -> dict:
        """Generate a full response, falling back to the next model if one fails or stalls"""
        start_time = time.time()
        payload = await self._build_payload(session_id, query, user_name, company, job_info, history)

        last_error = None
        for i, (model, wait_s) in enumerate(self.attempts):
            is_last = i == len(self.attempts) - 1
            if i == len(self.attempts) - 1:
                await asyncio.sleep(0.5)  # brief backoff before the last try
            try:
                url, headers = await get_endpoint(self.vertex_auth, self.api_key, model)
                response = await self.http_client.post(
                    url, json=payload, headers=headers,
                    timeout=wait_s + 15.0  # whole answer, not just the first token
                )
                if response.status_code != 200:
                    raise Exception(f"Gemini API Error {response.status_code}: {response.text[:300]}")

                data = response.json()
                response_text = extract_text(data)
                if not response_text:
                    candidates = data.get("candidates", [])
                    finish_reason = candidates[0].get("finishReason", "UNKNOWN") if candidates else "NO_CANDIDATES"
                    raise Exception(f"Gemini returned no text (finishReason={finish_reason})")
            except Exception as e:
                last_error = e
                logger.warning(f"[AGENT] {model} failed ({type(e).__name__}: {e}); "
                               f"{'no fallback left' if is_last else 'falling back'}")
                continue

            latency_ms = int((time.time() - start_time) * 1000)
            usage = data.get("usageMetadata", {})
            tokens_in = usage.get("promptTokenCount", 0)
            tokens_out = usage.get("candidatesTokenCount", 0)

            cost_usd = 0.0
            if trace:
                trace_result = self.tracer.log_generation(
                    trace=trace,
                    name="agent_response",
                    model=model,
                    input_text=query,
                    output_text=response_text,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                    metadata={"session_id": session_id, "user_name": user_name, "attempt": i + 1}
                )
                cost_usd = trace_result.get("cost_usd", 0.0) if trace_result else 0.0

            return {
                "response": response_text,
                "model": model,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd
            }

        return {
            "response": APOLOGY,
            "error": str(last_error),
            "latency_ms": int((time.time() - start_time) * 1000)
        }

    async def respond_stream(
        self,
        session_id: str,
        query: str,
        user_name: str,
        company: Optional[str] = None,
        job_info: Optional[dict] = None,
        trace = None,
        history: Optional[List[dict]] = None,
        stats: Optional[dict] = None
    ) -> AsyncGenerator[str, None]:
        """
        Stream a response over SSE. If the answer model errors or sends nothing within
        first_token_timeout, the next model is tried. Once text has reached the user the
        answer can't be restarted, so a failure mid-answer just ends it.
        """
        start_time = time.time()
        payload = await self._build_payload(session_id, query, user_name, company, job_info, history)

        chunks = []
        usage = {}
        used_model = None
        finish_reason = None
        for i, (model, wait_s) in enumerate(self.attempts):
            if i == len(self.attempts) - 1:
                await asyncio.sleep(0.5)  # brief backoff before the last try
            # A model that stays silent for wait_s seconds is abandoned for the next attempt
            timeout = httpx.Timeout(60.0, connect=10.0, read=wait_s)
            try:
                url, headers = await get_endpoint(self.vertex_auth, self.api_key, model, stream=True)
                async with self.http_client.stream("POST", url, json=payload, headers=headers, timeout=timeout) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode(errors="replace")[:300]
                        raise Exception(f"Gemini API Error {response.status_code}: {body}")

                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str:
                            continue
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to decode stream chunk: {data_str[:200]}")
                            continue

                        usage = data.get("usageMetadata", usage)
                        for cand in data.get("candidates", []):
                            finish_reason = cand.get("finishReason", finish_reason)
                        chunk_text = extract_text(data)  # thought parts are skipped
                        if chunk_text:
                            chunks.append(chunk_text)
                            used_model = model
                            yield chunk_text
                if chunks:
                    break
                logger.warning(f"[AGENT STREAM] {model} returned no text (finishReason={finish_reason})")
            except Exception as e:
                logger.warning(f"[AGENT STREAM] {model} failed ({type(e).__name__}: {e})")
                if chunks:
                    break

        if not chunks:
            yield APOLOGY
            return

        full_response = "".join(chunks)
        latency_ms = int((time.time() - start_time) * 1000)
        tokens_in = usage.get("promptTokenCount", 0)
        tokens_out = usage.get("candidatesTokenCount", 0)
        if stats is not None:
            # The route logs these after the stream ends (tokens feed the daily budget)
            stats.update({"model": used_model, "tokens_in": tokens_in, "tokens_out": tokens_out,
                          "latency_ms": latency_ms, "response": full_response})

        if trace:
            self.tracer.log_generation(
                trace=trace,
                name="agent_response_stream",
                model=used_model,
                input_text=query,
                output_text=full_response,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                metadata={"session_id": session_id, "user_name": user_name, "streaming": True,
                          "fallback": used_model != self.model}
            )

        logger.info(f"Streamed response from {used_model} in {latency_ms}ms (Tokens: {tokens_in} in / {tokens_out} out)")
