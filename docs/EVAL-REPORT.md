# Eval report: Rayhan's agent, Oct 25, 2026

What was measured, how, and the numbers. All runs were against the live models on Vertex AI (global endpoint) from a laptop in California; latencies include the network. Nothing here is a vendor figure.

## 1. Does it say the right things? Golden set, 13 questions

`backend/evals/golden.py`. Each case has regexes the answer must match and must not match, plus the banned-wording list from `tests/test_profile.py`. The set covers the questions recruiters ask most, a multi-turn follow-up, an off-topic request, a phone-number request, a question whose honest answer is "the profile doesn't say," and two questions that invite overclaiming.

| Run | Model | Result |
|---|---|---|
| Oct 25, ~4 a.m. | gemini-3.5-flash → 3.5-flash-lite | 12/12 (first version of the set) |
| Oct 25, ~4:30 a.m., twice | same | 13/13, 13/13 |
| Oct 25, ~4:45 a.m. | same | 12/13 (one answer mentioned a hackathon it wasn't asked about; the prompt was tightened) |
| Oct 25, ~5 a.m., twice | same | 13/13, 13/13 |
| Oct 25, ~6:55 a.m. (after the job-schema change) | same | 13/13 |
| Oct 25, ~8 a.m. (with stamps) | same | 13/13 |

Two provider failures occurred during these runs and were absorbed by the fallback: a 429 from `gemini-3.5-flash` and an empty `MALFORMED_RESPONSE` from `gemini-3.5-flash-lite`.

## 2. Does it stick to the profile? Verification stamps

Every sentence of an answer is stamped: `checked` (a verbatim profile quote, confirmed in code), `inferred` (the agent's own summary or framing), `unknown` (not in the profile), or `n/a` (not a claim). Numbers, dates and ranks are checked against the profile text separately.

Run of Oct 25, ~8 a.m., across the 13 golden answers:

| Claims | Checked | Inferred | Unknown | Unverified | Claims with a number not in the profile |
|---|---|---|---|---|---|
| 52 | 43 (83%) | 9 (17%) | 0 | 0 | 0 |

Verification latency: p50 1.2 s, p95 1.8 s (one fast-model call per answer plus code checks; runs after the answer has streamed, so it never delays the first word).

What "inferred" looked like: "He has not worked with Kubernetes" style framing, summaries across several profile fields, and the agent's own transitions. None was a fabricated fact in this run; the stamp exists for the day one is.

## 3. How fast? Time to first word

Measured with the full system prompt (about 16k tokens), streaming, Oct 25:

| Model | First word (s), four questions | Notes |
|---|---|---|
| gemini-3.8-flash | 33, timeout at 45, timeout at 45 (later: 1.4–2.3) | unusable that morning; may be regional load |
| gemini-3.5-flash | 0.9–1.9 (later 2.2–6.7) | chosen |
| gemini-3.5-flash-lite | 1.3–3.3 | fallback and fast model |

Live service, Oct 25 (includes the intent gate and session lookup): first words in 3.7–7.4 s. After Phase 1 removed the memory lookup: 1.5–4.4 s in the golden run.

## 4. What does it cost?

About 16k input tokens per answer (the whole profile) plus about 300 output tokens on `gemini-3.5-flash`, plus one fast-model verification call of about 9k input tokens. At current list prices this is well under a cent per answer; 100 conversations a month is cents.

## 5. What the tests cover (60 unit tests, run in CI on every push)

Request shape for Gemini 3.x; model fallback on error, silence and empty answers; the real-call health probe and its caching; signed session tokens (round trip, tampering, expiry, wrong session); quotas (in-memory and counted from the events table); the origin lock; the job-post schema boundary (bounded fields, one line each); the SSRF resolver check and the redirect refusal; the verifier (sentence splitting, quote and number checks, status mapping, decision-gate demotion, failure handling); the MCP interface (stateless tools/list and tools/call); and the banned-claim checks on the profile and the rendered prompt.

## 6. Not yet measured (honest list)

- Judge agreement with human labels. The five Langfuse judges have not been calibrated; a labeling session (60–100 traces) is planned.
- A judge from a different model family (Gemini judging Gemini favors itself).
- The decision-model second opinion: off until an OpenRouter key is set; the 200-pair study is planned.
- Multi-turn recruiter-pressure cases ("surely he's done Kubernetes in production?" three times).
- Real-user traffic numbers: the service had no working traffic between June and Oct 25.
