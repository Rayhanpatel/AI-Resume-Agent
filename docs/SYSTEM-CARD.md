# System card: Rayhan's agent (chat.rayhanpatel.com)

Version 2.7.0, Oct 25, 2026. This card says what the system is, what it can do, what data it touches, how it is evaluated, and what is known to go wrong. It is updated with every release that changes any of these.

## Purpose and scope

- **What it does:** answers questions about Rayhan Patel's professional background for visitors to rayhanpatel.com, mostly recruiters and engineers, and maps his experience to a pasted job post.
- **What it does not do:** it holds no information beyond the public profile, takes no actions on anyone's behalf, sends no messages, and cannot book anything. It declines unrelated requests.
- **Who it speaks for:** it is an AI representative, not Rayhan. The first message says so.

## System

- **Interfaces:** a React chat at chat.rayhanpatel.com (also embedded on rayhanpatel.com); a JSON API at `/api/v1` used only by that frontend.
- **Models:** answers from `gemini-3.5-flash` on Vertex AI (global endpoint), falling back to `gemini-3.5-flash-lite` if the first errors or stays silent for 10 s. Intent classification, job-post extraction and starter questions use `gemini-3.5-flash-lite`.
- **Knowledge:** one file, `backend/core/profile.json` (about 28 KB), the same file that renders rayhanpatel.com. The whole file is placed in the system instruction; there is no retrieval index. The profile carries an `updated` date.
- **Modules (one FastAPI deployment, 10 modules):** intent classifier, agent, Gemini request helper, Vertex auth, tracer (Langfuse), Supabase sessions and events, quotas, job extractor, job parser, prompt generator.
- **Hosting:** backend on Railway, frontend on Vercel behind Cloudflare.

## Data

| Data | Where it goes | Why | Retention |
|---|---|---|---|
| Visitor name and company (typed by the visitor) | Supabase `sessions`; Langfuse trace metadata; Google Sheet (lead capture, with the job URL and extracted fields) | to run the chat and let Rayhan follow up | Supabase rows are kept; deletion on request to rayhanbp@umd.edu |
| Job post text or URL | fetched once, read by a tool-less extraction call, reduced to a schema (title, level, skills, location, summary); the raw text is not stored | tailoring | raw text discarded after extraction |
| Messages | Supabase `sessions.preferences.history` (last 10 messages); Langfuse traces (inputs and outputs) | multi-turn context; quality review | as above |
| IP address | salted hash in event metadata | per-IP quotas | with the events |
| Model calls | Google Vertex AI | generation | Google's data-governance terms; not used for training without consent |
| Operational telemetry: model names, token counts, timings, outcomes, error types; no message text | the OTLP backend (Grafana Cloud), only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set | monitoring and alerts | 14 days (Grafana Cloud Free) |
| Browser errors, page performance, bot-check error codes, a random session ID | Grafana Faro, only when the frontend is built with `VITE_FARO_URL` | monitoring | 14 days (Grafana Cloud Free) |

No cookies beyond the frontend's localStorage copy of the visitor's own session (and, with Faro on, its random session ID in browser storage). No audio. No third-party memory service.

## Safety and security controls

- Cloudflare Turnstile on session creation; a signed session token required on every message; sessions the server didn't create are refused.
- Quotas counted from the events table: 40 messages per session, 80 per IP per day, 15 sessions per IP per day, a global daily token budget of 3M tokens; plus a per-IP per-minute limit. When the database doesn't answer, the quotas fall back to in-memory counts (reset on each deploy) instead of reading zero.
- A spend cap on the Google Cloud project (hard stop) and a model-call health probe with a daily external check.
- Job pages are fetched with a private-network check on the resolved host, no redirects, and size and type limits; their content reaches the persona model only as a bounded schema.
- The system prompt is public (this repository). Its rules are enforced in tests, not by secrecy: a build fails if known-wrong wording (an old prize figure, a retired claim, a phone number, family details) appears in the profile or the rendered prompt.
- HTML is stripped from every input; markdown output is rendered by a library with no raw HTML; `frame-ancestors` restricts embedding to rayhanpatel.com.

## Evaluation

- **Unit tests (77):** request shape, model fallback, health probe, session tokens, quotas (including the in-memory fallback with an unreachable database), origin lock, job-schema boundary, SSRF resolver, banned-claim checks on the profile and prompt, verification, MCP, telemetry (off without its variable, the synthetic tag, outcomes, `gen_ai` spans, Langfuse spans never exported) and the model-lifecycle parser.
- **Golden eval (13 questions, run against the live model):** what the agent must say and must not say on the questions recruiters ask most, including a multi-turn follow-up, an off-topic request, a phone-number request, a question whose honest answer is "the profile doesn't say", and two questions that invite overclaiming. Oct 25, 2026: 13/13 on three consecutive runs after the latest prompt change.
- **Online judges (Langfuse):** five LLM-as-judge evaluators score traces (hallucination, relevance, conciseness, helpfulness, toxicity). Known limitation: the judge model is from the same family as the answer model and has not been calibrated against human labels; both are planned (see Roadmap).
- **Latency, measured Oct 25, 2026 from the live service:** first words in 3.7–7.4 s; local runs against the same model 1–5 s. The intent gate runs before the answer and is the main fixed cost.
- **Cost:** about 16k input tokens per answer (the whole profile) on `gemini-3.5-flash`; cents per hundred conversations on the current credit.

## Known failure modes

- **Inference presented as fact.** The model can describe the profile's architecture decisions in its own words ("routes queries to them") in ways the profile does not literally state. Planned fix: per-claim evidence stamps (checked / inferred / unknown) verified in code.
- **Occasional provider errors.** `gemini-3.5-flash` returned 429s under load on Oct 25; the fallback absorbed them. `gemini-3.5-flash-lite` once returned an empty answer (`MALFORMED_RESPONSE`); the retry absorbed it.
- **Prompt-level rules are not a security boundary.** Adaptive prompt injection defeats prompt-level defenses in the literature; the structural defense here is that untrusted text never reaches the persona model, and the agent has no tools to misuse.
- **No memory across sessions** (by design since 2.5.0).
- **Sessions reset on deploy** until `SESSION_SECRET` is set in the environment.

## Incidents

- **Jun–Oct 2026:** total outage, invisible to monitoring (retired model plus a closed billing account). See `docs/POSTMORTEM-2026-10-chatbot-outage.md`.

## Added Oct 25, 2026 (version 2.6)

- **Verification stamps.** Every sentence is marked checked (verbatim profile quote, confirmed in code), inferred, unknown or n/a; numbers are checked against the profile. First measured run: 52 claims, 83% checked, 0 unknown, 0 unverified numbers, p50 1.2 s. The stamps stream after the answer and are shown under it.
- **MCP interface** at `/mcp` (protocol 2026-07-28, stateless): read-only `get_profile`, `ask` (with stamps), `fit_report`. No tool takes an action.
- **On-device tier.** On Chrome desktop with the built-in model, a visitor can answer on their own machine from the same profile; the browser verifies quotes and numbers and falls back to the cloud, visibly, when it can't. Only timing and counts are reported back, never text.
- **Eval gate workflow** (weekly and on pull requests, once Vertex credentials are added to the repository secrets).

## Added Oct 25, 2026 (version 2.7)

- **Monitoring** (`docs/MONITORING.md`), off until configured:
  - OpenTelemetry traces, metrics and logs over OTLP, with a `gen_ai` span around every model call and no message text;
  - three Grafana Synthetic Monitoring probes: the chat page and bot-check widget, model and database health, and a real verified answer every hour;
  - Grafana Faro for browser errors;
  - six alert rules with runbooks;
  - a weekly check of Google's model-retirement dates (GitHub Actions; no credentials).
- **Synthetic traffic** carries `X-Synthetic-Check`; it is tagged in telemetry and kept out of lead capture. It changes no check or limit.
- **Quota fallback fixed:** messages and sessions are now counted in memory whether or not Supabase is configured, so the fallback has data when the database is configured but unreachable (the state found on Oct 25, 2026).
- **Why these tools:** Grafana Cloud Free covers probes, metrics, logs, traces, frontend errors and phone alerts in one place within its free limits. Langfuse stays for model-quality traces. Checkly's free tier has too few browser runs for a 15-minute check. Sentry would duplicate Faro. Datadog can be added later by changing the OTLP variables.

## Roadmap (what this card does not yet claim)

A judge from a different model family, calibrated against hand labels; the decision-model second opinion (needs an OpenRouter key) and the 200-pair study; multi-turn pressure cases in the golden set; the monitoring above switched on in Grafana Cloud (code is in place); the origin lock (needs the API behind Cloudflare).
