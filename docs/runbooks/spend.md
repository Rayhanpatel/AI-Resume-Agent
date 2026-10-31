# Runbook: Spend

**Fires when** Google Cloud spend passes 90% of the monthly budget (Billing budget alert), or tokens in the last 24 h pass 90% of the backend's daily budget (Grafana, early warning). Severity: page.

## What it means

Something is using the model far more than a few visitors a day would: a scraper, a loop, a probe gone wrong, or a model whose price changed.

## Check first

1. Grafana: `sum by (gen_ai_request_model, app_purpose) (increase(gen_ai_client_token_usage_sum[24h]))`. Which model and which part of the system?
2. `sum by (app_synthetic, app_channel) (increase(chat_answers_total[24h]))`: is it web or MCP traffic, real or probes?
3. Railway logs: many requests from one place? Quota rejections (`chat_quota_rejections_total`) going up means the limits are working.

## Fix

- Abuse through the web chat: the quotas (40 messages per session, 80 per IP per day, 500k tokens per day) should hold. If they don't, check that the database answers (`database-down.md`), because without it the limits fall back to in-memory counts that reset on deploy.
- Abuse through `/mcp`: since `0fa047e` every POST passes `core/mcp_limits.py` (per-minute limiter, per-IP daily limit, global token budget). Per-IP limits key on `X-Real-IP`, which Railway's edge overwrites (since Oct 25; caller-sent `CF-Connecting-IP` and `X-Forwarded-For` are ignored). The global token budget and the GCP cap are the ceilings behind them. To stop it outright, remove the `/mcp` mount in `main.py` and deploy.
- A runaway probe: pause the Synthetics check in Grafana.
- **Benchmarks and evals share the cap.** `evals/*.py` run against the same Google Cloud project, so a long benchmark day counts toward the same monthly cap as visitors (it tripped the cap on Oct 30, see the post-mortem). Before a large run, check the month's spend in the Billing console.
- Last resort: the Google Cloud budget's hard cap stops all model calls; the chat then shows the apology.

## When the cap has tripped

Every model call returns `403 Spend cap breached for project ... aiplatform.googleapis.com`, `/health/llm` reports `failing` with `HTTP 403`, and A1 pages. The cap lifts by itself at the start of next month. To lift it sooner (owner's decision; the budget is `Chat bot Vertex AI spend cap` on billing account `01B025-D1FF04-5B44FB`):

```bash
B=billingAccounts/01B025-D1FF04-5B44FB/budgets/6b4358a8-65d8-4634-8a4f-655590a60048
T=$(gcloud auth print-access-token); H=(-H "Authorization: Bearer $T" -H "x-goog-user-project: gen-lang-client-0814894727" -H "Content-Type: application/json")
# 1. lift it (no cap for the rest of the month until step 2)
curl -X PATCH "${H[@]}" "https://billingbudgets.googleapis.com/v1/$B?updateMask=spendCap.inputState" -d '{"spendCap":{"inputState":"AWAITING_NEXT_PERIOD"}}'
# 2. once the lift has finished (a few minutes; until then this returns SPEND_CAP_LIFT_PENDING), re-arm at a higher amount
curl -X PATCH "${H[@]}" "https://billingbudgets.googleapis.com/v1/$B?updateMask=amount,displayName,spendCap.inputState" \
  -d '{"displayName":"Chat bot Vertex AI spend cap ($25/month)","amount":{"specifiedAmount":{"currencyCode":"USD","units":"25"}},"spendCap":{"inputState":"CONFIGURED"}}'
```

Google says services can take up to an hour to recover after a lift. While the cap is enforced, any other edit to the budget fails with `FAILED_PRECONDITION`. Source: [Google Cloud, spend caps](https://docs.cloud.google.com/billing/docs/how-to/budget-api-spend-cap).

## Confirm

Token usage per hour returns to normal for a few hours, and the budget forecast is under 100%.
