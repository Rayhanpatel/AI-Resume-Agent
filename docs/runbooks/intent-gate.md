# Runbook: the off-topic gate

Three email alerts point here. All read `chat.intent.decisions` (labels `gate`, `intent`, `turn`) or the daily drift check.

| Alert | Fires when |
|---|---|
| **Intent gate refusing too much** | More than half of the gate's decisions in 24 hours were refusals, with at least 6 refusals. |
| **Gate declined a mid-conversation message** | The decision model declined a message that came after an earlier turn (`turn="followup"`). |
| **Gate decision model drifted** | The daily re-score of 14 benchmark messages moved more than 0.06 on average from the recorded probabilities (`chat.intent.canary.drift`; noise alone is about 0.026). |

## What it means

The gate is a topic filter, not a security control. Most attacks are about Rayhan, so it lets them through on purpose; the answer layer handles them (`docs/EVAL-REPORT.md` §6, `docs/ARCHITECTURE.md` §12).

A refused real question is the expensive mistake: a recruiter gets "I only answer questions about his work" for a question about his work. A message wrongly let through is cheap. The answer model declines off-topic requests itself; it declined all 13 it was tested on.

- **Mid-conversation refusals** are either a visitor switching to an unrelated request (fine) or a follow-up the gate misread.
- **Many refusals in a day** are either someone testing the bot or the gate failing on real questions.
- **Drift** means the model behind the pinned name answers differently, so the thresholds no longer mean what the benchmarks measured.

## Check first

1. Langfuse → traces with metadata `intent = off_topic`. Each carries:
   - `intent_gate` (`jev`, `fallback` or `gemini`);
   - `turn` (`first` or `followup`);
   - `p_on_topic`.

   Read the refused message and, for a follow-up, the turn before it in the same session.
2. `GET /api/v1/health/llm`: `intent_gate` shows the model and both thresholds in use: first messages, and messages with a previous turn.
3. Grafana: `chat_intent_decisions_total` by `gate` and `turn`. Many `fallback` decisions mean the decision model is failing (see [decision-model.md](decision-model.md)).
4. For drift: Railway logs, `Gate drift check: average change …`, and `chat_intent_canary_drift`.

## Fix

- **A visitor's genuine off-topic switch:** nothing to do.
- **A real question refused:**
  1. If it keeps happening, roll back now: set `INTENT_GATE=gemini` on Railway. That is the Flash-Lite classifier, message only; it redeploys in about a minute.
  2. Add the message to a set with its label: `backend/evals/gate_conversations.jsonl` (with its first turn) or `intent_set.jsonl` (a first message).
  3. Re-run `evals/gate_bench.py` or `evals/intent_bench.py`. Change a threshold only if the dev half supports it. The tests in `tests/test_intent_gate.py` check that both thresholds come from the committed results.
- **Drift:** the thresholds belong to one model version. Pin the previous version (`DECISION_MODEL`) if OpenRouter still serves it. Otherwise re-run both benchmarks for the new one, and update `CANARY` in `services/intent.py` from the new results (a test checks they match).

## Confirm

- A follow-up after an answer ("How long was that?") gets an answer.
- A real question about one of his projects ("What is MedBill?") gets an answer.
- `chat_intent_decisions_total{intent="job_related"}` rises again.
- The next drift check logs an average change below 0.06.
