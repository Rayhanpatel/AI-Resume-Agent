"""
Off-topic gate on conversations: does seeing the previous turn fix refused follow-ups?

Three gates on the last message of each conversation in gate_conversations.jsonl (dev) and
gate_conversations_holdout.jsonl (another author, first turns not in dev):
  gemini  the Flash-Lite classifier, message only (the gate before Oct 30)
  v2      the decision model with the profile's names, message only, threshold 0.75 (live Oct 30)
  v3      the same, plus the previous turn (services.intent.previous_turn), threshold chosen on dev
The plan, the labelling rules and the ship rule were committed before the holdout and the code
(gate_v3_preregistration.md); this script applies them and reports the decision either way.

  cd backend && TOKEN=$(gcloud auth print-access-token) GOOGLE_CLOUD_PROJECT=... \\
    OPENROUTER_API_KEY=... venv/bin/python -m evals.gate_bench
"""
import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
os.environ.setdefault("GOOGLE_API_KEY", "unused")

from intent_bench import _GcloudAuth, _UsageCapture, run_gemini, wilson, pct, mcnemar_exact  # noqa: E402
from core.config import get_settings  # noqa: E402
from services.intent import IntentClassifier, JevIntentGate, _load_profile_names, previous_turn  # noqa: E402

ARMS = ("gemini", "v2", "v3")
V2_THRESHOLD = 0.75
THRESHOLDS = [round(0.05 * i, 2) for i in range(1, 20)]
REFUSAL_COST = 10  # per real message refused, against 1 per off-topic message let through (preregistration)


async def run_jev(gate, text, previous):
    _UsageCapture.last = None
    start = time.perf_counter()
    try:
        p, error = await gate.probability(text, previous), False
    except Exception as e:
        p, error = None, f"{type(e).__name__}: {e}"[:120]
    usage = _UsageCapture.last or {}
    return {"ms": (time.perf_counter() - start) * 1000, "error": error, "p_on": p, "cost": float(usage.get("cost") or 0)}


def choose_threshold(dev):
    """Preregistered: minimise 10 x real refused + 1 x off-topic allowed on dev scored items; ties to the lower threshold"""
    best = None
    for t in THRESHOLDS:
        refused = sum(1 for r in dev if r["label"] == "on" and r["v3_p"] < t)
        allowed = sum(1 for r in dev if r["label"] == "off" and r["v3_p"] >= t)
        loss = REFUSAL_COST * refused + allowed
        if best is None or loss < best[0]:
            best = (loss, t, refused, allowed)
    return best[1], {"loss": best[0], "refused": best[2], "allowed": best[3]}


def svg_chart(summary, path):
    """Mistakes on the holdout's scored messages, by gate (no plotting dependency)"""
    arms = [("Flash-Lite classifier, message only", "gemini", "#6b7280"), ("Version 2: Jev, message only", "v2", "#93c5fd"),
            ("Version 3: Jev + previous turn", "v3", "#1d6fb8")]
    h = summary["scores"]["holdout"]
    groups = [("real follow-ups refused", "followups_refused", "followups"), ("all real messages refused", "refused", "on"),
              ("off-topic or attacks let through", "allowed", "off")]
    w, ht = 820, 60 + len(groups) * 66 + 40
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{ht}" font-family="system-ui, sans-serif" font-size="12">',
           '<rect width="100%" height="100%" fill="#ffffff"/>',
           f'<text x="20" y="24" font-size="14" font-weight="600" fill="#111827">Off-topic gate on conversations: '
           f'mistakes on the holdout ({h["v3"]["on"]} real, {h["v3"]["off"]} off-topic last messages)</text>']
    y = 48
    for label, key, denom in groups:
        out.append(f'<text x="20" y="{y + 12}" fill="#374151">{label}</text>')
        y += 18
        for _, arm, color in arms:
            k, n = h[arm][key], h[arm][denom]
            bw = 0 if not k else max(1, 420 * k / n)  # a zero gets no bar
            out.append(f'<rect x="20" y="{y}" width="{bw:.1f}" height="13" fill="{color}"/>'
                       f'<text x="{26 + bw:.1f}" y="{y + 11}" fill="#111827">{k} of {n}</text>')
            y += 16
        y += 10
    for i, (name, _, color) in enumerate(arms):
        out.append(f'<rect x="{20 + i * 265}" y="{ht - 24}" width="12" height="12" fill="{color}"/>'
                   f'<text x="{38 + i * 265}" y="{ht - 14}" fill="#374151">{name}</text>')
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--seed", type=int, default=20261030)
    ap.add_argument("--limit", type=int, default=0, help="smoke test: only the first N items of each split")
    args = ap.parse_args(argv)

    settings = get_settings()
    load = lambda name: [json.loads(x) for x in (HERE / name).read_text(encoding="utf-8").splitlines() if x.strip()]
    items = load("gate_conversations.jsonl") + load("gate_conversations_holdout.jsonl")
    if args.limit:
        items = [i for sp in ("dev", "holdout") for i in [x for x in items if x["split"] == sp][:args.limit]]
    ctx = json.loads((HERE / "gate_contexts.json").read_text(encoding="utf-8"))
    contexts = {c["id"]: c for split in ("dev", "holdout") for c in ctx[split]}
    prev = {i["id"]: previous_turn([{"role": "user", "content": contexts[i["context"]]["visitor"]},
                                    {"role": "assistant", "content": contexts[i["context"]]["assistant"]}]) for i in items}

    rng = random.Random(args.seed)
    auth = _GcloudAuth(None, os.environ["GOOGLE_CLOUD_PROJECT"], settings.gemini_location)
    auth.enabled = True
    calls = []
    async with _UsageCapture(http2=True, timeout=60) as client:
        gemini = IntentClassifier("unused", client, auth, model=settings.gemini_fast_model, thinking_level=settings.gemini_thinking_level)
        jev = JevIntentGate(client, os.environ["OPENROUTER_API_KEY"], settings.decision_model, threshold=V2_THRESHOLD,
                            timeout=10.0, names=_load_profile_names())
        await gemini.classify("warm-up: what does Rayhan do?")
        await jev.probability("warm-up: what does Rayhan do?")
        runs = {"gemini": lambda i: run_gemini(gemini, i["text"]),
                "v2": lambda i: run_jev(jev, i["text"], None),
                "v3": lambda i: run_jev(jev, i["text"], prev[i["id"]])}
        for rep in range(args.repeats):
            order = items[:]
            rng.shuffle(order)
            for n, item in enumerate(order, 1):
                arms = list(ARMS)
                rng.shuffle(arms)
                for arm in arms:
                    calls.append({"id": item["id"], "arm": arm, "repeat": rep, **(await runs[arm](item))})
                if n % 40 == 0:
                    print(f"repeat {rep + 1}: {n}/{len(order)}", flush=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    (out / f"gate_bench_{stamp}_raw.json").write_text(json.dumps({"calls": calls}, indent=1), encoding="utf-8")  # before any analysis

    by = {}
    for c in calls:
        by.setdefault((c["id"], c["arm"]), []).append(c)
    rows = []
    for item in items:
        row = {k: item[k] for k in ("id", "split", "context", "category", "label", "borderline", "author", "text")}
        for arm in ARMS:
            ok = [c for c in by.get((item["id"], arm), []) if not c["error"]]
            if arm == "gemini":
                row["gemini_on"] = (sum(c["pred_on"] for c in ok) / len(ok) >= 0.5) if ok else None
            else:
                row[f"{arm}_p"] = round(statistics.fmean(c["p_on"] for c in ok), 4) if ok else None
                row[f"{arm}_spread"] = round(max(c["p_on"] for c in ok) - min(c["p_on"] for c in ok), 4) if ok else None
        rows.append(row)

    dev_scored = [r for r in rows if r["split"] == "dev" and not r["borderline"] and r["v3_p"] is not None]
    v3_threshold, dev_fit = choose_threshold(dev_scored)
    for r in rows:
        r["v2_on"] = None if r["v2_p"] is None else r["v2_p"] >= V2_THRESHOLD
        r["v3_on"] = None if r["v3_p"] is None else r["v3_p"] >= v3_threshold

    def scores(subset, arm):
        on = [r for r in subset if r["label"] == "on" and r[f"{arm}_on"] is not None]
        off = [r for r in subset if r["label"] == "off" and r[f"{arm}_on"] is not None]
        fol = [r for r in on if r["category"] == "followup"]
        att = [r for r in off if r["category"] == "attack"]
        refused = [r for r in on if not r[f"{arm}_on"]]
        allowed = [r for r in off if r[f"{arm}_on"]]
        per_cat = {}
        for r in on + off:
            c = per_cat.setdefault(r["category"], {"n": 0, "wrong": 0})
            c["n"] += 1
            c["wrong"] += r[f"{arm}_on"] != (r["label"] == "on")
        return {"on": len(on), "off": len(off), "followups": len(fol), "attacks": len(att),
                "refused": len(refused), "allowed": len(allowed),
                "followups_refused": sum(1 for r in fol if not r[f"{arm}_on"]),
                "attacks_allowed": sum(1 for r in att if r[f"{arm}_on"]),
                "accuracy": wilson(len(on) - len(refused) + len(off) - len(allowed), len(on) + len(off)),
                "false_refusal": wilson(len(refused), len(on)), "missed_off": wilson(len(allowed), len(off)),
                "weighted_loss": REFUSAL_COST * len(refused) + len(allowed), "per_category": per_cat,
                "refused_items": [r["text"] for r in refused], "allowed_items": [r["text"] for r in allowed]}

    def mcnemar(subset, a, b):
        both = [r for r in subset if r[f"{a}_on"] is not None and r[f"{b}_on"] is not None]
        right = lambda r, arm: r[f"{arm}_on"] == (r["label"] == "on")
        x = sum(1 for r in both if right(r, a) and not right(r, b))
        y = sum(1 for r in both if not right(r, a) and right(r, b))
        return {f"{a}_right_{b}_wrong": x, f"{b}_right_{a}_wrong": y, "p": mcnemar_exact(x, y)}

    summary = {"date": stamp, "models": {"gemini": settings.gemini_fast_model, "jev": settings.decision_model},
               "items": {s: sum(1 for r in rows if r["split"] == s) for s in ("dev", "holdout")},
               "repeats": args.repeats, "calls": len(calls),
               "errors": {a: sum(1 for c in calls if c["arm"] == a and c["error"]) for a in ARMS},
               "thresholds": {"v2": V2_THRESHOLD, "v3": v3_threshold, "v3_dev_fit": dev_fit, "refusal_cost": REFUSAL_COST},
               "scores": {}, "mcnemar": {}, "borderline": {}}
    for split in ("dev", "holdout"):
        scored = [r for r in rows if r["split"] == split and not r["borderline"]]
        summary["scores"][split] = {a: scores(scored, a) for a in ARMS}
        summary["mcnemar"][split] = mcnemar(scored, "v2", "v3")
        summary["borderline"][split] = [{"text": r["text"], "label": r["label"], "gemini_on": r["gemini_on"],
                                         "v2_p": r["v2_p"], "v3_p": r["v3_p"]} for r in rows if r["split"] == split and r["borderline"]]
    h = summary["scores"]["holdout"]
    summary["ship"] = {"rule": f"v3 refuses at most 1 real message on the holdout and its weighted loss ({REFUSAL_COST}:1) is lower than v2's",
                       "v3_refused": h["v3"]["refused"], "v3_loss": h["v3"]["weighted_loss"], "v2_loss": h["v2"]["weighted_loss"],
                       "met": h["v3"]["refused"] <= 1 and h["v3"]["weighted_loss"] < h["v2"]["weighted_loss"]}
    lat = {a: [c["ms"] for c in calls if c["arm"] == a and not c["error"]] for a in ARMS}
    summary["latency_ms"] = {a: {"p50": pct(v, 0.5), "p95": pct(v, 0.95), "p99": pct(v, 0.99), "max": max(v) if v else None, "n": len(v)}
                             for a, v in lat.items()}
    summary["over_1s"] = {a: sum(1 for x in v if x > 1000) for a, v in lat.items()}
    summary["cost_usd"] = {a: round(sum(c["cost"] for c in calls if c["arm"] == a), 4) for a in ARMS}

    (out / f"gate_bench_{stamp}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    svg_chart(summary, out / f"gate_bench_{stamp}.svg")
    print(json.dumps({k: summary[k] for k in ("thresholds", "ship", "errors", "latency_ms", "over_1s")}, indent=1))
    for split in ("dev", "holdout"):
        for a in ARMS:
            s = summary["scores"][split][a]
            print(f"{split:7} {a:6} refused {s['refused']}/{s['on']} (follow-ups {s['followups_refused']}/{s['followups']}), "
                  f"let through {s['allowed']}/{s['off']} (attacks {s['attacks_allowed']}/{s['attacks']}), loss {s['weighted_loss']}")
            print(f"        refused: {s['refused_items']}\n        allowed: {s['allowed_items']}")
    print("mcnemar", summary["mcnemar"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
