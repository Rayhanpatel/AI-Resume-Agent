# Post-mortem: chat.rayhanpatel.com answered every question with an apology for months

**Status:** resolved Oct 25, 2026. **Author:** Rayhan Patel (draft structure by Claude; facts from the repo, Google Cloud and the live service). **Severity:** total loss of function for every visitor, invisible to monitoring.

## Summary

From roughly June 2026 until Oct 25, 2026, every message sent to the agent returned the fallback text "I apologize, but I'm having trouble responding right now." The health endpoint reported `healthy` the whole time. Nobody noticed because nothing exercised the model, and the only alert path (a health check) looked at configuration flags, not behavior.

## Timeline (dates in PT)

| When | What |
|---|---|
| Jan–Feb 2026 | Agent built on `gemini-2.0-flash` via Vertex AI, model ID hardcoded in five files. Health check returns the `enabled` flags of each service. |
| Apr 22, 2026 | Last deploy before the outage (`95b8353`, data refresh). |
| Jun 1, 2026 | Google retires `gemini-2.0-flash` on Vertex AI ([model versions page](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions)). Every generate call now fails. The code catches the exception and returns the apology; the health check keeps passing. |
| Jun–Oct 2026 | The resume and the portfolio site keep linking to the agent. Unknown number of recruiters hit the broken bot. |
| Oct 20, 2026 | Google retires `gemini-2.5-flash`, `2.5-pro` and `2.5-flash-lite` on Vertex. The Langfuse evaluators (Gemini 2.5 Flash) stop scoring. |
| Oct 25, 2026, ~2:15 a.m. | During a site refresh, a test question returns the apology. Investigation starts. |
| ~2:30 a.m. | Cause 1 found: retired model ID. Cause 2 found: the agent's GCP project is billed to a closed hackathon billing account (`billingEnabled: false`). |
| ~2:40 a.m. | Billing relinked to the active account. Direct Vertex call to `gemini-3.8-flash` succeeds. |
| ~2:48 a.m. | Fix deployed (`314e333`): model IDs become settings, Gemini 3.x on the global endpoint, `systemInstruction` instead of prefilled turns, a `/health/llm` probe that makes a real model call. |
| ~2:50 a.m. | Live chat answers again, but takes 25–60 s per answer on `gemini-3.8-flash`. |
| ~5:30 a.m. | Second fix deployed (`975fbf3`): `gemini-3.5-flash` with automatic fallback to `gemini-3.5-flash-lite`, server-side profile data, daily uptime workflow. First words in 2–7 s. |

Time to detect: about 4.5 months. Time to resolve after detection: about 35 minutes for function, 3 hours for quality.

## Root causes

1. **A dependency with a published end-of-life was hardcoded and never watched.** Google gives at least 45 days' notice; the notice went to a Google Cloud mailbox nobody read, and no "Product updates" contact was configured.
2. **The billing account had closed.** The project was created on a hackathon credit account in January; when that account closed, Vertex calls would have failed even with a live model.
3. **The health check measured configuration, not behavior.** `/health` returned `vertex_ai: true` because credentials were present. It never asked the model anything.
4. **The failure mode was silent by design.** "Graceful degradation" returned a polite apology on every exception, so the user-visible failure looked like a transient hiccup, and the server logs were never read.

## What went well

- The code's error handling kept the service up (no crash loop), which made diagnosis fast once someone looked.
- The fix needed no data migration; model IDs were the only thing tied to the old provider version.

## What we changed

| Change | Where |
|---|---|
| Model IDs are environment settings, not code | `backend/core/config.py` |
| `/api/v1/health/llm` makes a real model call (cached 10 min, failures retried after 60 s) and returns 503 on failure | `backend/api/routes/health.py` |
| Answer path falls back to a second model if the first errors, returns nothing, or stays silent for 10 s; the fallback gets one more patient try | `backend/services/agent.py` |
| Daily GitHub Actions workflow calls `/health/llm` and both sites; GitHub emails the owner on failure | `.github/workflows/uptime.yml` |
| Unit tests on the request shape, the fallback and the probe; a golden eval against the real model | `backend/tests/`, `backend/evals/golden.py` |
| Billing on the active account; a spend cap on the project (owner action) | Google Cloud |
| An inbox the owner reads is registered for Google Cloud "Product updates" (owner action) | Google Cloud Essential Contacts |

## What we would still like to change

- Alert on the *rate* of fallback apologies, not only on the probe (a probe can pass while most real questions fail).
- Pin and re-run the eval on every model version change, in CI.
- Treat "graceful degradation" as a visible state in the UI ("the model is unavailable right now"), not a normal-looking answer.

## Lessons

- A health check must do what the user does. If the user asks the model a question, so must the probe.
- Every retirement date in the stack is a calendar item, with a person on it.
- A dependency's silent failure is the most expensive kind. The code that hides errors from users must not hide them from the operator.

## Follow-up: what broke on Oct 25, 2026, while fixing it

The repair day had its own incidents. They are listed here because the same lessons apply, and some were mine.

| Time (PT) | What happened | Cause | Fix |
|---|---|---|---|
| ~7:20 a.m. – 9:20 p.m. | Every chat start returned "CAPTCHA verification required". | The Turnstile site key in the frontend had one extra character, so browsers never got a token. The old frontend had silently fallen back to a local session; my security change made the token mandatory, which turned a hidden bug into an outage. I verified that forged tokens were refused, but never that a real browser could start a chat. | Correct key (`2fcfc34`); the bot check was switched off for the afternoon and back on at night. A browser probe that watches the widget now exists. |
| unknown – 7:25 p.m. | Quotas silently read zero; history lived only in memory. | The free Supabase project had paused after a quiet week. The startup log still said "Database: Enabled" because it checked configuration, not a query. | Project restored; real database check in startup and `/health/llm` (`8214bb1`); limits fall back to in-memory counts (`acd6240`; the first attempt only covered half the code paths). The app now queries the database every 6 hours. |
| ~7:20 a.m. – 4:50 p.m. | `/mcp` could call the model with no limit, while its docstring said limits applied. | The limits were dependencies of the chat routes, not middleware; the docstring was written from intent, not from a test. | `0fa047e`, with a test that MCP calls hit the limits. |
| until ~11:05 p.m. | Per-IP limits could be bypassed by sending a different `CF-Connecting-IP` header with each request (14 of 14 forged requests got through in a test). | The code trusted headers the caller controls. Railway's edge overwrites `X-Real-IP`, which is the one to use. | Use `X-Real-IP` only; `/mcp` also has its own daily budget so it can't use up the chat's. |
| Oct 20 – 25 | The five Langfuse judges were paused. | Their model, Gemini 2.5 Flash, was retired on Oct 20, the same kind of retirement as the original outage. | Moved to `anthropic/claude-haiku-5.5` through OpenRouter. |

### More lessons

- Test the success path, not only the refusal. A security control that has only been tested by being refused can be refusing everyone.
- "Configured" is not "working". Every dependency check should run a real request.
- A docstring that describes a safeguard needs a test that proves it.
- Never trust a header the caller can set. Find out which one the platform's edge overwrites.
- Free tiers have quiet failure modes (pausing, retirements, scheduled-job expiry). Each one needs an owner and a check.

