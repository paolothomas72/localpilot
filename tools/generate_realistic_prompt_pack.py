from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


CAPABILITY_KEYWORDS = [
    "security",
    "auth",
    "deadlock",
    "migration",
    "outage",
    "hosted runner",
    "stack trace",
]

PRIVACY_KEYWORDS = [
    "pii",
    "phi",
    "hipaa",
    "ssn",
    "personal data",
    "customer data",
    "production dump",
    "secret",
    "api key",
    "token",
    "credential",
]


def matched_keywords(prompt: str, vocab: list[str]) -> list[str]:
    lowered = prompt.lower()
    out: list[str] = []
    for k in vocab:
        if k in lowered:
            out.append(k)
    return out


def build_rows(seed: int) -> list[dict]:
    rnd = random.Random(seed)
    systems = ["billingPipeline", "eventStore", "tenantRouter", "sessionManager", "cacheLayer", "authGateway"]
    repos = ["widget-store", "order-sync", "catalog-service", "checkout-core", "report-worker", "admin-console"]

    very_small_templates = [
        ("Rename `{old}` to `{new}` in `{path}` and run `{test}` only.", "local"),
        ("Add type hints to `{fn}` in `{path}` without behavior changes; run `{test}`.", "local"),
        ("Write a docstring for `{fn}` in `{path}` with params and return.", "local"),
        ("Reformat `{path}` for readability only; show diff before finish.", "local"),
        ("Fix known null-check bug in `{path}` line `{line}`; rerun `{test}`.", "local"),
        ("Convert fixture `{fixture}` to interface `{iface}` in `{path}`.", "local"),
        ("Patch typo in config key `{cfg}` in `{path}` and run `{test}`.", "local"),
        ("Translate one-liner from bash to PowerShell in `{path}`.", "local"),
    ]
    medium_small_templates = [
        ("Investigate flaky `{test}` (1/8 failures) using logs and test code; provide likely root cause and smallest patch.", "cloud"),
        ("Refactor `{path}` into smaller units but keep API unchanged; propose plan then apply minimal phase 1.", "local"),
        ("Analyze why `{test}` passes local but fails on hosted runner; rank top causes and patch YAML.", "cloud"),
        ("Review `{path}` for auth/session mistakes and list severity-ranked issues.", "cloud"),
        ("Choose index changes for `{path}` using EXPLAIN output and justify tradeoffs.", "cloud"),
        ("Triage intermittent timeout in `{repo}` with partial traces; propose evidence-first next steps.", "cloud"),
        ("Implement bounded retry/backoff in `{path}` without changing call signatures.", "local"),
        ("Assess whether module `{module}` should split into services or stay modular monolith.", "cloud"),
    ]
    heavy_small_templates = [
        ("Design zero-downtime migration for `{repo}` from shared DB to per-tenant schemas with rollback.", "cloud"),
        ("Diagnose production deadlock in `{repo}` using lock graphs and stack trace; propose hotfix and long fix.", "cloud"),
        ("Threat-model webhook ingestion in `{repo}` and propose phased mitigations.", "cloud"),
        ("Plan outage response for `{repo}` with elevated 5xx and unclear root cause.", "cloud"),
        ("Create local-only sanitation plan for production dump with customer data and token fields.", "local"),
        ("Perform security review on auth token validation and secret rotation policy for `{repo}`.", "cloud"),
        ("Build incident decision tree for multi-service latency regression in `{repo}`.", "cloud"),
        ("Design privacy-first data redaction flow for pii/phi export artifacts in `{repo}`.", "local"),
    ]

    def fill(t: str) -> str:
        sys = rnd.choice(systems)
        repo = rnd.choice(repos)
        return t.format(
            old="tmpCount",
            new="retryCount",
            path=f"src/{repo}/{sys}.py",
            test=f"pytest tests/{repo}/test_{sys.lower()}.py -q",
            fn=f"process_{sys.lower()}",
            line=str(rnd.randint(20, 180)),
            fixture=f"fixtures/{repo}/{sys}.json",
            iface=f"{sys}Response",
            cfg=f"{sys}.poll_secs",
            repo=repo,
            module=sys,
        )

    rows: list[dict] = []
    spec = [
        ("very_small", very_small_templates, 40),
        ("medium_small", medium_small_templates, 40),
        ("heavy_small", heavy_small_templates, 40),
    ]
    idx = 1
    for size, templates, count in spec:
        for i in range(count):
            t, label = templates[i % len(templates)]
            prompt = fill(t)
            rows.append(
                {
                    "id": idx,
                    "task_size": size,
                    "template_id": f"{size}_{i % len(templates):02d}",
                    "category": size,
                    "prompt": prompt,
                    "label": label,
                    "capability_keywords_expected": matched_keywords(prompt, CAPABILITY_KEYWORDS),
                    "privacy_keywords_expected": matched_keywords(prompt, PRIVACY_KEYWORDS),
                }
            )
            idx += 1
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate realistic LocalPilot prompt pack by task size.")
    parser.add_argument("--out", default="datasets/promptpack-realistic-v1.json")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    out = Path(args.out).resolve()
    rows = build_rows(args.seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote={out}")
    print(f"rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

