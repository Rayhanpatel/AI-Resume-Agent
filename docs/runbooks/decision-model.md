# Runbook: Decision model failing

**Fires when** calls to the second-opinion decision model (`app.purpose="decision"`, TypeSafe's Jev through OpenRouter) end in errors, or when the A2 probe finds no sentence with a gate probability. Severity: email.

## What it means

Answers still go out, and each sentence is still checked against the profile, but the second opinion that demotes weak "checked" stamps to "inferred" is missing. Stamps may be slightly too confident until it is back.

## Check first

1. Railway logs around the failures: the OpenRouter status code and message.
2. OpenRouter → **Activity** for the backend's key: 401 (key revoked), 402 (credit or key limit reached), 404 (model ID gone), 429 (rate limit).
3. The pinned model ID in `backend/core/config.py` (`decision_model`) still exists in OpenRouter's Decisions API.

## Fix

- **Limit reached:** raise the key's monthly limit or wait for the reset.
- **Key revoked:** create a new key and set `OPENROUTER_API_KEY` on Railway.
- **Model ID gone:** pin the newest version of the same model, run the golden eval, then deploy.
- To switch the check off on purpose, remove `OPENROUTER_API_KEY`; A2 will then fail until it is back, by design.

## Confirm

A2 passes, and an MCP `ask` shows `gate_model` and a `gate_probability` on checked sentences.
