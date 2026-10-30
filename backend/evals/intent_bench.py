"""
Intent gate benchmark: the LLM classifier (Gemini Flash-Lite) against a decision model (Jev).

Runs both gates on evals/intent_set.jsonl (labelled questions, a fixed dev/test split), interleaved
item by item so time of day and network affect all gates alike. Three gates: the LLM classifier,
Jev on the message only (version 1) and Jev with names from the profile (version 2). Jev's threshold is chosen on the dev
half only, by a rule fixed before the run (minimise 3 x wrongly refused + 1 x wrongly allowed,
ties to the lower threshold). Version 1 is judged on the test half (clear-cut items); version 2 was designed after
seeing version 1's test mistakes, so it is judged on evals/intent_holdout.jsonl, committed before version 2 existed.
Borderline items are reported separately as agreement with the written policy. It also times the
answer model's first token on on-topic test items, to compare running the gate before the answer
with running it alongside.

    TOKEN=$(gcloud auth print-access-token) GOOGLE_CLOUD_PROJECT=gen-lang-client-0814894727 \\
    OPENROUTER_API_KEY=... python evals/intent_bench.py --repeats 2 --out evals/results

Costs a few cents. Prices: Jev from the API's own usage.cost; Flash-Lite at $0.30 / $2.50 per 1M
input / output tokens (third-party trackers, Oct 2026; output includes thinking tokens).
"""
import argparse
import asyncio
import json
import math
import os
import random
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GOOGLE_API_KEY", "unused")

from core.config import get_settings  # noqa: E402
from services.agent import AgentService  # noqa: E402
from services.intent import IntentClassifier, JevIntentGate, _load_profile_names  # noqa: E402
from services.vertex_auth import VertexAuthService  # noqa: E402

HERE = Path(__file__).resolve().parent
FLASH_LITE_PRICE = (0.30, 2.50)  # USD per 1M input, output tokens
THRESHOLDS = [round(0.05 * i, 2) for i in range(1, 20)]


class _GcloudAuth(VertexAuthService):
    async def get_headers(self):
        return {"Authorization": f"Bearer {os.environ['TOKEN']}", "Content-Type": "application/json"}


class _UsageCapture(httpx.AsyncClient):
    """Keeps the last response's token usage so the benchmark can price each call"""
    last = None

    async def post(self, *args, **kwargs):
        response = await super().post(*args, **kwargs)
        try:
            body = response.json()
            _UsageCapture.last = body.get("usageMetadata") or body.get("usage")
        except Exception:
            _UsageCapture.last = None
        return response


async def run_gemini(classifier, text):
    _UsageCapture.last = None
    start = time.perf_counter()
    result = await classifier.classify(text)
    ms = (time.perf_counter() - start) * 1000
    error = "reasoning" not in result or result.get("reasoning") == "Classification timed out"
    usage = _UsageCapture.last or {}
    tokens_in = usage.get("promptTokenCount") or 0
    tokens_out = (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
    on = result.get("intent") == "job_related"
    conf = float(result.get("confidence") or 0.5)
    return {"ms": ms, "error": error, "pred_on": on, "p_on": conf if on else 1 - conf,
            "cost": (tokens_in * FLASH_LITE_PRICE[0] + tokens_out * FLASH_LITE_PRICE[1]) / 1e6,
            "tokens_in": tokens_in, "tokens_out": tokens_out}


async def run_jev(gate, text):
    _UsageCapture.last = None
    start = time.perf_counter()
    try:
        p = await gate.probability(text)
        error = False
    except Exception as e:
        p, error = None, f"{type(e).__name__}: {e}"[:120]
    ms = (time.perf_counter() - start) * 1000
    usage = _UsageCapture.last or {}
    return {"ms": ms, "error": error, "p_on": p, "cost": float(usage.get("cost") or 0),
            "tokens_in": usage.get("input_tokens") or 0, "tokens_out": usage.get("output_tokens") or 0}


async def first_token_ms(agent, text):
    start = time.perf_counter()
    stream = agent.respond_stream(session_id="bench", query=text, user_name="a recruiter", history=[], stats={})
    try:
        async for _ in stream:
            return (time.perf_counter() - start) * 1000
    finally:
        await stream.aclose()
    return None


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0, c - h), min(1, c + h)


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def calibration(pairs, bins=10):
    """Brier score and expected calibration error of (p_on, is_on) pairs"""
    pairs = [(p, y) for p, y in pairs if p is not None]
    if not pairs:
        return float("nan"), float("nan")
    brier = statistics.fmean((p - y) ** 2 for p, y in pairs)
    ece = 0.0
    for b in range(bins):
        inb = [(p, y) for p, y in pairs if b / bins <= p < (b + 1) / bins or (b == bins - 1 and p == 1.0)]
        if inb:
            ece += len(inb) / len(pairs) * abs(statistics.fmean(p for p, _ in inb) - statistics.fmean(y for _, y in inb))
    return brier, ece


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p-value from the discordant counts"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def choose_threshold(dev):
    """Fixed rule: minimise 3 x wrongly refused + 1 x wrongly allowed on dev clear items; ties to the lower threshold"""
    best = None
    for t in THRESHOLDS:
        refused = sum(1 for r in dev if r["label"] == "on" and r["jev_p"] < t)
        allowed = sum(1 for r in dev if r["label"] == "off" and r["jev_p"] >= t)
        loss = 3 * refused + allowed
        if best is None or loss < best[0]:
            best = (loss, t, refused, allowed)
    return best[1], {"loss": best[0], "refused": best[2], "allowed": best[3]}


def svg_chart(summary, path):
    """Two-panel bar chart (no plotting dependency): gate latency, and mistakes on the held-out set"""
    arms = [("LLM classifier (Flash-Lite)", "gemini", "#6b7280"), ("Jev, message only", "jev", "#93c5fd"),
            ("Jev + profile names", "jev_names", "#1d6fb8")]
    w, h = 820, 256
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="system-ui, sans-serif" font-size="12">',
           '<rect width="100%" height="100%" fill="#ffffff"/>',
           '<text x="20" y="22" font-size="14" font-weight="600" fill="#111827">Intent gate: LLM classifier vs decision model</text>']

    def panel(x0, title, series, unit, vmax):
        out.append(f'<text x="{x0}" y="50" font-weight="600" fill="#111827">{title}</text>')
        y = 64
        for label, values in series:
            out.append(f'<text x="{x0}" y="{y + 12}" fill="#374151">{label}</text>')
            y += 18
            for _, key, color in arms:
                v = values[key]
                bw = 0 if not vmax else max(1, 230 * v / vmax)
                txt = f"{v:.0f}{unit}" if unit == " ms" else f"{v:.1f}{unit}"
                out.append(f'<rect x="{x0}" y="{y}" width="{bw:.1f}" height="13" fill="{color}"/>'
                           f'<text x="{x0 + bw + 6:.1f}" y="{y + 11}" fill="#111827">{txt}</text>')
                y += 16
            y += 10

    lat = summary["latency_ms"]
    panel(20, "Gate latency (all calls)", [("median", {k: lat[k]["p50"] for _, k, _ in arms}),
                                         ("95th percentile", {k: lat[k]["p95"] for _, k, _ in arms})], " ms",
          max(lat[k]["p95"] for _, k, _ in arms))
    t = summary["scores"]["holdout"]
    panel(420, "Mistakes on the held-out set", [
        ("real questions refused", {k: 100 * t[k]["false_refusal"][0] for _, k, _ in arms}),
        ("off-topic or attacks let through", {k: 100 * t[k]["missed_off"][0] for _, k, _ in arms})], " %", 100)
    for i, (name, _, color) in enumerate(arms):
        out.append(f'<rect x="{20 + i * 260}" y="{h - 24}" width="12" height="12" fill="{color}"/>'
                   f'<text x="{38 + i * 260}" y="{h - 14}" fill="#374151">{name}</text>')
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


ARMS = ("gemini", "jev", "jev_names")


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--first-token-items", type=int, default=24)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--seed", type=int, default=20261030)
    ap.add_argument("--limit", type=int, default=0, help="smoke test: only the first N items of each set")
    args = ap.parse_args(argv)

    settings = get_settings()
    load = lambda name: [json.loads(x) for x in (HERE / name).read_text(encoding="utf-8").splitlines() if x.strip()]
    items = load("intent_set.jsonl") + load("intent_holdout.jsonl")
    if args.limit:
        items = [i for sp in ("dev", "test", "holdout") for i in [x for x in items if x["split"] == sp and not x["borderline"]][:args.limit]]
    rng = random.Random(args.seed)
    auth = _GcloudAuth(None, os.environ["GOOGLE_CLOUD_PROJECT"], settings.gemini_location)
    auth.enabled = True
    calls = []
    async with _UsageCapture(http2=True, timeout=60) as client:
        gemini = IntentClassifier("unused", client, auth, model=settings.gemini_fast_model, thinking_level=settings.gemini_thinking_level)
        key = os.environ["OPENROUTER_API_KEY"]
        gates = {"gemini": (run_gemini, gemini),
                 "jev": (run_jev, JevIntentGate(client, key, settings.decision_model, threshold=0.5, timeout=10.0)),
                 "jev_names": (run_jev, JevIntentGate(client, key, settings.decision_model, threshold=0.5, timeout=10.0,
                                                      names=_load_profile_names()))}
        await gemini.classify("warm-up: what does Rayhan do?")
        for _, (fn, gate) in gates.items():
            await fn(gate, "warm-up: what does Rayhan do?")
        # First, so a failure here shows in seconds, not after the gate runs
        agent = AgentService("unused", None, client, auth, model=settings.gemini_model,
                             thinking_level=settings.gemini_thinking_level, fallback_model=settings.gemini_fallback_model,
                             first_token_timeout=settings.gemini_first_token_timeout)
        on_test = [i for i in items if i["label"] == "on" and i["split"] == "test" and not i["borderline"]] or [i for i in items if i["label"] == "on"]
        rng.shuffle(on_test)
        first_tokens = {}
        for item in on_test[:args.first_token_items]:
            first_tokens[item["id"]] = await first_token_ms(agent, item["text"])
        print(f"answer first token measured on {sum(v is not None for v in first_tokens.values())} items", flush=True)
        for rep in range(args.repeats):
            order = items[:]
            rng.shuffle(order)
            for n, item in enumerate(order, 1):
                arms = list(gates.items())
                rng.shuffle(arms)
                for arm, (fn, gate) in arms:
                    r = await fn(gate, item["text"])
                    calls.append({"id": item["id"], "arm": arm, "repeat": rep, **r})
                if n % 50 == 0:
                    print(f"repeat {rep + 1}: {n}/{len(order)}", flush=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    (out / f"intent_bench_{stamp}_raw.json").write_text(
        json.dumps({"calls": calls, "first_tokens": first_tokens}, indent=1), encoding="utf-8")  # saved before any analysis

    by = {}
    for c in calls:
        by.setdefault((c["id"], c["arm"]), []).append(c)
    rows = []
    for item in items:
        row = dict(item)
        for arm in ARMS:
            ok = [c for c in by.get((item["id"], arm), []) if not c["error"]]
            if arm == "gemini":
                row["gemini_on"] = (sum(c["pred_on"] for c in ok) / len(ok) >= 0.5) if ok else None
                row["gemini_stable"] = len({c["pred_on"] for c in ok}) <= 1
            row[f"{arm}_p"] = statistics.fmean(c["p_on"] for c in ok) if ok else None
            if arm != "gemini":
                row[f"{arm}_spread"] = (max(c["p_on"] for c in ok) - min(c["p_on"] for c in ok)) if ok else None
        rows.append(row)

    thresholds = {}
    for arm in ("jev", "jev_names"):
        dev = [{"label": r["label"], "jev_p": r[f"{arm}_p"]} for r in rows if r["split"] == "dev" and not r["borderline"] and r[f"{arm}_p"] is not None]
        thresholds[arm] = choose_threshold(dev)
        for r in rows:
            r[f"{arm}_on"] = None if r[f"{arm}_p"] is None else r[f"{arm}_p"] >= thresholds[arm][0]

    def scores(subset, arm):
        on = [r for r in subset if r["label"] == "on" and r[f"{arm}_on"] is not None]
        off = [r for r in subset if r["label"] == "off" and r[f"{arm}_on"] is not None]
        adv = [r for r in off if r["category"].startswith("adversarial")]
        refused = sum(1 for r in on if not r[f"{arm}_on"])
        allowed = sum(1 for r in off if r[f"{arm}_on"])
        correct = len(on) - refused + len(off) - allowed
        return {"n": len(on) + len(off), "accuracy": wilson(correct, len(on) + len(off)),
                "false_refusal": wilson(refused, len(on)), "missed_off": wilson(allowed, len(off)),
                "missed_adversarial": wilson(sum(1 for r in adv if r[f"{arm}_on"]), len(adv)),
                "refused_items": [r["text"] for r in on if not r[f"{arm}_on"]],
                "allowed_items": [r["text"] for r in off if r[f"{arm}_on"]]}

    def mcnemar(subset, a, b):
        both = [r for r in subset if r[f"{a}_on"] is not None and r[f"{b}_on"] is not None]
        right = lambda r, arm: r[f"{arm}_on"] == (r["label"] == "on")
        x = sum(1 for r in both if right(r, a) and not right(r, b))
        y = sum(1 for r in both if not right(r, a) and right(r, b))
        return {f"{a}_right_{b}_wrong": x, f"{b}_right_{a}_wrong": y, "p": mcnemar_exact(x, y)}

    sets = {"test": [r for r in rows if r["split"] == "test" and not r["borderline"]],
            "holdout": [r for r in rows if r["split"] == "holdout"]}
    border = [r for r in rows if r["borderline"]]
    lat = {arm: [x["ms"] for x in calls if x["arm"] == arm and not x["error"]] for arm in ARMS}
    errors = {arm: sum(1 for x in calls if x["arm"] == arm and x["error"]) for arm in ARMS}
    cost = {arm: statistics.fmean(x["cost"] for x in calls if x["arm"] == arm and not x["error"]) for arm in ARMS}
    gate_med = {arm: {r["id"]: statistics.median(x["ms"] for x in by[(r["id"], arm)] if not x["error"]) for r in rows
                      if any(not x["error"] for x in by.get((r["id"], arm), []))} for arm in ARMS}
    ft = {k: v for k, v in first_tokens.items() if v is not None}
    ttfw = {}
    for arm in ARMS:
        seq = [gate_med[arm][k] + v for k, v in ft.items() if k in gate_med[arm]]
        par = [max(gate_med[arm][k], v) for k, v in ft.items() if k in gate_med[arm]]
        ttfw[arm] = {"sequential_p50": pct(seq, 0.5), "parallel_p50": pct(par, 0.5),
                     "sequential_p95": pct(seq, 0.95), "parallel_p95": pct(par, 0.95)}
    summary = {
        "date": stamp, "models": {"gemini": settings.gemini_fast_model, "jev": settings.decision_model, "answer": settings.gemini_model},
        "items": len(items), "repeats": args.repeats, "calls": len(calls), "errors": errors,
        "thresholds": {arm: {"threshold": t, "dev_fit": fit} for arm, (t, fit) in thresholds.items()},
        "scores": {name: {arm: scores(sub, arm) for arm in ARMS} for name, sub in sets.items()},
        "mcnemar": {name: {"gemini_vs_jev_names": mcnemar(sub, "gemini", "jev_names"), "jev_vs_jev_names": mcnemar(sub, "jev", "jev_names")}
                    for name, sub in sets.items()},
        "calibration": {name: {arm: calibration([(r[f"{arm}_p"], 1 if r["label"] == "on" else 0) for r in sub]) for arm in ARMS}
                        for name, sub in sets.items()},
        "borderline_agreement": {arm: wilson(sum(1 for r in border if r[f"{arm}_on"] == (r["label"] == "on")),
                                             sum(1 for r in border if r[f"{arm}_on"] is not None)) for arm in ARMS},
        "stability": {"gemini_items_flipped": sum(1 for r in rows if not r["gemini_stable"]),
                      **{f"{arm}_mean_spread": statistics.fmean(r[f"{arm}_spread"] for r in rows if r[f"{arm}_spread"] is not None)
                         for arm in ("jev", "jev_names")}},
        "latency_ms": {arm: {"p50": pct(v, 0.5), "p95": pct(v, 0.95), "mean": statistics.fmean(v), "n": len(v)} for arm, v in lat.items()},
        "cost_per_1k_usd": {arm: 1000 * v for arm, v in cost.items()},
        "answer_first_token_ms": {"n": len(ft), "p50": pct(list(ft.values()), 0.5), "p95": pct(list(ft.values()), 0.95)},
        "time_to_first_word_ms": ttfw,
    }
    (out / f"intent_bench_{stamp}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    svg_chart(summary, out / f"intent_bench_{stamp}.svg")
    for name in sets:
        for arm in ARMS:
            t = summary["scores"][name][arm]
            print(f"{name} {arm}: accuracy {t['accuracy'][0]:.1%} [{t['accuracy'][1]:.1%}, {t['accuracy'][2]:.1%}] of {t['n']}; "
                  f"refused real {t['false_refusal'][0]:.1%}; let through off-topic {t['missed_off'][0]:.1%}, attacks {t['missed_adversarial'][0]:.1%}")
            print("    refused:", t["refused_items"], "| allowed:", t["allowed_items"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
