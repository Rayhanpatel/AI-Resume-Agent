# Monitoring

Probes, alert rules and the model-lifecycle check for chat.rayhanpatel.com and rayhanpatel.com. Why it looks like this (three silent outages, a few visitors a day, one person on call) is in [`docs/MONITORING.md`](../docs/MONITORING.md).

| File | What it is |
|---|---|
| `checks/b1-chat-page.browser.js` | **B1**, browser check, every 15 min: the site and resume load; the chat form renders; the Turnstile widget has no configuration error |
| `checks/a1-health.k6.js` | **A1**, scripted check, every 5 min: `/health/llm` says the model and the database both answer |
| `checks/a2-real-answer.k6.js` | **A2**, scripted check, every 6 hours (each run costs about a cent under the $10/month spend cap): the agent answers a fixed question correctly and with verification stamps, the decision model answers, in under 15 s |
| `checks/s1-site.k6.js` | **S1**, scripted check, every 15 min: rayhanpatel.com names him and has the chat launcher; the resume is a real PDF; `profile.json` is valid |
| `checks/d1-dns.json` | **D1**, DNS checks, hourly: MX and DMARC for rayhanpatel.com (mail to the domain), the `chat` CNAME to Vercel |
| `checks/d2-domain-expiry.k6.js` | **D2**, scripted check, daily: rayhanpatel.com has at least 45 days left at the .com registry |
| `alert-rules.yaml` | the alert rules: query, condition, severity, runbook |
| `model_lifecycle.py` | the weekly check that no configured model retires within 60 days (run by `.github/workflows/model-lifecycle.yml`) |

Probe requests carry an `X-Synthetic-Check` header. The backend tags them `app_synthetic="true"` and keeps them out of lead capture. The header changes no check and no limit.

Oct 28, 2026, k6 in Docker against the live site: S1 7/7, D2 3/3 (322 days left), A2 8/8 including the decision model. Earlier, Oct 25, 2026 with k6 v2.3.0 in Docker: A2 passed against the live agent (5 of 5 sentences checked, 6 s). A1 failed on `database: unreachable`, which was true at the time. B1 reported `error:400020` against a local production build with the old site key: that is the outage it exists to catch. Against the live site, B1 fails until a frontend with the `data-turnstile-state` marker (this branch) is deployed.

## Environment variables

| Where | Variable | Value |
|---|---|---|
| Railway, backend | `OTEL_EXPORTER_OTLP_ENDPOINT` | the OTLP endpoint Grafana shows, e.g. `https://otlp-gateway-prod-<region>.grafana.net/otlp` |
| Railway, backend | `OTEL_EXPORTER_OTLP_HEADERS` | the header line Grafana shows, `Authorization=Basic%20<token>` (a secret: paste it straight into Railway) |
| Railway, backend | `OTEL_SERVICE_NAME` | optional; defaults to `rayhan-agent` |
| (none needed) | `VITE_FARO_URL` | optional override; the production collector URL for the `chat` Faro app is in `frontend/src/lib/telemetry.js` (public by design) and used on chat.rayhanpatel.com only |

Without them nothing is exported and nothing changes: telemetry is off and the Faro code isn't even loaded.

## What is live (Oct 28–29, 2026)

Stack `savvyanemone2645.grafana.net` (US West). Set up from code and through Grafana's hosted MCP server, not by hand:

- **Telemetry:** the backend exports over OTLP (`OTEL_EXPORTER_OTLP_*` on Railway, token `railway-backend`); the chat page sends Faro data to the Frontend Observability app `chat` (id 6310, CORS https://chat.rayhanpatel.com only, location at continent level); automated browsers (`navigator.webdriver`) send nothing.
- **Checks:** `provision_checks.py` creates the 9 Synthetic Monitoring checks (A1, B1, S1, D1 ×3, D2, T1, T2) and their per-check alerts; re-run it after any change. A2 runs every 6 hours from `.github/workflows/real-answer.yml` (a check in Synthetic Monitoring runs at least hourly, and an answer costs about a cent) and reports `synthetic_check_success{check="A2 real answer"}`; the eval workflow reports `eval_golden_*`. Both need the `GRAFANA_OTLP_HEADERS` repo secret.
- **Alerts:** 14 rules in folder `rayhanpatel.com`, group `chat-agent` (5 min), from `alert-rules.yaml`; contact points `email rayhan` and `phone (IRM)`; policy: everything to email, and failures of A1/B1/S1 plus `severity=page` also to the phone (IRM escalation chain `rayhan`). "Real answer check missing (A2)" stays paused until the secret is set and the first run reports.
- **Dashboards:** `rp-overview` and `rp-chat` in the same folder.

## Setting it up in Grafana Cloud by hand (the original click-path, kept for reference)

1. **Account.** Sign up for Grafana Cloud Free and create a stack (e.g. `rayhanpatel`).
2. **Backend telemetry.** In the stack, open **Connections**, then the **OpenTelemetry (OTLP)** setup. Generate a token, then copy the two environment variables it shows into Railway → the backend service → **Variables**, and deploy. Startup logs then say `✓ Telemetry (OpenTelemetry): exporting over OTLP`.
3. **Frontend errors.** Open **Frontend Observability** and create an app named `chat`, with allowed origin `https://chat.rayhanpatel.com`. Copy its collector URL into Vercel → the frontend project → **Settings → Environment Variables** as `VITE_FARO_URL` (Production), then redeploy.
4. **Checks.** Go to **Testing & synthetics → Synthetics → Create new check**. Create these three:

   | Type | Job name | Target | Frequency | Timeout | Probe locations | Script |
   |---|---|---|---|---|---|---|
   | Browser | `B1 chat page` | `https://chat.rayhanpatel.com/` | 900 s | 60 s | one, in the US | `checks/b1-chat-page.browser.js` |
   | Scripted | `A1 health` | `https://rayhan-ai-resume-production-6239.up.railway.app/api/v1/health/llm` | 300 s | 60 s | one, in the US | `checks/a1-health.k6.js` |
   | Scripted | `A2 real answer` | `https://rayhan-ai-resume-production-6239.up.railway.app/mcp/` | 3600 s | 60 s | one, in the US | `checks/a2-real-answer.k6.js` |

   Paste each script into **Script**, click **Test** once, then **Save**. One location keeps it inside the free tier (an execution is one minute of runtime times the number of locations). That comes to about 2,900 browser and 9,400 API executions a month, against 10,000 and 100,000.
5. **"Chat broken" alerts.** In each check's alerting settings, turn on the per-check **failed executions** alert. Set B1 to 2 failures in 35 minutes, A1 to 2 in 12 and A2 to 2 in 125. Give each the label `severity=page`.
6. **The other rules.** Go to **Alerting → Alert rules → New alert rule**. For each `promql` rule in `alert-rules.yaml`:
   - set the data source to the stack's Prometheus (`grafanacloud-<stack>-prom`) and paste the query;
   - set the threshold as written, the pending period (1 hour where the file gives one, otherwise 5 minutes) and the label `severity`;
   - add the annotation `runbook_url` = `runbook_base` + the rule's runbook file.

   First check the metric names in **Explore**: type `chat_` and pick from the list.
7. **Where alerts go.** In **Alerting → Contact points**, add your email. In **Alerting → Notification policies**, route `severity=page` to Grafana IRM and to email, and `severity=email` to email only.
8. **Phone.** Install the Grafana IRM app, sign in to the stack and allow notifications. Then trigger a test alert from the contact point.
9. **Spend.** This one isn't in Grafana. In Google Cloud, go to **Billing → Budgets & alerts → Create budget**, scope it to project `gen-lang-client-0814894727`, and set alerts at 50%, 90% and 100%. The Grafana rule on tokens per day is only an early warning.

## Game day (after setup)

Break each thing on purpose, one at a time, and confirm the right alert arrives with the right runbook:

- on a Railway staging environment, if there is one, set a wrong `GEMINI_MODEL` (A1 and A2 should fail) and remove `SUPABASE_URL` (Database down);
- point a copy of B1 at a page without the marker (B1);
- open a pull request that sets `gemini_fast_model` to `gemini-3.6-flash`, which retires Nov 19, 2026. The model-lifecycle workflow should fail on it. Close the PR without merging.

Write down how long each took to alert.
