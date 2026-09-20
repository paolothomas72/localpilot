from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[idx]


def fmt_num(v: Any, ndigits: int = 2) -> str:
    if isinstance(v, (int, float)):
        return str(round(float(v), ndigits))
    return "n/a"


def fmt_pct(v: Any, ndigits: int = 2) -> str:
    if isinstance(v, (int, float)):
        return f"{round(float(v) * 100.0, ndigits)}%"
    return "n/a"


def safe_div(n: float, d: float) -> float:
    return (n / d) if d else 0.0


def compute_metrics(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary", {})
    rows = data.get("rows", [])
    total = len(rows)
    ok_rows = [r for r in rows if r.get("ok") is True]
    parsed_rows = [r for r in rows if r.get("route") in ("local", "cloud")]
    matched_rows = [r for r in rows if r.get("label") == r.get("route")]
    latencies = [float(r.get("latency_ms")) for r in rows if isinstance(r.get("latency_ms"), (int, float))]

    disagreements = [
        {
            "id": r.get("id"),
            "label": r.get("label"),
            "route": r.get("route"),
            "policy_path": r.get("policy_path"),
            "confidence": r.get("confidence"),
            "latency_ms": r.get("latency_ms"),
            "risk_axis_decision": r.get("risk_axis_decision"),
            "capability_risk_keywords": r.get("capability_risk_keywords"),
            "privacy_risk_keywords": r.get("privacy_risk_keywords"),
            "prompt": r.get("prompt"),
        }
        for r in rows
        if r.get("label") in ("local", "cloud") and r.get("route") in ("local", "cloud") and r.get("label") != r.get("route")
    ]

    policy_counts = Counter(str(r.get("policy_path") or "unknown") for r in rows)
    axis_counts = Counter(str(r.get("risk_axis_decision") or "unknown") for r in rows)
    conflict_count = sum(1 for r in rows if r.get("risk_conflict") is True)
    threshold_applied_count = sum(1 for r in rows if r.get("threshold_applied") is True)

    llm_first_rows = [r for r in rows if isinstance(r.get("router_first_confidence"), (int, float))]
    llm_uncertain_count = 0
    confidence_threshold = summary.get("confidence_threshold")
    if isinstance(confidence_threshold, (int, float)):
        llm_uncertain_count = sum(1 for r in llm_first_rows if float(r.get("router_first_confidence")) < float(confidence_threshold))

    label_local = sum(1 for r in rows if r.get("label") == "local")
    label_cloud = sum(1 for r in rows if r.get("label") == "cloud")
    pred_local = sum(1 for r in rows if r.get("route") == "local")
    pred_cloud = sum(1 for r in rows if r.get("route") == "cloud")
    local_to_cloud = sum(1 for r in rows if r.get("label") == "local" and r.get("route") == "cloud")
    cloud_to_local = sum(1 for r in rows if r.get("label") == "cloud" and r.get("route") == "local")

    parse_rate = safe_div(len(parsed_rows), total)
    accuracy = safe_div(len(matched_rows), total)
    ok_rate = safe_div(len(ok_rows), total)

    return {
        "runtime": summary.get("runtime"),
        "router_model": summary.get("router_model"),
        "fallback_model": summary.get("fallback_model"),
        "total": total,
        "parsed": len(parsed_rows),
        "ok_count": len(ok_rows),
        "failed_count": total - len(ok_rows),
        "accuracy": accuracy,
        "parse_rate": parse_rate,
        "ok_rate": ok_rate,
        "avg_latency_ms": mean(latencies) if latencies else None,
        "p50_latency_ms": median(latencies) if latencies else None,
        "p95_latency_ms": percentile(latencies, 95) if latencies else None,
        "max_latency_ms": max(latencies) if latencies else None,
        "policy_path_counts": dict(policy_counts),
        "risk_axis_decision_counts": dict(axis_counts),
        "risk_conflict_count": conflict_count,
        "threshold_applied_count": threshold_applied_count,
        "llm_first_count": len(llm_first_rows),
        "llm_uncertain_count": llm_uncertain_count,
        "labels_local": label_local,
        "labels_cloud": label_cloud,
        "pred_local": pred_local,
        "pred_cloud": pred_cloud,
        "local_to_cloud": local_to_cloud,
        "cloud_to_local": cloud_to_local,
        "disagreements": disagreements,
        "source_summary": summary,
    }


def build_markdown_card(metrics: dict[str, Any], source_label: str, title: str) -> str:
    generated = datetime.now(timezone.utc).isoformat()
    lines = [
        f"# {title}",
        "",
        f"- Source artifact: `{source_label}`",
        f"- Generated (UTC): `{generated}`",
        f"- Runtime: `{metrics['runtime']}`",
        f"- Router model: `{metrics['router_model']}`",
        f"- Fallback model: `{metrics['fallback_model']}`",
        "",
        "## Headline Metrics",
        f"- Total samples: `{metrics['total']}`",
        f"- Accuracy (total denominator): `{fmt_pct(metrics['accuracy'])}`",
        f"- Parse rate: `{fmt_pct(metrics['parse_rate'])}`",
        f"- Success rate (`ok=true`): `{fmt_pct(metrics['ok_rate'])}`",
        f"- Average latency: `{fmt_num(metrics['avg_latency_ms'])} ms`",
        f"- P50 latency: `{fmt_num(metrics['p50_latency_ms'])} ms`",
        f"- P95 latency: `{fmt_num(metrics['p95_latency_ms'])} ms`",
        f"- Max latency: `{fmt_num(metrics['max_latency_ms'])} ms`",
        "",
        "## Reliability + Error Surface",
        f"- Parsed / Total: `{metrics['parsed']}/{metrics['total']}`",
        f"- OK / Failed: `{metrics['ok_count']}/{metrics['failed_count']}`",
        f"- Label local/cloud: `{metrics['labels_local']}/{metrics['labels_cloud']}`",
        f"- Pred local/cloud: `{metrics['pred_local']}/{metrics['pred_cloud']}`",
        "",
        "## Misroute Breakdown",
        f"- Local -> Cloud: `{metrics['local_to_cloud']}`",
        f"- Cloud -> Local: `{metrics['cloud_to_local']}`",
        f"- Total disagreements: `{len(metrics['disagreements'])}`",
        "",
        "## Routing Policy Mix",
        "",
        "| Policy Path | Count | Share |",
        "|---|---:|---:|",
    ]

    total = max(1, int(metrics["total"]))
    for k, v in sorted(metrics["policy_path_counts"].items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"| `{k}` | {v} | {fmt_pct(v / total)} |")

    lines.extend(
        [
            "",
            "## Two-Axis Risk Audit",
            f"- Risk conflicts: `{metrics['risk_conflict_count']}`",
            f"- Threshold-applied decisions: `{metrics['threshold_applied_count']}`",
            f"- LLM-first samples: `{metrics['llm_first_count']}`",
            f"- LLM uncertain samples: `{metrics['llm_uncertain_count']}`",
            "",
            "| Axis Decision | Count | Share |",
            "|---|---:|---:|",
        ]
    )
    for k, v in sorted(metrics["risk_axis_decision_counts"].items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"| `{k}` | {v} | {fmt_pct(v / total)} |")

    lines.extend(
        [
            "",
            "## Disagreement Cases",
            "",
            "| ID | Label | Route | Policy Path | Confidence | Latency ms | Axis Decision |",
            "|---:|---|---|---|---:|---:|---|",
        ]
    )
    for d in metrics["disagreements"]:
        lines.append(
            f"| {d.get('id')} | {d.get('label')} | {d.get('route')} | `{d.get('policy_path')}` | "
            f"{fmt_num(d.get('confidence'), 4)} | {fmt_num(d.get('latency_ms'))} | {d.get('risk_axis_decision')} |"
        )

    lines.extend(["", "### Disagreement Prompt Details", ""])
    if not metrics["disagreements"]:
        lines.append("- None")
    else:
        for d in metrics["disagreements"]:
            lines.extend(
                [
                    f"- ID `{d.get('id')}` | label=`{d.get('label')}` route=`{d.get('route')}` path=`{d.get('policy_path')}`",
                    f"  - prompt: {d.get('prompt')}",
                    f"  - capability keywords: {d.get('capability_risk_keywords')}",
                    f"  - privacy keywords: {d.get('privacy_risk_keywords')}",
                ]
            )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build publish-ready benchmark card from one LocalPilot benchmark artifact.")
    parser.add_argument("--in", dest="input_path", required=True, help="Path to benchmark-*.json artifact")
    parser.add_argument("--out-md", default=None, help="Output markdown path")
    parser.add_argument("--out-json", default=None, help="Output JSON metrics path")
    parser.add_argument("--title", default="LocalPilot Benchmark Card", help="Card title")
    parser.add_argument("--source-label", default=None, help="Label shown in card for source artifact (default: input filename)")
    args = parser.parse_args()

    input_path = Path(args.input_path).resolve()
    data = json.loads(input_path.read_text(encoding="utf-8"))
    metrics = compute_metrics(data)
    source_label = args.source_label or input_path.name
    card_md = build_markdown_card(metrics, source_label, args.title)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_md = Path(args.out_md).resolve() if args.out_md else input_path.parent / f"benchmark-card-{stamp}.md"
    out_json = Path(args.out_json).resolve() if args.out_json else input_path.parent / f"benchmark-card-{stamp}.json"

    out_md.write_text(card_md, encoding="utf-8")
    out_json.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(f"wrote_md={out_md}")
    print(f"wrote_json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

