# Monitoring

Probes, alert rules and the model-lifecycle check for chat.rayhanpatel.com and rayhanpatel.com. Why it looks like this (three silent outages, a few visitors a day, one person on call) is in [`docs/MONITORING.md`](../docs/MONITORING.md).

| File | What it is |
|---|---|
| `checks/b1-chat-page.browser.js` | **B1**, browser check, every 15 min: the site and resume load; the chat form renders; the Turnstile widget has no configuration error |
| `checks/a1-health.k6.js` | **A1**, scripted check, every 5 min: `/health/llm` says the model and the database both answer |
| `checks/a2-real-answer.k6.js` | **A2**, run by `.github/workflows/real-answer.yml` every 6 hours (not a Synthetic Monitoring check: those run at least hourly, and each answer costs about a cent under the $25/month spend cap): the agent answers a fixed question correctly and with verification stamps, the decision model answers, in under 15 s |
| `checks/s1-site.k6.js` | **S1**, scripted check, every 15 min: rayhanpatel.com names him and has the chat launcher; the resume is a real PDF; `profile.json` is valid |
| `checks/d1-dns.json` | **D1**, DNS checks, hourly: MX and DMARC for rayhanpatel.com (mail to the domain), the `chat` CNAME to Vercel |
| `checks/d2-domain-expiry.k6.js` | **D2**, scripted check, daily: rayhanpatel.com has at least 45 days left at the .com registry |
| `provision_checks.py` | creates or updates the Synthetic Monitoring checks and their per-check alerts (token from `~/.config/grafana/sm-token`) |
| `push_metric.py` | sends one gauge to Grafana over OTLP; used by the A2 and eval workflows |
| `alert-rules.yaml` | the alert rules: query, condition, severity, runbook |
| `DASHBOARDS.md` | the two Grafana dashboards, panel by panel, for rebuilding them |
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
- **Alerts:** 17 rules in folder `rayhanpatel.com`, group `chat-agent` (5 min), from `alert-rules.yaml`; contact points `email rayhan` and `phone (IRM)`; policy: everything to email, and failures of A1/B1/S1 plus `severity=page` also to the phone (IRM escalation chain `rayhan`). A2's two rules are email-only with 25-hour windows (Oct 30). Tested Oct 30: a `severity=page` test rule reached the phone in 32 seconds. "Real answer check missing (A2)" stays paused until the secret is set and the first run reports.
- **Dashboards:** `rp-overview` and `rp-chat` in the same folder.

## Rebuilding it from scratch

1. **Stack and telemetry.** Create a Grafana Cloud stack. **Connections → Add new connection → OpenTelemetry (OTLP) → View connection details**: create a token and put `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL` and `OTEL_EXPORTER_OTLP_HEADERS` (with `Basic%20`) on Railway.
2. **Faro.** Create a Frontend Observability web app `chat` (CORS origin `https://chat.rayhanpatel.com`, geolocation at continent level) and put its collector URL in `PRODUCTION_FARO_URL` in `frontend/src/lib/telemetry.js`.
3. **Checks.** **Testing & synthetics → Synthetics → Config → Generate access token**, save it to `~/.config/grafana/sm-token` (`chmod 600`), then `python3 monitoring/provision_checks.py`. Set `SM_API_URL` if the stack is not in US West, and check the probe IDs at the top of the script against `GET /api/v1/probe/list`.
4. **CI results.** Add the repo secret `GRAFANA_OTLP_HEADERS` (same form as the Railway header) so A2 and the eval report to Grafana. Don't name an OTLP attribute `job` or `instance`: the gateway overwrites them.
5. **Delivery.** In Grafana IRM: an escalation chain notifying the owner and a "Grafana Alerting" integration. In Alerting: contact points `email rayhan` and `phone (IRM)` (type oncall, the integration URL); policy: default to email; A1/B1/S1 failures (`namespace=synthetic_monitoring`, `alertname=~ProbeFailedExecutionsTooHigh.*`) and `severity=page` also to the phone, `continue: true`. On the phone: the Grafana IRM app, linked from IRM → Users → the profile → Mobile app connection, with mobile push first in the default notifications.
6. **Rules.** Create the rules in `alert-rules.yaml` (folder `rayhanpatel.com`, group `chat-agent`, 5-minute interval, no-data and error states OK). Where a query matches with the value 0 (`== 0`, a failed check's 0, a passed count), use the threshold `> -1`, not `> 0`. Keep "Real answer check missing (A2)" paused until the first A2 run has reported.
7. **Dashboards.** Rebuild `rp-overview` and `rp-chat` from `DASHBOARDS.md` (every panel and its query).
8. **Spend** stays in Google Cloud: a budget on project `gen-lang-client-0814894727` with alerts at 50/90/100% and the $25 hard cap ($10 until Oct 30).

## Game day (after setup)

Break each thing on purpose, one at a time, and confirm the right alert arrives with the right runbook:

- on a Railway staging environment, if there is one, set a wrong `GEMINI_MODEL` (A1 and A2 should fail) and remove `SUPABASE_URL` (Database down);
- point a copy of B1 at a page without the marker (B1);
- open a pull request that sets `gemini_fast_model` to `gemini-3.6-flash`, which retires Nov 19, 2026. The model-lifecycle workflow should fail on it. Close the PR without merging.

Write down how long each took to alert.
