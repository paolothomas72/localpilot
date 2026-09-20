from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

DEFAULT_CONFIG = {
    "runtime": "ollama",
    "ollama": {"host": "127.0.0.1", "port": 11434},
    "llamacpp": {
        "base_url": "http://127.0.0.1:8090",
        "endpoint": "/v1/chat/completions",
        "api_key": "",
        "timeout_s": 45,
        "temperature": 0.0,
        "top_p": 1.0,
        "top_k": 1,
        "min_p": 0.0,
        "repeat_penalty": 1.0,
        "seed": 7,
        "max_tokens": 48,
    },
    "models": {
        "router": "gemma4:12b",
        "fallback": "qwen3-8b-ft-agentic-v1:latest",
        "ollama_router": "gemma4:12b",
        "ollama_fallback": "qwen3-8b-ft-agentic-v1:latest",
        "llamacpp_router": "local-model",
        "llamacpp_fallback": None,
    },
    "policy": {
        "confidence_threshold": 0.88,
        "risk_keywords": ["security", "auth", "deadlock", "migration", "outage", "hosted runner"],
        "two_axis": {
            "enabled": True,
            "capability_threshold": 1,
            "privacy_threshold": 1,
            "conflict_mode": "privacy_first",
            "capability_match_mode": "token",
            "privacy_match_mode": "substring",
            "capability_phrases": [
                "split into services",
                "modular monolith",
                "threat-model",
                "threat model",
                "rollback plan",
                "secret-rotation workflow",
                "zero-downtime",
                "upstream outages",
                "architecture",
            ],
            "privacy_keywords": [
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
            ],
        },
        "cheap_router": {
            "enabled": True,
            "dataset": "datasets/splits/dev.json",
            "confidence_threshold": 0.85,
        },
        "uncertain_band": {
            "low_confidence_to_cloud": True
        },
        "privacy_content": {
            "enabled": True,
            "threshold": 1,
            "fail_closed_on_error": True,
        },
    },
    "local": {
        "single_model_lock": True,
        "warmup_on_route": True,
        "warmup_calls": 1,
        "keep_alive": "30m",
    },
}

DEFAULT_PROMPTS = [
    {"id": 1, "prompt": "Explain what this 12-line function does, line by line.", "label": "local"},
    {"id": 2, "prompt": "Rename a variable from tmp to retryCount in one file.", "label": "local"},
    {"id": 3, "prompt": "Convert this JSON response into a TypeScript interface.", "label": "local"},
    {"id": 4, "prompt": "Write a regex for semantic version strings like 1.20.3.", "label": "local"},
    {"id": 5, "prompt": "Add a docstring to one function with params and return value.", "label": "local"},
    {"id": 6, "prompt": "Fix an off-by-one loop bug that is already identified.", "label": "local"},
    {"id": 7, "prompt": "Reformat a 30-line SQL query without changing behavior.", "label": "local"},
    {"id": 8, "prompt": "Translate a bash one-liner to equivalent PowerShell.", "label": "local"},
    {"id": 9, "prompt": "Design caching architecture for a read-heavy multi-region service.", "label": "cloud"},
    {"id": 10, "prompt": "Choose event sourcing vs CRUD for strict audit requirements.", "label": "cloud"},
    {"id": 11, "prompt": "Diagnose an intermittent production database deadlock.", "label": "cloud"},
    {"id": 12, "prompt": "Plan zero-downtime migration to per-tenant shards.", "label": "cloud"},
    {"id": 13, "prompt": "Review auth token validation diff for security holes.", "label": "cloud"},
    {"id": 14, "prompt": "Design retry and backoff to avoid amplifying upstream outages.", "label": "cloud"},
    {"id": 15, "prompt": "Write tests for a pure function with three branches.", "label": "local"},
    {"id": 16, "prompt": "Explain why CI passes locally but fails on hosted runner.", "label": "cloud"},
    {"id": 17, "prompt": "Refactor a 300-line class into smaller single-responsibility units.", "label": "local"},
    {"id": 18, "prompt": "Read a 40-line stack trace and summarize likely root cause.", "label": "cloud"},
    {"id": 19, "prompt": "Choose an index for a slow query using EXPLAIN output.", "label": "local"},
    {"id": 20, "prompt": "Write a commit message for an already staged diff.", "label": "local"},
]


@dataclass
class BenchmarkSummary:
    total: int
    parsed: int
    accuracy: float | None
    accuracy_parsed_only: float | None
    avg_latency_ms: float | None
    local_count: int
    cloud_count: int
    ok_count: int
    failed_count: int


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def init_workspace(project_dir: Path) -> dict[str, str]:
    cfg_path = project_dir / "localpilot.config.json"
    prompts_path = project_dir / "prompts.seed.json"
    out_dir = project_dir / "runs"
    if not cfg_path.exists():
        save_json(cfg_path, DEFAULT_CONFIG)
    if not prompts_path.exists():
        save_json(prompts_path, DEFAULT_PROMPTS)
    out_dir.mkdir(parents=True, exist_ok=True)
    return {"config": str(cfg_path), "prompts": str(prompts_path), "runs": str(out_dir)}


def summarize_rows(rows: list[dict[str, Any]]) -> BenchmarkSummary:
    valid_route_rows = [r for r in rows if r.get("route") in ("local", "cloud")]
    matched_total = [r for r in rows if r.get("label") == r.get("route")]
    matched_parsed = [r for r in valid_route_rows if r.get("label") == r.get("route")]
    latencies = [float(r.get("latency_ms", 0)) for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    ok_rows = [r for r in rows if r.get("ok") is True]
    return BenchmarkSummary(
        total=len(rows),
        parsed=len(valid_route_rows),
        accuracy=(len(matched_total) / len(rows)) if rows else None,
        accuracy_parsed_only=(len(matched_parsed) / len(valid_route_rows)) if valid_route_rows else None,
        avg_latency_ms=mean(latencies) if latencies else None,
        local_count=sum(1 for r in valid_route_rows if r.get("route") == "local"),
        cloud_count=sum(1 for r in valid_route_rows if r.get("route") == "cloud"),
        ok_count=len(ok_rows),
        failed_count=len(rows) - len(ok_rows),
    )

