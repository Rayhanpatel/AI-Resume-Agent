# Monitoring

Three outages in 2026 went unnoticed because nothing watched the visitor's path:
- a retired model ID, with a closed billing account, ran for about four months;
- an invalid bot-check site key blocked every chat for hours;
- the Supabase project paused, so the quotas silently read zero.

Each was visible in a log line or a refused request. This is the setup that watches for them.

## Design

1. **Watch the journey from outside, on a schedule.** The agent gets a few visitors a day, so real-traffic error rates are noise. Three Grafana Synthetic Monitoring probes give a steady signal:
   - **B1** (browser, 15 min): the page, the form and the bot-check widget. It never solves the challenge.
   - **A1** (5 min): `/health/llm`, which makes a real model call and a real database query.
   - **A2** (60 min): a real question through the MCP `ask` tool. It checks the answer's content, its stamps and its latency.
2. **Instrument the inside once, with OpenTelemetry** (`backend/core/telemetry.py`). Traces, metrics and logs go over OTLP, and the destination is set by the standard `OTEL_*` variables. With `OTEL_EXPORTER_OTLP_ENDPOINT` unset it does nothing.
   - The providers are private to the module, and Langfuse gets a private provider of its own (`services/tracer.py`), so neither pipeline sees the other's spans.
   - Message text is never exported. Langfuse spans are filtered out of the OTLP export.
   - `gen_ai.*` names follow the GenAI semantic conventions as shipped in `opentelemetry-semantic-conventions` 0.66b1. They are still "Development" status upstream.
3. **Watch the dependencies that failed:**
   - a weekly check of Google's model-retirement dates (`monitoring/model_lifecycle.py`);
   - `chat.database.up`;
   - the bot-check widget state, in B1 and in Faro;
   - a Google Cloud budget.
4. **Alert on what a visitor would feel,** at two levels: a page (phone and email) when the chat is broken or money is at risk, and email when something is degrading. Every alert has a runbook in `docs/runbooks/`.

Probe traffic carries `X-Synthetic-Check`. It is tagged `app.synthetic` and kept out of lead capture. It is a tag, not a bypass.

## What the backend reports

| Metric | Labels | Used by |
|---|---|---|
| `chat.starts` | `outcome` (ok, captcha_failed, quota, invalid, error) | dashboards; a bot-check outage shows as captcha_failed with no ok |
| `chat.answers` | `outcome` (ok, apology, error), `gen_ai.response.model`, `app.channel` (web, mcp) | Degrading answers |
| `chat.model_fallbacks` | `app.from_model`, `app.to_model` | Degrading answers |
| `chat.time_to_first_word` (s) | `app.synthetic` | Slow |
| `chat.verification.stamps` | `stamp` (checked, inferred, unknown, unverified, numbers_unverified) | quality dashboard |
| `chat.quota.rejections` | `scope` | Spend |
| `chat.database.up` | | Database down |
| `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, `gen_ai.client.operation.time_to_first_chunk` | model, `app.purpose` (answer, intent, verify, job_parse, prompts, decision), `error.type` | Spend, Slow, Degrading answers |

Every metric also carries `app.synthetic` and `app.channel`. Traces have a `gen_ai` client span around each model call, with the model, the token counts and the attempt number (attempt 2+ is the fallback). Log records carry the trace ID of their request. Frontend errors and bot-check failures go to Grafana Faro when `VITE_FARO_URL` is set.

## Langfuse (v4 data model, Python SDK 5)

- One chat turn is one trace: a root observation (`stream_chat` or `chat_message`) holds the visitor's message as input and the answer as output; model calls are child generations (`agent_response_stream`, `agent_response`).
- Session ID, user ID (`visitor-` plus the salted IP hash, never the typed name), tags (`web`, `stream` or `sync`, `synthetic` for probes), the profile version and the environment are propagated onto every observation, so session cost includes the generations.
- Session starts and the MCP channel are not traced in Langfuse.
- **Evaluator targets (candidates from the code, not verified against the project):** all five judges move to observation-level rules on the root observation, filtered to names `stream_chat`/`chat_message`, without the `synthetic` tag, and (for helpfulness) metadata `intent = job_related`. Variables: `input` and `output` of that observation. The hallucination judge also needs the public profile as its reference: the root carries only the profile version, so the profile goes into the judge's template.

## Where things are

- Setup steps, environment variables, the probes and the game day: [`monitoring/README.md`](../monitoring/README.md)
- Alert rules: [`monitoring/alert-rules.yaml`](../monitoring/alert-rules.yaml)
- Runbooks: [`docs/runbooks/`](runbooks/)
- Why these tools (Grafana Cloud Free plus Langfuse, not Checkly, Sentry or Datadog): the monitoring plan of Oct 25, 2026, summarised in the system card
