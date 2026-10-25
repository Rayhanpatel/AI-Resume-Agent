"""
Answer verification: every sentence the agent says gets a stamp.

    checked   the sentence is supported by a quote that appears verbatim in the profile
              (the model proposes the quote, code confirms it)
    inferred  the model says the profile supports it but no verifiable quote backs it,
              or the sentence is the agent's own reasoning or framing
    unknown   the profile does not contain this (the agent should have said so)
    n/a       not a claim about Rayhan (greetings, "email him", offers to help)

Numbers get their own check: every number, date and rank in a claim must appear
verbatim in the profile text, or the claim is marked `numbers_unverified`.

The verdicts come from a fast model in JSON mode; the quote check and the number
check are code. An optional decision-model backend (OpenRouter Decisions API) adds
a per-sentence probability that is logged alongside, and demotes a "checked" stamp
to "inferred" when it disagrees strongly. Thresholds live here, in one place.
"""
import hashlib
import json
import logging
import re
import time
from typing import List, Optional

import httpx

from core import telemetry
from core.prompts import PROFILE, PROFILE_TEXT
from services.gemini import get_endpoint, generation_config, extract_text

logger = logging.getLogger(__name__)

PROFILE_VERSION = hashlib.sha256(PROFILE_TEXT.encode()).hexdigest()[:12]
PROFILE_AS_OF = (PROFILE.get("meta") or {}).get("updated", "unknown")

NOUL_DEMOTE_BELOW = 0.5       # decision model strongly disagrees with a "checked" stamp
MIN_CLAIM_CHARS = 12
NUMBER_RE = re.compile(r"(?<![\w.])[+\-−]?\d[\d,]*(?:\.\d+)?(?:\s?[%×x])?(?:st|nd|rd|th)?(?![\w])")


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace("−", "-").replace("×", "x")
    return re.sub(r"\s+", " ", text).strip().lower()


_PROFILE_NORM = _norm(PROFILE_TEXT)


def split_sentences(answer: str) -> List[str]:
    """Split markdown-ish prose into sentences; bullets and lines count as boundaries"""
    text = re.sub(r"\*\*|__|`", "", answer)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # keep link text, drop the URL
    parts = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip()
        if not line:
            continue
        parts += re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", line)
    return [p.strip() for p in parts if len(p.strip()) >= MIN_CLAIM_CHARS]


def quote_in_profile(quote: str) -> bool:
    q = _norm(quote or "")
    return len(q) >= 8 and q in _PROFILE_NORM


def numbers_in(text: str) -> List[str]:
    return [m.group(0).strip() for m in NUMBER_RE.finditer(text)]


def unverified_numbers(sentence: str) -> List[str]:
    """Numbers in the sentence that appear nowhere in the profile text"""
    missing = []
    for raw in numbers_in(sentence):
        token = _norm(raw).rstrip("stndrh").replace(",", "")
        bare = re.sub(r"\s?[%x]$", "", token)
        if bare and bare not in _PROFILE_NORM.replace(",", ""):
            missing.append(raw)
    return missing


VERIFY_PROMPT = """You are checking an AI assistant's answer about Rayhan Patel against his PROFILE (JSON).
For each numbered SENTENCE decide:
- "supported": the profile states this. Give the shortest exact, verbatim substring of the profile (10-200 characters, copied character for character) that supports it, and the JSON path of the field it comes from.
- "inferred": the profile supports the gist but the sentence adds interpretation, summary across fields, or wording the profile does not use. Give the closest verbatim quote if one exists, else null.
- "not_in_profile": the profile does not contain this claim.
- "not_a_claim": greeting, offer to help, contact pointer, or a question; not a factual claim about Rayhan.
Return ONLY a JSON array, one object per sentence, in order:
[{{"i": 0, "verdict": "supported", "quote": "...", "path": "experience[0].highlights[2]"}}, ...]

PROFILE:
{profile}

SENTENCES:
{sentences}"""


class GeminiJsonVerifier:
    """Verdicts and quotes from the fast model in JSON mode"""

    def __init__(self, http_client: httpx.AsyncClient, vertex_auth, api_key: str, model: str):
        self.http_client, self.vertex_auth, self.api_key, self.model = http_client, vertex_auth, api_key, model

    async def verdicts(self, sentences: List[str]) -> List[dict]:
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences))
        url, headers = await get_endpoint(self.vertex_auth, self.api_key, self.model)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": VERIFY_PROMPT.format(profile=PROFILE_TEXT, sentences=numbered)}]}],
            "generationConfig": generation_config(max_output_tokens=4096, json_mode=True),
        }
        provider = telemetry.provider_for(self.vertex_auth)
        with telemetry.gen_ai_span(self.model, "verify", provider) as span:
            response = await self.http_client.post(url, json=payload, headers=headers, timeout=30.0)
            telemetry.record_response(span, self.model, "verify", response, provider)
        if response.status_code != 200:
            raise RuntimeError(f"verifier HTTP {response.status_code}: {response.text[:200]}")
        text = extract_text(response.json()).strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        if not isinstance(data, list):
            raise RuntimeError("verifier returned a non-list")
        return data


class DecisionsGate:
    """
    Optional second opinion from a decision model on OpenRouter's Decisions API
    (Jev and 21 peers share it). One request: state = the profile, one `noul`
    question per sentence. Returns {index: probability}. Thresholds are per model,
    so the model ID is pinned in settings and logged with every probability.
    """

    def __init__(self, http_client: httpx.AsyncClient, api_key: str, model: str):
        self.http_client, self.api_key, self.model = http_client, api_key, model

    async def probabilities(self, sentences: List[str]) -> dict:
        questions = {
            f"s{i}": {
                "type": "noul",
                "instructions": f"Is this statement supported by the PROFILE? Statement: {s}",
                "criteria": {"true": "The profile states this fact, in these or equivalent words.",
                             "false": "The profile does not state this, or states something different."},
            }
            for i, s in enumerate(sentences)
        }
        with telemetry.gen_ai_span(self.model, "decision", telemetry.PROVIDER_OPENROUTER) as span:
            response = await self.http_client.post(
                "https://openrouter.ai/api/alpha/decisions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "state": "PROFILE (JSON):\n" + PROFILE_TEXT, "questions": questions},
                timeout=20.0,
            )
            if response.status_code != 200:
                telemetry.mark_error(span, str(response.status_code))
        if response.status_code != 200:
            raise RuntimeError(f"decisions HTTP {response.status_code}: {response.text[:200]}")
        answers = response.json().get("answers", {})
        return {int(k[1:]): float(v.get("noul")) for k, v in answers.items() if k.startswith("s") and v.get("noul") is not None}


async def verify_answer(answer: str, verifier: GeminiJsonVerifier, gate: Optional[DecisionsGate] = None) -> dict:
    """Stamp every sentence of an answer. Never raises; a failed check yields 'unverified'."""
    start = time.time()
    sentences = split_sentences(answer)
    result = {"profile_version": PROFILE_VERSION, "as_of": PROFILE_AS_OF, "claims": [],
              "summary": {"checked": 0, "inferred": 0, "unknown": 0, "numbers_unverified": 0, "unverified": 0},
              "latency_ms": 0, "gate_model": gate.model if gate else None}
    if not sentences:
        return result

    try:
        verdicts = await verifier.verdicts(sentences)
    except Exception as e:
        logger.warning(f"[VERIFY] verdicts failed: {e}")
        verdicts = []
    by_index = {v.get("i"): v for v in verdicts if isinstance(v, dict)}

    probs = {}
    if gate:
        try:
            probs = await gate.probabilities(sentences)
        except Exception as e:
            logger.warning(f"[VERIFY] decision gate failed: {e}")

    for i, sentence in enumerate(sentences):
        v = by_index.get(i) or {}
        verdict = v.get("verdict")
        quote = (v.get("quote") or "").strip() or None
        quote_ok = quote_in_profile(quote) if quote else False
        if not v:
            status = "unverified"
        elif verdict == "not_a_claim":
            status = "n/a"
        elif verdict == "supported" and quote_ok:
            status = "checked"
        elif verdict in ("supported", "inferred"):
            status = "inferred"
        elif verdict == "not_in_profile":
            status = "unknown"
        else:
            status = "unverified"

        p = probs.get(i)
        if status == "checked" and p is not None and p < NOUL_DEMOTE_BELOW:
            status = "inferred"

        missing = unverified_numbers(sentence) if status in ("checked", "inferred") else []
        claim = {"text": sentence, "status": status,
                 "evidence": {"quote": quote, "path": v.get("path"), "verified": quote_ok} if quote else None,
                 "numbers_unverified": missing}
        if p is not None:
            claim["gate_probability"] = round(p, 3)
        result["claims"].append(claim)
        if status in result["summary"]:
            result["summary"][status] += 1
        if missing:
            result["summary"]["numbers_unverified"] += 1

    result["latency_ms"] = int((time.time() - start) * 1000)
    telemetry.record_stamps(result["summary"])
    return result
