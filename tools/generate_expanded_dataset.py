from __future__ import annotations

import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
OUT_PATH = PROJECT_DIR / "datasets" / "prompts.expanded.120.json"


def main() -> int:
    categories: list[tuple[str, str, list[str]]] = [
        (
            "mechanical_edits",
            "local",
            [
                "Rename variable `{a}` to `{b}` in one file only.",
                "Add type annotations to `{a}` function parameters in this file.",
                "Reformat this `{a}` snippet for readability without behavior change.",
                "Write a concise docstring for `{a}` with params and returns.",
                "Convert this JSON payload shape into a `{a}` interface.",
                "Update import path from `{a}` to `{b}` in this module.",
            ],
        ),
        (
            "targeted_bugfix",
            "local",
            [
                "Fix an off-by-one bug in `{a}` loop; root cause already known.",
                "Correct null-check ordering in `{a}` to avoid crash.",
                "Patch typo in `{a}` config key that breaks startup.",
                "Replace deprecated `{a}` call with modern equivalent.",
                "Fix unit test `{a}` expectations after harmless refactor.",
                "Repair regex `{a}` to match semantic versions correctly.",
            ],
        ),
        (
            "diagnosis_and_ops",
            "cloud",
            [
                "Diagnose intermittent `{a}` deadlocks visible only under production load.",
                "Explain why CI passes locally but fails on hosted runner for `{a}`.",
                "Summarize likely root cause from this long `{a}` stack trace.",
                "Design rollback plan for `{a}` deployment showing elevated error rates.",
                "Investigate memory leak pattern in `{a}` service over 24h.",
                "Triage flaky integration test `{a}` with nondeterministic timing failures.",
            ],
        ),
        (
            "architecture_and_tradeoffs",
            "cloud",
            [
                "Choose between `{a}` and `{b}` architecture for strict audit needs.",
                "Design multi-region caching policy for `{a}` read-heavy workload.",
                "Plan zero-downtime migration from shared DB to `{a}` sharded model.",
                "Define retry/backoff strategy to avoid amplifying `{a}` upstream outages.",
                "Evaluate queue vs RPC for `{a}` reliability and latency tradeoffs.",
                "Propose tenancy isolation strategy for `{a}` with compliance constraints.",
            ],
        ),
        (
            "security_review",
            "cloud",
            [
                "Review auth token validation changes in `{a}` and identify security gaps.",
                "Assess risk of `{a}` request-signing implementation under replay attacks.",
                "Design secret-rotation workflow for `{a}` with zero service interruption.",
                "Threat-model `{a}` webhook ingestion path and propose mitigations.",
                "Evaluate permission boundary changes in `{a}` IAM policy diff.",
                "Analyze SSRF safeguards for `{a}` URL-fetch pipeline.",
            ],
        ),
        (
            "ambiguous_boundary",
            "cloud",
            [
                "Refactor `{a}` 300-line class into smaller units while preserving behavior.",
                "Choose an index for slow `{a}` query given schema and EXPLAIN output.",
                "Propose test strategy for `{a}` module with sparse existing coverage.",
                "Assess whether `{a}` should be split into services or kept modular monolith.",
                "Review `{a}` API contract changes and backward compatibility risk.",
                "Debug sporadic timeout in `{a}` where logs are partially missing.",
            ],
        ),
    ]

    slots_a = [
        "orderSync",
        "retryWorker",
        "billingPipeline",
        "authGateway",
        "cacheLayer",
        "tenantRouter",
        "queryPlanner",
        "ingestionFlow",
        "eventStore",
        "sessionManager",
    ]
    slots_b = [
        "messageQueue",
        "eventSourcing",
        "auditLog",
        "backoffPolicy",
        "batchUpdater",
        "readReplica",
        "idempotencyKey",
        "accessPolicy",
        "requestSigner",
        "tokenVerifier",
    ]

    rows = []
    idx = 1
    # 6 categories * 20 prompts = 120
    for category, label, templates in categories:
        for n in range(20):
            template_idx = n % len(templates)
            t = templates[template_idx]
            a = slots_a[n % len(slots_a)]
            b = slots_b[(n + 3) % len(slots_b)]
            prompt = t.replace("{a}", a).replace("{b}", b)
            rows.append(
                {
                    "id": idx,
                    "prompt": prompt,
                    "label": label,
                    "category": category,
                    "template_id": f"{category}:{template_idx + 1}",
                }
            )
            idx += 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote={OUT_PATH}")
    print(f"count={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

