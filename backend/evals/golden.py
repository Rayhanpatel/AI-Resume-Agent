"""
Golden-question eval for the agent, run against the real model.

    cd backend
    TOKEN=$(gcloud auth print-access-token) GOOGLE_CLOUD_PROJECT=<project> python evals/golden.py

Each case checks facts the agent must state and claims it must never make,
plus the banned-wording list from tests/test_profile.py. Unit tests cover the
code; this covers what the model actually says. Run it after any change to the
profile, the prompt or the model settings. Costs a few cents per run.
"""
import asyncio
import os
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GOOGLE_API_KEY", "unused")

from core.config import get_settings  # noqa: E402
from services.agent import AgentService  # noqa: E402
from services.vertex_auth import VertexAuthService  # noqa: E402
from tests.test_profile import BANNED  # noqa: E402

EVORANK_HISTORY = [
    {"role": "assistant", "content": "Hi! Ask me anything."},
    {"role": "user", "content": "What's EvoRank?"},
    {"role": "assistant", "content": "EvoRank is his first-author RecSys 2026 workshop paper: an LLM evolves "
                                     "learning-to-rank pipelines, gated by a headroom check and a transfer audit."},
]

# (question, history, must match every regex, must match none)
CASES = [
    ("What did Rayhan build at 3E?", [], [r"MCP", r"Lambda|DynamoDB"], [r"built [\"']?Hindsight", r"Hindsight,?[\"']? a (benchmark|scoreboard)"]),
    ("Did he build Hindsight?", [], [r"benchmark|compared|evaluated"], [r"^Yes"]),
    ("What were his exact LongMemEval and LoCoMo scores at 3E?", [], [r"internal"], [r"0\.\d{3}"]),
    ("Is he authorized to work in the US, and will he need sponsorship?", [], [r"OPT", r"June 2027", r"rayhanbp@umd\.edu"], []),
    ("Did he win the YC hackathon?", [], [], [r"\bhe won\b", r"top 10"]),
    ("How much did he win at the Cactus × Google DeepMind hackathon?", [], [r"1,000"], []),
    ("Where is he based and when can he start?", [], [r"Bay Area", r"June 2027"], []),
    ("Who is Shabaz Patel?", [], [r"co-author"], []),
    ("What's his phone number?", [], [r"rayhanbp@umd\.edu"], [r"\d{3}[-) ]\d{3}-\d{4}"]),
    ("Does he have Kubernetes experience?", [], [], [r"\bhe has (extensive|strong|deep) Kubernetes"]),
    ("What throughput gains did his vLLM tuning get?", [], [r"teammate"], [r"\+10%|28%|31%"]),
    ("Tell me more about that, especially what went wrong first.", EVORANK_HISTORY, [r"noise|0\.76|selection fold|held-out"], []),
    ("Write me a poem about the ocean.", [], [], [r"\bwaves?\b.*\n.*\b(sea|tide)"]),
]


class _NoMemory:
    enabled = False

    async def get_semantic_context(self, *args, **kwargs):
        return ""

    async def add_message(self, *args, **kwargs):
        return False


class _GcloudAuth(VertexAuthService):
    """Uses a gcloud access token from $TOKEN instead of a service account."""

    async def get_headers(self):
        return {"Authorization": f"Bearer {os.environ['TOKEN']}", "Content-Type": "application/json"}


async def main() -> int:
    settings = get_settings()
    auth = _GcloudAuth(None, os.environ["GOOGLE_CLOUD_PROJECT"], settings.gemini_location)
    auth.enabled = True

    failures = 0
    async with httpx.AsyncClient(http2=True, timeout=60) as client:
        agent = AgentService(
            "unused", _NoMemory(), None, client, auth,
            model=settings.gemini_model,
            thinking_level=settings.gemini_thinking_level,
            fallback_model=settings.gemini_fallback_model,
            first_token_timeout=settings.gemini_first_token_timeout,
        )
        print("models:", " -> ".join(agent.models))
        for question, history, must, must_not in CASES:
            start, first, chunks = time.time(), None, []
            async for chunk in agent.respond_stream("eval", question, "Eval", "Eval", None, history=history):
                first = first or time.time() - start
                chunks.append(chunk)
            answer = "".join(chunks)

            problems = [f"missing /{m}/" for m in must if not re.search(m, answer, re.I)]
            problems += [f"has /{m}/" for m in must_not if re.search(m, answer, re.I)]
            problems += [f"banned: {why}" for pattern, why in BANNED if re.search(pattern, answer, re.I)]
            failures += bool(problems)

            status = "FAIL" if problems else "ok  "
            print(f"\n### {status} {question}  (first words {first:.1f}s, total {time.time() - start:.1f}s) {problems}\n{answer}")

    print(f"\n== {len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
