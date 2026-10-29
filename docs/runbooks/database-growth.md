# Runbook: Database growing

**Fires when** the `sessions` table passes 20,000 rows or `events` passes 300,000 (`chat_database_rows`). Severity: email.

## What it means

The free Supabase plan holds 500 MB. With 90-day retention the tables should stay small, so growth this large means unusual traffic (a bot getting past Turnstile, an MCP client in a loop) or a broken retention job.

## Check first

1. Is `Retention job failing` also firing? See `retention.md`.
2. Supabase → **Reports → Database**: size and growth.
3. Events by type and hashed IP in the last day (`event_type`, `metadata->>'ip_hash'`): one source dominating?
4. `chat_quota_rejections_total`: are the limits doing their job?

## Fix

- **Abuse:** tighten `RATE_LIMIT_RPM` or the per-IP daily limits, or block the source at Cloudflare.
- **Retention broken:** fix it first; the next run deletes the backlog.
- **Real growth:** shorten `data_retention_days`, or move to a paid plan.

## Confirm

The row counts fall after the next retention run.
