"""
Send one gauge value to Grafana Cloud over OTLP (HTTP/JSON). Used by GitHub Actions to report
results that don't come from the backend: the A2 real-answer check and the golden eval.

    GRAFANA_OTLP_HEADERS="Authorization=Basic%20..." python3 monitoring/push_metric.py \
        synthetic.check.success 1 job="A2 real answer"

The header value has the same form as OTEL_EXPORTER_OTLP_HEADERS (which is also accepted).
Without either variable it prints a note and exits 0, so a missing secret never fails a run.
Standard library only.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ENDPOINT = os.getenv("GRAFANA_OTLP_ENDPOINT", "https://otlp-gateway-prod-us-west-0.grafana.net/otlp")


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    name, value, pairs = argv[0], float(argv[1]), argv[2:]
    header = os.getenv("GRAFANA_OTLP_HEADERS") or os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
    if not header:
        print(f"no GRAFANA_OTLP_HEADERS: not sending {name}={value}")
        return 0
    key, _, raw = header.partition("=")
    attrs = [{"key": k, "value": {"stringValue": v.strip('"')}} for k, _, v in (p.partition("=") for p in pairs)]
    body = {"resourceMetrics": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "rayhan-agent-ci"}}]},
        "scopeMetrics": [{"scope": {"name": "github-actions"}, "metrics": [{
            "name": name,
            "gauge": {"dataPoints": [{"asDouble": value, "timeUnixNano": str(time.time_ns()), "attributes": attrs}]},
        }]}],
    }]}
    req = urllib.request.Request(f"{ENDPOINT}/v1/metrics", data=json.dumps(body).encode(), method="POST",
                                 headers={key.strip(): urllib.parse.unquote(raw.strip()), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"sent {name}={value} {pairs}: HTTP {r.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
