# Runbook: Slow

**Fires when** the 95th-percentile time to first word for real visitors over 24 hours is above 6 seconds. Severity: email.

## What it means

Visitors wait too long before the answer starts. With a few visitors a day this is a coarse signal; A2's 15-second limit is the steady one.

## Check first

1. `histogram_quantile(0.95, sum by (le, gen_ai_request_model) (rate(gen_ai_client_operation_time_to_first_chunk_seconds_bucket[24h])))`: is the model slow, or is it everything before the model?
2. `histogram_quantile(0.95, sum by (le, app_purpose) (rate(gen_ai_client_operation_duration_seconds_bucket[24h])))`: the intent check runs before every answer and adds about a second.
3. A trace of a slow request in Grafana (Explore → Tempo, filter `service.name = rayhan-agent`) shows where the time went.

## Fix

- The model itself: compare models on `/health/llm` latency and switch `GEMINI_MODEL` if one is clearly faster at the same quality (measure first).
- The intent check: it uses the fast model; a slow fast model slows every answer.
- Cold starts after a deploy: the first answer after a restart is slower; ignore one-offs.

## Confirm

p95 time to first word drops under 6 s over the next day.
