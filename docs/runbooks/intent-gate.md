# Runbook: Intent gate refusing too much

**Fires when** more than half of the off-topic gate's decisions in 24 hours were refusals, with at least 6 refusals (`chat.intent.decisions`). Severity: email.

## What it means

Either visitors really sent many off-topic or hostile messages (someone testing the bot), or the gate is declining real questions, which is the expensive mistake: a recruiter gets "I only answer questions about his work" for a question about his work.

## Check first

1. Langfuse → traces from the last day with metadata `intent = off_topic`. Each carries `intent_gate` (`jev`, `fallback` or `gemini`) and, for the decision model, `p_on_topic`. Read the refused messages.
2. If most are real questions about Rayhan: note their `p_on_topic`. Short follow-ups ("tell me more", "why?") score lowest, because the gate sees only the current message.
3. `GET /api/v1/health/llm`: `intent_gate` shows the model and threshold in use.
4. Grafana: `chat_intent_decisions_total` by `gate`. Many `fallback` decisions mean the decision model is failing (see [decision-model.md](decision-model.md)).

## Fix

- **Off-topic traffic:** nothing to do.
- **Real questions refused, decision model at fault:** roll back now by setting `INTENT_GATE=gemini` on Railway (the Flash-Lite classifier; redeploys in about a minute). Then add the refused messages to `backend/evals/intent_set.jsonl` with their labels, re-run `evals/intent_bench.py`, and change the threshold or the question only if the benchmark supports it (the threshold must come from the dev half; the test in `tests/test_intent_gate.py` checks this).
- **The model version changed:** the threshold belongs to one version. Pin the old one (`DECISION_MODEL`) or re-run the benchmark for the new one.

## Confirm

A real question about one of his projects ("What is MedBill?") gets an answer, and `chat_intent_decisions_total{intent="job_related"}` rises again.
