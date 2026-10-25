# Monitoring

Probes, alert rules and the model-lifecycle check for chat.rayhanpatel.com and rayhanpatel.com. Why it looks like this (three silent outages, a few visitors a day, one person on call) is in [`docs/MONITORING.md`](../docs/MONITORING.md).

| File | What it is |
|---|---|
| `checks/b1-chat-page.browser.js` | **B1**, browser check, every 15 min: the site and resume load; the chat form renders; the Turnstile widget has no configuration error |
| `checks/a1-health.k6.js` | **A1**, scripted check, every 5 min: `/health/llm` says the model and the database both answer |
| `checks/a2-real-answer.k6.js` | **A2**, scripted check, every 60 min: the agent answers a fixed question correctly and with verification stamps, in under 15 s |
| `alert-rules.yaml` | the six alert rules: query, condition, severity, runbook |
| `model_lifecycle.py` | the weekly check that no configured model retires within 60 days (run by `.github/workflows/model-lifecycle.yml`) |

Probe requests carry an `X-Synthetic-Check` header. The backend tags them `app_synthetic="true"` and keeps them out of lead capture. The header changes no check and no limit.

Tested on Oct 25, 2026 with k6 v2.3.0 in Docker: A2 passed against the live agent (5 of 5 sentences checked, 6 s). A1 failed on `database: unreachable`, which was true at the time. B1 reported `error:400020` against a local production build with the old site key: that is the outage it exists to catch. Against the live site, B1 fails until a frontend with the `data-turnstile-state` marker (this branch) is deployed.

## Environment variables

| Where | Variable | Value |
|---|---|---|
| Railway, backend | `OTEL_EXPORTER_OTLP_ENDPOINT` | the OTLP endpoint Grafana shows, e.g. `https://otlp-gateway-prod-<region>.grafana.net/otlp` |
| Railway, backend | `OTEL_EXPORTER_OTLP_HEADERS` | the header line Grafana shows, `Authorization=Basic%20<token>` (a secret: paste it straight into Railway) |
| Railway, backend | `OTEL_SERVICE_NAME` | optional; defaults to `rayhan-agent` |
| Vercel, frontend (Production) | `VITE_FARO_URL` | the Faro collector URL (public by design); takes effect on the next build |

Without them nothing is exported and nothing changes: telemetry is off and the Faro code isn't even loaded.

## Setting it up in Grafana Cloud (about 20 minutes)

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
