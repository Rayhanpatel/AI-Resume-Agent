"""
Gemini request helpers shared by every service.
One place for the endpoint choice (Vertex AI vs AI Studio), the generation
config and response parsing, so a model change is a config change.
"""
import time
from typing import Tuple

import httpx

AI_STUDIO_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


async def get_endpoint(vertex_auth, api_key: str, model: str, stream: bool = False) -> Tuple[str, dict]:
    """Return (url, headers) for a generate call, preferring Vertex AI"""
    if vertex_auth and vertex_auth.enabled:
        url = vertex_auth.get_streaming_url(model) if stream else vertex_auth.get_generate_url(model)
        return url, await vertex_auth.get_headers()

    method = "streamGenerateContent?alt=sse" if stream else "generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    return f"{AI_STUDIO_BASE}/{model}:{method}", headers


def generation_config(max_output_tokens: int, thinking_level: str = "low", json_mode: bool = False) -> dict:
    """
    Generation config for Gemini 3.x models.
    temperature/topP are deprecated on these models, and thinking tokens
    count against maxOutputTokens, so limits are higher than on 2.0 Flash.
    """
    config = {
        "maxOutputTokens": max_output_tokens,
        "thinkingConfig": {"thinkingLevel": thinking_level},
    }
    if json_mode:
        config["responseMimeType"] = "application/json"
    return config


def extract_text(data: dict) -> str:
    """Join the visible text parts of a response (or stream chunk), skipping thought parts"""
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(
        p.get("text", "") for p in parts
        if isinstance(p, dict) and not p.get("thought")
    )


async def check_llm(http_client: httpx.AsyncClient, vertex_auth, api_key: str, model: str) -> dict:
    """
    Make one tiny real generate call and report whether it worked.
    The plain /health endpoint only checks config flags, which said "healthy"
    for months while every chat call failed (retired model, closed billing).
    """
    api_mode = "Vertex AI" if vertex_auth and vertex_auth.enabled else "AI Studio"
    start = time.time()
    try:
        url, headers = await get_endpoint(vertex_auth, api_key, model)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": "Reply with the single word: ok"}]}],
            "generationConfig": generation_config(max_output_tokens=256),
        }
        response = await http_client.post(url, json=payload, headers=headers, timeout=20.0)
        latency_ms = int((time.time() - start) * 1000)
        if response.status_code != 200:
            return {"ok": False, "api_mode": api_mode, "model": model, "latency_ms": latency_ms,
                    "error": f"HTTP {response.status_code}: {response.text[:300]}"}
        text = extract_text(response.json())
        return {"ok": bool(text), "api_mode": api_mode, "model": model, "latency_ms": latency_ms,
                "error": None if text else "empty response"}
    except Exception as e:
        return {"ok": False, "api_mode": api_mode, "model": model,
                "latency_ms": int((time.time() - start) * 1000), "error": str(e)[:300]}
