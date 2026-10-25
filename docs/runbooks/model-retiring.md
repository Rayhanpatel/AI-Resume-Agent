# Runbook: Model retiring

**Fires when** the weekly `Model lifecycle` workflow fails: a model in `backend/core/config.py` (answer, fallback or fast) has a scheduled retirement date within 60 days on Google's model-versions page. Severity: email (from GitHub).

## What it means

Google blocks new access to a model a month before its retirement date, and a retired model ID returns 404. In 2026 the bot answered every question with an apology for four months after `gemini-2.0-flash` was retired.

## Check first

1. The workflow's summary names the model, the date and the replacement Google suggests.
2. Google's [model-versions page](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions): confirm the date and look at the replacement's latency and price.

## Fix

1. Try the replacement on the real prompt first. `/health/llm` and the golden eval (`backend/evals/golden.py`) measure it; time to first word matters as much as quality.
2. Change the default in `backend/core/config.py` (and `GEMINI_MODEL` / `GEMINI_FALLBACK_MODEL` / `GEMINI_FAST_MODEL` on Railway if they are set there). Add the model to `MODEL_PRICING` in `services/tracer.py`.
3. Open a pull request: CI, the eval gate and this workflow all run on it.

## Confirm

The workflow passes on the merged change, and A2 passes with the new model named in its result.
