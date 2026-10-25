# Runbook: Chat broken

**Fires when** a probe fails twice in a row: B1 (chat page), A1 (model and database health) or A2 (a real answer). Severity: page.

## What it means

Visitors probably can't start a chat or get an answer. The alert names the check; start there.

## Check first

1. Open chat.rayhanpatel.com in a private window and start a chat. Do you get an answer?
2. `curl -s https://rayhan-ai-resume-production-6239.up.railway.app/api/v1/health/llm`: look at `status`, `model` and `database`.
3. Railway → the backend service → **Deployments**: did a deploy just happen, or is the latest one crashed?
4. In Grafana, the failed check's logs show which assertion failed. A2 also shows the answer it got.

## Fix, by what failed

| Failed | Likely cause | Fix |
|---|---|---|
| B1: `Turnstile has no configuration error (state: error:400020)` or `110200` | the site key is invalid, or the domain isn't allowed on the widget | Cloudflare → Turnstile: check the widget and its hostnames; put the site key in `frontend/src/components/WelcomeForm.jsx` and the secret in Railway `TURNSTILE_SECRET_KEY` (they must be a pair) |
| B1: `chat form renders` | the frontend build or Vercel is down | Vercel → Deployments; roll back to the last good one |
| A1: `model answers` | the model ID is retired or renamed, billing is off, or quota | `/health/llm` error line, then Railway logs. Retired model: see `model-retiring.md`. Billing: Google Cloud → Billing → the project is linked to an open account |
| A1: `database answers` | the Supabase project is paused or gone | see `database-down.md` (the chat still answers without it) |
| A2: apology, or no "3E" | every model attempt failed, or the profile or prompt changed | Railway logs for `[AGENT]` lines; run the golden eval (`backend/evals/golden.py`) |
| A2: over 15 s | the answer model is slow (Gemini 3.8 Flash took 25–45 s on Oct 25, 2026) | check `gen_ai_client_operation_duration_seconds` by model; switch `GEMINI_MODEL` if one model is slow |

## Confirm

The next two runs of the failed check pass, and a chat in a private window answers.
