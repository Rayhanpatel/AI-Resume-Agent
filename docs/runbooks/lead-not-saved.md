# Runbook: Lead not saved

**Fires when** the Google Sheet webhook refused a recruiter lead in the last hour (`chat_leads_total{outcome="error"}`): an HTTP error, an Apps Script error page, or no answer. Severity: email.

## What it means

A visitor shared a job post and the lead did not reach the sheet. The chat itself worked, and the visitor's name, company and job details are still in Supabase (`sessions`), so nothing is lost yet.

## Check first

1. Railway logs: `Lead NOT saved:` with the HTTP status or the error.
2. The Apps Script project behind `GOOGLE_SHEET_WEBHOOK`: **Executions** shows the failed run and its error.
3. Was the script redeployed? A new deployment gets a new URL; the old one stops.

## Fix

- **Script error:** fix it in Apps Script, then **Deploy → Manage deployments → edit → new version** (keeps the same URL).
- **New URL:** put it in `GOOGLE_SHEET_WEBHOOK` on Railway.
- **Authorization expired:** open the script, run it once by hand and accept the prompt.
- Copy the missed lead from Supabase (`sessions` row for that time) into the sheet by hand.

## Confirm

The next real lead logs `Lead submitted`, and `chat_leads_total{outcome="ok"}` goes up.
