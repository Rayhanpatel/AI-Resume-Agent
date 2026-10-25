# Runbook: Database down

**Fires when** `chat_database_up` has been 0 for an hour (a real query to Supabase failed at startup or in `/health/llm`). Severity: email.

## What it means

The chat still answers, but:
- session history lives in memory only and is lost on each deploy;
- events and lead capture are lost;
- the quotas fall back to in-memory counts that reset on deploy.

In Oct 2026 the Supabase project's host stopped resolving: free projects pause after 7 days of low activity, and can be restored for 90 days.

## Check first

1. supabase.com/dashboard → the project: **Paused**, **Restoring**, or missing?
2. `curl -s .../api/v1/health/llm` → `database` is `unreachable` or `off` (`off` means `SUPABASE_URL` or `SUPABASE_KEY` isn't set on Railway).
3. Railway logs: `supabase.ping failed` with the error.

## Fix

- **Paused:** click **Restore project** and wait until it says healthy. The A1 probe queries it every few minutes, which keeps it from pausing again.
- **Gone, or paused more than 90 days:** create a new project, recreate the `sessions` and `events` tables, and put the new `SUPABASE_URL` and `SUPABASE_KEY` on Railway.
- **Keys rotated:** update `SUPABASE_KEY` on Railway.

## Confirm

`/health/llm` shows `"database":"ok"`, and the startup log says `✓ Database (Supabase): ok`.
