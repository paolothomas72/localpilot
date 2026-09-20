from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def gate_result(agg: dict[str, Any], gates: dict[str, float], expected_runs: int) -> tuple[str, list[str]]:
    reasons: list[str] = []
    acc = agg.get("accuracy_mean")
    parse_rate = agg.get("parse_rate_mean")
    lat = agg.get("avg_latency_mean_ms")
    p95 = agg.get("p95_latency_mean_ms")
    ok_runs = agg.get("ok_runs")
    timeout_runs = agg.get("timeout_runs", 0)
    incomplete = bool(agg.get("incomplete"))

    if incomplete or (isinstance(ok_runs, int) and ok_runs < expected_runs):
        reasons.append("incomplete_runs")
    if isinstance(timeout_runs, int) and timeout_runs > 0:
        reasons.append(f"timeouts={timeout_runs}")

    if not isinstance(acc, (int, float)) or acc < gates["min_accuracy"]:
        reasons.append(f"accuracy<{gates['min_accuracy']}")
    if not isinstance(parse_rate, (int, float)) or parse_rate < gates["min_parse_rate"]:
        reasons.append(f"parse_rate<{gates['min_parse_rate']}")
    if not isinstance(lat, (int, float)) or lat > gates["max_avg_latency_ms"]:
        reasons.append(f"avg_latency>{gates['max_avg_latency_ms']}")
    if not isinstance(p95, (int, float)) or p95 > gates["max_p95_latency_ms"]:
        reasons.append(f"p95_latency>{gates['max_p95_latency_ms']}")

    verdict = "PASS" if not reasons else "WARN"
    if "incomplete_runs" in reasons:
        verdict = "INCOMPLETE"
    return verdict, reasons


def score(agg: dict[str, Any]) -> float:
    # Higher better: combine accuracy and speed into a single index.
    acc = agg.get("accuracy_mean") or 0.0
    lat = agg.get("avg_latency_mean_ms") or 999999.0
    p95 = agg.get("p95_latency_mean_ms") or 999999.0
    parse_rate = agg.get("parse_rate_mean") or 0.0
    # Normalize latency terms with soft caps.
    lat_term = max(0.0, 1.0 - min(lat, 5000.0) / 5000.0)
    p95_term = max(0.0, 1.0 - min(p95, 8000.0) / 8000.0)
    return round((0.5 * acc) + (0.25 * parse_rate) + (0.15 * lat_term) + (0.10 * p95_term), 4)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build leaderboard markdown from multi-run evaluation JSON.")
    parser.add_argument("--in", dest="input_path", default="runs/multi-eval-results.json")
    parser.add_argument("--out-md", default="runs/leaderboard.md")
    parser.add_argument("--out-json", default="runs/leaderboard.json")
    parser.add_argument("--min-accuracy", type=float, default=0.93)
    parser.add_argument("--min-parse-rate", type=float, default=1.0)
    parser.add_argument("--max-avg-latency-ms", type=float, default=1800.0)
    parser.add_argument("--max-p95-latency-ms", type=float, default=3500.0)
    args = parser.parse_args()

    input_path = Path(args.input_path).resolve()
    out_md = Path(args.out_md).resolve()
    out_json = Path(args.out_json).resolve()
    data = json.loads(input_path.read_text(encoding="utf-8"))
    gates = {
        "min_accuracy": args.min_accuracy,
        "min_parse_rate": args.min_parse_rate,
        "max_avg_latency_ms": args.max_avg_latency_ms,
        "max_p95_latency_ms": args.max_p95_latency_ms,
    }

    rows = []
    for item in data.get("results", []):
        c = item.get("candidate", {})
        agg = item.get("aggregate", {})
        verdict, reasons = gate_result(agg, gates, expected_runs=int(data.get("runs_per_model", 0) or 0))
        s = score(agg)
        rows.append(
            {
                "id": c.get("id"),
                "runtime": c.get("runtime"),
                "model": c.get("model"),
                "tier": c.get("tier"),
                "score": s,
                "verdict": verdict,
                "reasons": reasons,
                "aggregate": agg,
                "ok_runs": agg.get("ok_runs"),
                "timeout_runs": agg.get("timeout_runs"),
            }
        )

    rows.sort(key=lambda r: r["score"], reverse=True)
    leaderboard = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(input_path),
        "gates": gates,
        "rows": rows,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(leaderboard, indent=2), encoding="utf-8")

    lines = [
        "# LocalPilot Leaderboard",
        "",
        f"- Source: `{input_path}`",
        f"- Generated: `{leaderboard['generated_at']}`",
        "",
        "## Gates",
        f"- min_accuracy: `{gates['min_accuracy']}`",
        f"- min_parse_rate: `{gates['min_parse_rate']}`",
        f"- max_avg_latency_ms: `{gates['max_avg_latency_ms']}`",
        f"- max_p95_latency_ms: `{gates['max_p95_latency_ms']}`",
        "",
        "## Rankings",
        "",
        "| Rank | ID | Runtime | Model | Tier | Score | Verdict | Accuracy | Avg ms | P95 ms | Parse Rate |",
        "|---:|---:|---|---|---|---:|---|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(rows, start=1):
        agg = r["aggregate"]
        lines.append(
            f"| {i} | {r['id']} | {r['runtime']} | `{r['model']}` | {r['tier']} | {r['score']} | {r['verdict']} | "
            f"{agg.get('accuracy_mean')} | {agg.get('avg_latency_mean_ms')} | {agg.get('p95_latency_mean_ms')} | {agg.get('parse_rate_mean')} |"
        )
        lines.append(
            f"|  |  |  |  |  |  | runs | `ok={r.get('ok_runs')} timeout={r.get('timeout_runs')} router_direct_acc={agg.get('router_direct_accuracy_mean')}` |  |  |  |"
        )
        if r["reasons"]:
            lines.append(f"|  |  |  |  |  |  | reasons | `{' ; '.join(r['reasons'])}` |  |  |  |")
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote_md={out_md}")
    print(f"wrote_json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

