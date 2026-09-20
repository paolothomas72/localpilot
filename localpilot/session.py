from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .cheap_router import CheapRouter
from .core import load_json
from .local_ops import (
    enforce_single_model_lock,
    hardware_profile,
    is_hold_path,
    probe_host,
    probe_llamacpp,
    probe_ollama,
    probe_runtime_activity,
    probe_vram,
    read_lock,
    recommend_local_setup,
)
from .model_board import display_model, load_gate_scores, lock_hint
from .privacy import DEFAULT_CONTENT_PATTERNS
from .runtime_factory import model_for_runtime


KNOWN_PROJECT = Path(r"C:\Users\paolo\paseo-tool-workspaces\cursor\localpilot")


def page_is_openable() -> bool:
    if os.environ.get("LOCALPILOT_PLAIN"):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def project_dir(raw: str | None) -> Path:
    requested = Path(raw or ".").resolve()
    if (requested / "localpilot.config.json").exists():
        return requested
    if (KNOWN_PROJECT / "localpilot.config.json").exists():
        return KNOWN_PROJECT
    return requested


def load_config(project: Path) -> dict[str, Any]:
    cfg_path = project / "localpilot.config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}. Run `localpilot init` first.")
    return load_json(cfg_path)


def load_config_optional(project: Path) -> dict[str, Any]:
    cfg_path = project / "localpilot.config.json"
    if not cfg_path.exists():
        return {}
    return load_json(cfg_path)


def short_model(name: str) -> str:
    return display_model(name, width=44)


def build_cheap_router(cfg: dict[str, Any], project: Path) -> CheapRouter | None:
    policy = cfg.get("policy", {})
    cheap_cfg = policy.get("cheap_router", {})
    if not bool(cheap_cfg.get("enabled", False)):
        return None
    dataset_rel = cheap_cfg.get("dataset", "datasets/splits/dev.json")
    dataset_path = (project / dataset_rel).resolve()
    if not dataset_path.exists() or is_hold_path(dataset_path):
        return None
    rows = load_json(dataset_path)
    if not isinstance(rows, list):
        return None
    return CheapRouter.from_rows(rows)


def route_kwargs(cfg: dict[str, Any], project: Path, fallback_model: str | None) -> dict[str, Any]:
    two_axis = cfg.get("policy", {}).get("two_axis", {})
    cheap_cfg = cfg.get("policy", {}).get("cheap_router", {})
    privacy_content = cfg.get("policy", {}).get("privacy_content", {})
    return {
        "confidence_threshold": float(cfg["policy"]["confidence_threshold"]),
        "risk_keywords": list(cfg["policy"]["risk_keywords"]),
        "privacy_keywords": list(two_axis.get("privacy_keywords", [])),
        "capability_phrases": list(two_axis.get("capability_phrases", [])),
        "capability_match_mode": str(two_axis.get("capability_match_mode", "substring")),
        "privacy_match_mode": str(two_axis.get("privacy_match_mode", "substring")),
        "two_axis_enabled": bool(two_axis.get("enabled", True)),
        "capability_threshold": int(two_axis.get("capability_threshold", 1)),
        "privacy_threshold": int(two_axis.get("privacy_threshold", 1)),
        "conflict_mode": str(two_axis.get("conflict_mode", "privacy_first")),
        "cheap_router": build_cheap_router(cfg, project),
        "cheap_router_threshold": float(cheap_cfg.get("confidence_threshold", 0.85)),
        "low_confidence_to_cloud": bool(cfg.get("policy", {}).get("uncertain_band", {}).get("low_confidence_to_cloud", True)),
        "privacy_content_patterns": list(privacy_content.get("patterns", DEFAULT_CONTENT_PATTERNS)) if privacy_content.get("enabled", True) else [],
        "privacy_content_threshold": int(privacy_content.get("threshold", 1)),
        "cheap_privacy_content_veto_cloud": True,
        "cheap_privacy_topic_abstain": True,
        "fail_closed_on_error": bool(privacy_content.get("fail_closed_on_error", True)),
        "fallback_model": fallback_model,
    }


def apply_lock(project: Path, cfg: dict[str, Any], runtime_name: str, router_model: str, force: bool) -> tuple[bool, str | None, str | None]:
    lock_on = bool(cfg.get("local", {}).get("single_model_lock", True))
    result = enforce_single_model_lock(project, runtime_name, router_model, enabled=lock_on, force=force)
    if not result.get("ok"):
        return False, result.get("error"), None
    fallback = None if lock_on else model_for_runtime(cfg, runtime_name, None, "fallback")
    return True, None, fallback


def warmup(adapter: Any, model: str, calls: int) -> list[float]:
    times: list[float] = []
    for _ in range(max(0, calls)):
        r = adapter.classify(model, "Warmup routing classifier. Return strict JSON.")
        times.append(float(r.latency_ms))
    return times


def collect_live() -> dict[str, Any]:
    return {
        "vram": probe_vram(),
        "host": probe_host(),
        "activity": probe_runtime_activity(),
    }


def collect_snapshot(project: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    ollama_cfg = cfg.get("ollama", {})
    lc = cfg.get("llamacpp", {})
    ollama = probe_ollama(ollama_cfg.get("host", "127.0.0.1"), int(ollama_cfg.get("port", 11434)))
    llamacpp = probe_llamacpp(lc.get("base_url", "http://127.0.0.1:8090"))
    live = collect_live()
    lock = read_lock(project)
    scores = load_gate_scores(project)
    lock_model = str(lock.get("model")) if lock else None
    tips = recommend_local_setup(live["vram"], ollama, llamacpp)
    hint = lock_hint(scores, lock_model)
    if hint:
        tips.append(hint)
    return {
        "profile": hardware_profile(),
        "vram": live["vram"],
        "host": live["host"],
        "activity": live["activity"],
        "ollama": ollama,
        "llamacpp": llamacpp,
        "lock": lock,
        "scores": scores,
        "tips": tips,
    }


def last_benchmark(project: Path) -> dict[str, Any] | None:
    runs = project / "runs"
    if not runs.exists():
        return None
    files = sorted(
        (p for p in runs.glob("benchmark-*.json") if not p.name.startswith("benchmark-card-")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return None
    data = load_json(files[0])
    if not isinstance(data, dict):
        return None
    return {"path": files[0].name, "summary": data.get("summary") or {}}
