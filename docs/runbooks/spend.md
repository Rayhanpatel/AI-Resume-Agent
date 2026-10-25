# Runbook: Spend

**Fires when** Google Cloud spend passes 90% of the monthly budget (Billing budget alert), or tokens in the last 24 h pass 90% of the backend's daily budget (Grafana, early warning). Severity: page.

## What it means

Something is using the model far more than a few visitors a day would: a scraper, a loop, a probe gone wrong, or a model whose price changed.

## Check first

1. Grafana: `sum by (gen_ai_request_model, app_purpose) (increase(gen_ai_client_token_usage_sum[24h]))`. Which model and which part of the system?
2. `sum by (app_synthetic, app_channel) (increase(chat_answers_total[24h]))`: is it web or MCP traffic, real or probes?
3. Railway logs: many requests from one place? Quota rejections (`chat_quota_rejections_total`) going up means the limits are working.

## Fix

- Abuse through the web chat: the quotas (40 messages per session, 80 per IP per day, 3M tokens per day) should hold. If they don't, check that the database answers (`database-down.md`), because without it the limits fall back to in-memory counts that reset on deploy.
- Abuse through `/mcp`: as of Oct 25, 2026 it has **no** rate limit or quota. The rate limiter and the quotas are dependencies of the chat routes, not middleware, despite what the `mcp_server.py` docstring says. Turn the MCP mount off (`app.mount("/mcp", ...)` in `main.py`) and deploy, until it has its own limit.
- A runaway probe: pause the Synthetics check in Grafana.
- Last resort: the Google Cloud budget's hard cap stops all model calls; the chat then shows the apology.

## Confirm

Token usage per hour returns to normal for a few hours, and the budget forecast is under 100%.
