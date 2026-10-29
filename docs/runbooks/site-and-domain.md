# Runbook: Site, DNS, domain and certificate

**Fires when** the S1 check (rayhanpatel.com and the resume PDF) fails twice, a D1 DNS check finds a record missing or changed, the D2 check finds the domain expiring within 45 days, or a certificate expires within 21 days (page within 7). Severity: email; page for S1 and for a certificate under 7 days.

## Check first

- **S1:** Cloudflare → Workers & Pages → the site's Worker: latest deployment failed? The Portfolio-V2 build (`scripts/build.py`) fails on banned wording, which blocks a deploy, not the live site.
- **D1:** Cloudflare → DNS → records for `rayhanpatel.com`: MX and DMARC (mail to the domain), the `chat` CNAME to Vercel. Cloudflare's audit log shows who changed what.
- **D2:** the registrar's console: auto-renew on, payment method valid.
- **TLS:** rayhanpatel.com uses Cloudflare's certificate; chat.rayhanpatel.com uses Vercel's (it needs the CNAME in place to renew); the backend uses Railway's.

## Fix

- Restore the record from the audit log, or redeploy the last good site build.
- Renew the domain by hand if auto-renew failed.
- A certificate that won't renew: check the DNS record its provider validates against.

## Confirm

The checks pass on their next run.
