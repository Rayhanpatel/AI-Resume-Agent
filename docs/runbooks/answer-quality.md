# Runbook: Answer quality dropping

**Fires when** more than 20% of the sentences in real visitors' answers over 24 hours are stamped unknown or unverified, or carry a number the profile doesn't contain. Severity: email.

## What it means

The agent is saying things the profile can't back. Usual causes: a profile change that removed facts the prompt still leans on, a model change, a broken verifier call, or visitors asking about something new.

## Check first

1. Langfuse → traces from the window, filtered to low hallucination-judge scores: what was asked, which sentences failed.
2. The last deploy and the last profile change (`git log` in both repos).
3. The golden eval's latest run (GitHub Actions → Eval gate): still 16/16?
4. `chat_verification_stamps_total{stamp="unverified"}` alone rising means the verifier call itself is failing (Railway logs).

## Fix

- **Profile gap:** add the missing fact to `data/profile.json` (Portfolio-V2), sync, run the eval, deploy.
- **Model change:** roll back the model setting and measure first.
- **Verifier failing:** see the logged error (quota, model ID, timeout).

## Confirm

The ratio falls back under 20% on the next day's traffic, and the golden eval passes.
