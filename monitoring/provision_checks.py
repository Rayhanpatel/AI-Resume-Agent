"""
Create or update the Grafana Synthetic Monitoring checks and their per-check alerts.

The checks are defined here and in monitoring/checks/; this script makes the stack match,
so it is safe to run again after any change (it matches checks by job name). Standard
library only. The Synthetic Monitoring access token is read from a file, never printed:

    SM_TOKEN_FILE=~/.config/grafana/sm-token python3 monitoring/provision_checks.py [--dry-run]

Execution budget (Grafana Cloud Free: 100k API and 10k browser executions a month): about
26k API and 2.9k browser executions with the frequencies below.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = os.getenv("SM_API_URL", "https://synthetic-monitoring-api-us-west-0.grafana.net/api/v1")
CHECKS_DIR = Path(__file__).resolve().parent / "checks"
BACKEND = "https://rayhan-ai-resume-production-6239.up.railway.app/api/v1"
RUNBOOKS = "https://github.com/Rayhanpatel/AI-Resume-Agent/blob/main/docs/runbooks"

OHIO, VIRGINIA, OREGON = 7, 13, 15
MIN = 60_000
HOUR = 60 * MIN


def script(name: str) -> str:
    return base64.b64encode((CHECKS_DIR / name).read_bytes()).decode()


def failures(threshold: int, period: str, runbook: str) -> dict:
    return {"name": "ProbeFailedExecutionsTooHigh", "threshold": threshold, "period": period,
            "runbookUrl": f"{RUNBOOKS}/{runbook}"}


def tls(days: int = 14) -> dict:
    return {"name": "TLSTargetCertificateCloseToExpiring", "threshold": days,
            "runbookUrl": f"{RUNBOOKS}/site-and-domain.md"}


def labels(severity: str) -> list:
    return [{"name": "severity", "value": severity}, {"name": "service", "value": "rayhanpatel"}]


def dns(job: str, target: str, record: str, expect: str) -> dict:
    return {"job": job, "target": target, "frequency": HOUR, "timeout": 10_000, "probes": [OHIO],
            "labels": labels("email"),
            "settings": {"dns": {"recordType": record, "server": "1.1.1.1", "port": 53, "protocol": "UDP",
                                 "ipVersion": "V4", "validRCode": ["NOERROR"],
                                 "validateAnswerRRS": {"failIfMatchesRegexp": [], "failIfNotMatchesRegexp": [expect]}}},
            "alerts": [failures(2, "3h", "site-and-domain.md")]}


def tls_only(job: str, target: str, probe: int) -> dict:
    return {"job": job, "target": target, "frequency": HOUR, "timeout": 15_000, "probes": [probe],
            "labels": labels("email"),
            "settings": {"http": {"method": "GET", "ipVersion": "V4", "validStatusCodes": [200],
                                  "headers": ["X-Synthetic-Check: TLS"]}},
            "alerts": [tls()]}


CHECKS = [
    {"job": "A1 health", "target": f"{BACKEND}/health/llm", "frequency": 5 * MIN, "timeout": 30_000,
     "probes": [VIRGINIA, OREGON], "labels": labels("page"),
     "settings": {"http": {"method": "GET", "ipVersion": "V4", "validStatusCodes": [200],
                           "headers": ["X-Synthetic-Check: A1"],
                           "failIfBodyNotMatchesRegexp": ['"status":\\s*"ok"', '"database":\\s*"ok"']}},
     "alerts": [failures(3, "15m", "chat-broken.md"), tls()]},
    # A real answer costs about a cent (Vertex, under the $10/month cap): every 6 hours, not hourly
    {"job": "A2 real answer", "target": f"{BACKEND.rsplit('/api/v1', 1)[0]}/mcp/", "frequency": 6 * HOUR,
     "timeout": 60_000, "probes": [VIRGINIA], "labels": labels("page"),
     "settings": {"scripted": {"script": script("a2-real-answer.k6.js")}},
     "alerts": [failures(2, "3h", "chat-broken.md")]},
    {"job": "B1 chat page", "target": "https://chat.rayhanpatel.com/", "frequency": 15 * MIN, "timeout": 60_000,
     "probes": [OREGON], "labels": labels("page"),
     "settings": {"browser": {"script": script("b1-chat-page.browser.js")}},
     "alerts": [failures(2, "35m", "chat-broken.md")]},
    {"job": "S1 site", "target": "https://rayhanpatel.com/", "frequency": 15 * MIN, "timeout": 30_000,
     "probes": [VIRGINIA], "labels": labels("page"),
     "settings": {"scripted": {"script": script("s1-site.k6.js")}},
     "alerts": [failures(2, "35m", "site-and-domain.md")]},
    dns("D1 MX rayhanpatel.com", "rayhanpatel.com", "MX", "route[123]\\.mx\\.cloudflare\\.net"),
    dns("D1 DMARC rayhanpatel.com", "_dmarc.rayhanpatel.com", "TXT", "v=DMARC1"),
    dns("D1 chat CNAME", "chat.rayhanpatel.com", "CNAME", "vercel-dns"),
    {"job": "D2 domain expiry", "target": "https://rdap.verisign.com/com/v1/domain/rayhanpatel.com",
     "frequency": HOUR, "timeout": 30_000, "probes": [OHIO], "labels": labels("email"),
     "settings": {"scripted": {"script": script("d2-domain-expiry.k6.js")}},
     "alerts": [failures(2, "3h", "site-and-domain.md")]},
    tls_only("T1 TLS chat", "https://chat.rayhanpatel.com/", OREGON),
    tls_only("T2 TLS site", "https://rayhanpatel.com/", VIRGINIA),
]


def call(method: str, path: str, token: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:500]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    token = Path(os.path.expanduser(os.getenv("SM_TOKEN_FILE", "~/.config/grafana/sm-token"))).read_text().strip()

    status, existing = call("GET", "/check/list", token)
    if status != 200:
        print(f"listing checks failed: HTTP {status} {existing}")
        return 1
    by_job = {c["job"]: c for c in existing}
    failed = False
    for spec in CHECKS:
        alerts = spec.pop("alerts")
        body = {"enabled": True, "basicMetricsOnly": True, "alertSensitivity": "none", **spec}
        old = by_job.get(spec["job"])
        if args.dry_run:
            print(f"would {'update' if old else 'create'}: {spec['job']} ({', '.join(body['settings'])}, every "
                  f"{body['frequency'] // MIN} min, probes {body['probes']}, alerts {[a['name'] for a in alerts]})")
            continue
        if old:
            body.update(id=old["id"], tenantId=old["tenantId"])
            status, result = call("POST", "/check/update", token, body)
        else:
            status, result = call("POST", "/check/add", token, body)
        if status != 200:
            print(f"FAILED {spec['job']}: HTTP {status} {result}")
            failed = True
            continue
        status2, result2 = call("PUT", f"/check/{result['id']}/alerts", token, {"alerts": alerts})
        alert_note = "alerts ok" if status2 == 200 else f"alerts FAILED: HTTP {status2} {result2}"
        failed = failed or status2 != 200
        print(f"{'updated' if old else 'created'}: {spec['job']} (id {result['id']}), {alert_note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
