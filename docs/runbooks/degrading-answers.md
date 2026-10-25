# Runbook: Degrading answers

**Fires when** the fallback model gave more than 20% of answers in 6 hours, or more than 2 answers in 24 hours were the apology or an error. Severity: email.

## What it means

The answer model (`GEMINI_MODEL`, default `gemini-3.5-flash`) is failing or stalling, so answers come from `gemini-3.5-flash-lite`, or from nothing. Visitors still get answers, but they are slower or weaker.

## Check first

1. `sum by (error_type, gen_ai_request_model) (increase(gen_ai_client_operation_duration_seconds_count{app_purpose="answer"}[6h]))`: errors by type and model (`429` = quota or overload, `404` = retired, `ReadTimeout` = stalled past the first-token timeout).
2. Railway logs: `[AGENT] ... failed` and `[AGENT STREAM] ... failed` lines.
3. Google Cloud status page and the model-versions page for the answer model.

## Fix

- `429` bursts: transient overload; the fallback is doing its job. If it lasts, request more quota in Google Cloud, or make the lite model the answer model for a while.
- `404`: the model is retired; see `model-retiring.md`.
- Timeouts: raise `GEMINI_FIRST_TOKEN_TIMEOUT` only if the model is slow but correct; otherwise switch models.
- Apology answers with no model errors: look for an exception in the route (`Streaming error` in the logs).

## Confirm

The fallback share returns under 20% and no new apology or error answers appear in a day.
