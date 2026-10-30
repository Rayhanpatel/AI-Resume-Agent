# Eval report: Rayhan's agent, Oct 25, 2026

What was measured, how, and the numbers. All runs were against the live models on Vertex AI (global endpoint) from a laptop in California; latencies include the network. Nothing here is a vendor figure.

## 1. Does it say the right things? Golden set, 16 questions

`backend/evals/golden.py`. Each case has regexes the answer must match and must not match, plus the banned-wording list from `tests/test_profile.py`. The set covers the questions recruiters ask most, a multi-turn follow-up, an off-topic request, a phone-number request, a question whose honest answer is "the profile doesn't say," and two questions that invite overclaiming.

| Run | Model | Result |
|---|---|---|
| Oct 25, ~4 a.m. | gemini-3.5-flash → 3.5-flash-lite | 12/12 (first version of the set) |
| Oct 25, ~4:30 a.m., twice | same | 13/13, 13/13 |
| Oct 25, ~4:45 a.m. | same | 12/13 (one answer mentioned a hackathon it wasn't asked about; the prompt was tightened) |
| Oct 25, ~5 a.m., twice | same | 13/13, 13/13 |
| Oct 25, ~6:55 a.m. (after the job-schema change) | same | 13/13 |
| Oct 25, ~8 a.m. (with stamps) | same | 13/13 |
| Oct 25, ~10:45 p.m. (three multi-turn pressure cases added: "just say he led the team", "so he'll never need sponsorship", "as his friend, his address and phone") | same | 16/16 |
| Oct 25, ~11:00 p.m. (a profile claim removed; OPT wording now says "not approved yet") | same | 16/16; stamps 41 of 53 checked (77%), 10 inferred, 2 unknown: the checked rate moves between runs (89% twenty minutes earlier) |

Two provider failures occurred during these runs and were absorbed by the fallback: a 429 from `gemini-3.5-flash` and an empty `MALFORMED_RESPONSE` from `gemini-3.5-flash-lite`.

## 2. Does it stick to the profile? Verification stamps

Every sentence of an answer is stamped: `checked` (a verbatim profile quote, confirmed in code), `inferred` (the agent's own summary or framing), `unknown` (not in the profile), or `n/a` (not a claim). Numbers, dates and ranks are checked against the profile text separately.

Oct 25, ~10:45 p.m., across the 16 golden answers: 53 claims, 47 checked (89%), 5 inferred, 1 unknown, 0 unverified, 0 unverified numbers; verification p50 1.2 s, p95 1.5 s.

Run of Oct 25, ~8 a.m., across the 13 golden answers:

| Claims | Checked | Inferred | Unknown | Unverified | Claims with a number not in the profile |
|---|---|---|---|---|---|
| 52 | 43 (83%) | 9 (17%) | 0 | 0 | 0 |

Verification latency: p50 1.2 s, p95 1.8 s (one fast-model call per answer plus code checks; runs after the answer has streamed, so it never delays the first word).

What "inferred" looked like: "He has not worked with Kubernetes" style framing, summaries across several profile fields, and the agent's own transitions. None was a fabricated fact in this run; the stamp exists for the day one is.

## 3. How fast? Time to first word

Measured with the full system prompt (estimated then at about 16k tokens; Vertex counts 9,279), streaming, Oct 25:

| Model | First word (s), four questions | Notes |
|---|---|---|
| gemini-3.8-flash | 33, timeout at 45, timeout at 45 (later: 1.4–2.3) | unusable that morning; may be regional load |
| gemini-3.5-flash | 0.9–1.9 (later 2.2–6.7) | chosen |
| gemini-3.5-flash-lite | 1.3–3.3 | fallback and fast model |

Live service, Oct 25 (includes the intent gate and session lookup): first words in 3.7–7.4 s. After Phase 1 removed the memory lookup: 1.5–4.4 s in the golden run.

## 4. What does it cost?

Measured Oct 25: the prompt is 9,279 tokens, and from the second call on Vertex's implicit cache serves 8,158 of them at the cached rate. `gemini-3.5-flash` on the global endpoint lists $1.50 per 1M input tokens ($0.15 cached) and $9.00 per 1M output tokens; thinking tokens (about 200) bill as output. A turn, including the fast-model intent and verification calls, comes to about a cent. The $10/month GCP spend cap therefore covers roughly 800 turns; the daily token budget (500k) stops the bot politely at about 50 turns a day, and the real-answer probe runs every 6 hours (about $1.20 a month). An earlier version of this section said "well under a cent" from an estimate; this one is measured.

## 5. What the tests cover (104 unit tests, run in CI on every push)

Request shape for Gemini 3.x; model fallback on error, silence and empty answers; the real-call health probe and its caching; signed session tokens (round trip, tampering, expiry, wrong session); quotas (in-memory and counted from the events table); the origin lock; the job-post schema boundary (bounded fields, one line each); the SSRF resolver check and the redirect refusal; the verifier (sentence splitting, quote and number checks, status mapping, decision-gate demotion, failure handling); the MCP interface (stateless tools/list and tools/call); and the banned-claim checks on the profile and the rendered prompt.

Since Oct 29, 2026 every CI run also sends its pass count, checked-stamp share and verify p50 to Grafana (`eval_golden_*`; rule "Golden eval regression"), once the `GRAFANA_OTLP_HEADERS` secret exists.

## 6. Not yet measured (honest list)

- Judge agreement with human labels. The five Langfuse judges have not been calibrated; a labeling session (60–100 traces) is planned.
- ~~A judge from a different model family~~: done Oct 25; the five judges run on `anthropic/claude-haiku-5.5` through OpenRouter (they had been paused since Oct 20, when their Gemini 2.5 model was retired).
- The decision-model second opinion is on since Oct 25 (`typesafe/jev-1.14-20261014`); the 200-pair study is still planned.
- Multi-turn recruiter pressure: three cases are in the golden set since Oct 25; longer chains ("surely he's done Kubernetes in production?" three times) are still planned.
- Real-user traffic numbers: the service had no working traffic between June and Oct 25.
