# Runbook: Retention job failing

**Fires when** the daily job that deletes visitor chats idle for more than 90 days (`data_retention_days`) failed for a store, or has not succeeded for 2 days. Severity: email.

## What it means

Old visitor data is not being deleted, which breaks the promise in the chat's privacy note. Nothing is broken for visitors.

## Check first

1. Railway logs: `Retention (90 days) failed for supabase|langfuse:` and the error.
2. **Supabase:** a timeout on a very large delete, or the database paused (see `database-down.md`).
3. **Langfuse:** 401 (keys rotated), 429 (rate limit), or an API change in the SDK's `observations.get_many` / `trace.delete_multiple`.
4. Not run at all: the app has not been up 10 minutes since a deploy, or `chat_retention_runs_total` is missing because telemetry is off.

## Fix

- Fix the cause, then redeploy (the job runs 10 minutes after each start).
- Langfuse: one run reads up to 10 pages of 1,000 old traces (following the cursor) and deletes them in batches; Langfuse processes deletions in the background, so a large backlog clears over a few runs.

## Confirm

Logs show `Retention (90 days): deleted N from supabase` and `... from langfuse`, and `chat_retention_runs_total{outcome="ok"}` goes up for both.
