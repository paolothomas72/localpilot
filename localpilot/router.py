from __future__ import annotations

import re
from typing import Any

from .cheap_router import CheapRouter
from .explain import confidence_trust
from .privacy import find_content_matches, privacy_audit_fields
from .runtime_base import ClassificationResult, RuntimeAdapter


def has_risk_keyword(task: str, keywords: list[str]) -> bool:
    lowered = task.lower()
    return any(k.lower() in lowered for k in keywords)


def _keyword_regex(keyword: str) -> str:
    tokens = [re.escape(t) for t in keyword.strip().split() if t.strip()]
    if not tokens:
        return ""
    inner = r"\s+".join(tokens)
    return rf"(?<![a-z0-9_]){inner}(?![a-z0-9_])"


def _keyword_matches(task: str, keyword: str, mode: str) -> bool:
    lowered_task = task.lower()
    lowered_key = keyword.lower().strip()
    if not lowered_key:
        return False
    if mode == "substring":
        return lowered_key in lowered_task
    if mode == "token":
        pattern = _keyword_regex(lowered_key)
        return bool(pattern and re.search(pattern, lowered_task))
    return lowered_key in lowered_task


def _find_matched_keywords(task: str, keywords: list[str], mode: str = "substring") -> list[str]:
    lowered = task.lower()
    matched: list[str] = []
    seen: set[str] = set()
    for k in keywords:
        key = k.lower().strip()
        if key and _keyword_matches(lowered, key, mode) and key not in seen:
            matched.append(k)
            seen.add(key)
    return matched


def _build_audit(
    task: str,
    capability_keywords: list[str],
    capability_phrases: list[str],
    privacy_keywords: list[str],
    capability_threshold: int,
    privacy_threshold: int,
    conflict_mode: str,
    capability_match_mode: str,
    privacy_match_mode: str,
    privacy_content_patterns: list[str] | None = None,
    privacy_content_threshold: int = 1,
) -> dict[str, Any]:
    capability_matches = _find_matched_keywords(task, capability_keywords, capability_match_mode)
    capability_phrase_matches = _find_matched_keywords(task, capability_phrases, "token")
    privacy_matches = _find_matched_keywords(task, privacy_keywords, privacy_match_mode)
    content_matches = find_content_matches(task, privacy_content_patterns)
    privacy_extra = privacy_audit_fields(
        topic_matches=privacy_matches,
        content_matches=content_matches,
        topic_threshold=privacy_threshold,
        content_threshold=privacy_content_threshold,
    )
    capability_score = len(capability_matches) + len(capability_phrase_matches)
    privacy_score = len(privacy_matches)
    capability_triggered = capability_score >= capability_threshold
    privacy_triggered = privacy_extra["privacy_topic_triggered"]
    content_triggered = privacy_extra["privacy_content_triggered"]
    conflict = capability_triggered and (privacy_triggered or content_triggered)
    if content_triggered:
        decision = "force_local"
    elif conflict:
        if conflict_mode == "capability_first":
            decision = "force_cloud"
        elif conflict_mode == "model_decides":
            decision = "defer"
        elif conflict_mode == "privacy_content_first":
            decision = "force_cloud"
        else:
            decision = "force_local"
    elif capability_triggered:
        decision = "force_cloud"
    elif privacy_triggered:
        decision = "force_local"
    else:
        decision = "defer"
    out = {
        "capability_risk_score": capability_score,
        "privacy_risk_score": privacy_score,
        "capability_risk_keywords": capability_matches,
        "capability_risk_phrases": capability_phrase_matches,
        "privacy_risk_keywords": privacy_matches,
        "capability_risk_triggered": capability_triggered,
        "privacy_risk_triggered": privacy_triggered or content_triggered,
        "risk_conflict": conflict,
        "risk_conflict_mode": conflict_mode,
        "risk_axis_decision": decision,
    }
    out.update(privacy_extra)
    return out


def _attach_audit(base: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    out.update(audit)
    out["confidence_trust"] = confidence_trust(out)
    if "llm_model" not in out and out.get("policy_path") in (
        "router_direct",
        "uncertain_band_cloud",
        "low_confidence_escalation",
        "router_failed_then_fallback",
        "router_failed_no_fallback",
        "privacy_fail_closed_local",
    ):
        out["llm_model"] = out.get("selected_model")
    if "model_said" not in out:
        raw = out.get("model_raw")
        if raw:
            out["model_said"] = raw
        elif out.get("error"):
            out["model_said"] = str(out.get("error"))
        else:
            path = str(out.get("policy_path") or "")
            out["model_said"] = f"(no model call) {path}" if path else "(no model output)"
    return out


def route_task(
    adapter: RuntimeAdapter,
    task: str,
    router_model: str,
    fallback_model: str | None,
    confidence_threshold: float,
    risk_keywords: list[str],
    privacy_keywords: list[str] | None = None,
    capability_phrases: list[str] | None = None,
    capability_match_mode: str = "substring",
    privacy_match_mode: str = "substring",
    two_axis_enabled: bool = True,
    capability_threshold: int = 1,
    privacy_threshold: int = 1,
    conflict_mode: str = "privacy_first",
    cheap_router: CheapRouter | None = None,
    cheap_router_threshold: float = 0.85,
    low_confidence_to_cloud: bool = True,
    privacy_content_patterns: list[str] | None = None,
    privacy_content_threshold: int = 1,
    cheap_privacy_content_veto_cloud: bool = True,
    cheap_privacy_topic_abstain: bool = True,
    fail_closed_on_error: bool = True,
) -> dict[str, Any]:
    capability_keys = list(risk_keywords)
    privacy_keys = list(privacy_keywords or [])
    audit = _build_audit(
        task=task,
        capability_keywords=capability_keys,
        capability_phrases=list(capability_phrases or []),
        privacy_keywords=privacy_keys,
        capability_threshold=max(1, int(capability_threshold)),
        privacy_threshold=max(1, int(privacy_threshold)),
        conflict_mode=conflict_mode,
        capability_match_mode=capability_match_mode,
        privacy_match_mode=privacy_match_mode,
        privacy_content_patterns=privacy_content_patterns,
        privacy_content_threshold=privacy_content_threshold,
    )

    if audit.get("privacy_content_triggered"):
        return _attach_audit({
            "route": "local",
            "confidence": 0.99,
            "selected_model": router_model,
            "policy_path": "privacy_content_local_rule",
            "latency_ms": 0.1,
            "ok": True,
            "router_first_route": None,
            "router_first_confidence": None,
            "threshold_applied": False,
        }, audit)

    if two_axis_enabled and audit["risk_axis_decision"] == "force_cloud":
        return _attach_audit({
            "route": "cloud",
            "confidence": 0.99,
            "selected_model": fallback_model or router_model,
            "policy_path": "capability_risk_cloud_rule",
            "latency_ms": 0.1,
            "ok": True,
            "router_first_route": None,
            "router_first_confidence": None,
            "threshold_applied": False,
        }, audit)

    if two_axis_enabled and audit["risk_axis_decision"] == "force_local":
        return _attach_audit({
            "route": "local",
            "confidence": 0.99,
            "selected_model": router_model,
            "policy_path": "privacy_risk_local_rule",
            "latency_ms": 0.1,
            "ok": True,
            "router_first_route": None,
            "router_first_confidence": None,
            "threshold_applied": False,
        }, audit)

    if (not two_axis_enabled) and has_risk_keyword(task, capability_keys):
        return _attach_audit({
            "route": "cloud",
            "confidence": 0.99,
            "selected_model": fallback_model or router_model,
            "policy_path": "risk_keyword_rule",
            "latency_ms": 0.1,
            "ok": True,
            "router_first_route": None,
            "router_first_confidence": None,
            "threshold_applied": False,
        }, audit)

    cheap_confidence = None
    # Phase B fast pre-gate: cheap lexical router decides obvious cases.
    if cheap_router is not None:
        cheap = cheap_router.predict(task)
        cheap_confidence = None if cheap is None else round(float(cheap.confidence), 4)
        audit["cheap_confidence"] = cheap_confidence
        audit["cheap_overlap"] = None if cheap is None else cheap.overlap
        audit["cheap_abstain"] = bool(cheap and cheap.abstain)
        audit["cheap_abstain_reason"] = None if cheap is None else cheap.abstain_reason
        topic_hot = bool(audit.get("privacy_topic_triggered"))
        content_hot = bool(audit.get("privacy_content_triggered"))
        privacy_abstain = bool(cheap_privacy_topic_abstain and topic_hot and not content_hot)
        novel_abstain = bool(cheap and cheap.abstain)
        abstain = privacy_abstain or novel_abstain
        if cheap is not None and cheap.confidence >= cheap_router_threshold and not abstain:
            if cheap.route == "cloud" and cheap_privacy_content_veto_cloud and content_hot:
                pass
            else:
                return _attach_audit({
                    "route": cheap.route,
                    "confidence": round(cheap.confidence, 4),
                    "selected_model": "cheap_router_nb",
                    "policy_path": "cheap_router_direct",
                    "latency_ms": cheap.latency_ms,
                    "ok": True,
                    "router_first_route": None,
                    "router_first_confidence": None,
                    "threshold_applied": False,
                    "model_said": f"cheap gate skipped the LLM  route={cheap.route}  confidence={cheap.confidence:.2f}",
                }, audit)

    first: ClassificationResult = adapter.classify(router_model, task)
    audit["llm_model"] = router_model
    if not first.ok:
        if fail_closed_on_error and (audit.get("privacy_content_triggered") or audit.get("privacy_topic_triggered")):
            return _attach_audit({
                "route": "local",
                "confidence": 0.99,
                "selected_model": router_model,
                "policy_path": "privacy_fail_closed_local",
                "latency_ms": first.latency_ms,
                "ok": True,
                "error": first.error,
                "router_first_route": first.route,
                "router_first_confidence": first.confidence,
                "threshold_applied": False,
                "model_raw": first.raw,
                "model_said": first.raw or first.error or "(empty model output)",
            }, audit)
        if fallback_model:
            second = adapter.classify(fallback_model, task)
            return _attach_audit({
                "route": second.route,
                "confidence": second.confidence,
                "selected_model": fallback_model,
                "policy_path": "router_failed_then_fallback",
                "latency_ms": round(first.latency_ms + second.latency_ms, 2),
                "ok": second.ok,
                "error": second.error if not second.ok else first.error,
                "router_first_route": first.route,
                "router_first_confidence": first.confidence,
                "threshold_applied": False,
                "model_raw": second.raw or first.raw,
                "model_said": second.raw or first.raw or second.error or first.error or "(empty model output)",
            }, audit)
        return _attach_audit({
            "route": None,
            "confidence": None,
            "selected_model": router_model,
            "policy_path": "router_failed_no_fallback",
            "latency_ms": first.latency_ms,
            "ok": False,
            "error": first.error,
            "router_first_route": first.route,
            "router_first_confidence": first.confidence,
            "threshold_applied": False,
            "model_raw": first.raw,
            "model_said": first.raw or first.error or "(empty model output)",
        }, audit)

    conf = first.confidence if isinstance(first.confidence, (int, float)) else 0.0
    if conf < confidence_threshold and low_confidence_to_cloud:
        return _attach_audit({
            "route": "cloud",
            "confidence": conf,
            "selected_model": fallback_model or router_model,
            "policy_path": "uncertain_band_cloud",
            "latency_ms": first.latency_ms,
            "ok": True,
            "router_first_route": first.route,
            "router_first_confidence": first.confidence,
            "threshold_applied": True,
            "model_raw": first.raw,
            "model_said": first.raw or "(empty model output)",
        }, audit)

    if conf < confidence_threshold and fallback_model:
        second = adapter.classify(fallback_model, task)
        return _attach_audit({
            "route": second.route if second.ok else first.route,
            "confidence": second.confidence if second.ok else first.confidence,
            "selected_model": fallback_model if second.ok else router_model,
            "policy_path": "low_confidence_escalation",
            "latency_ms": round(first.latency_ms + second.latency_ms, 2),
            "ok": second.ok,
            "error": second.error if not second.ok else None,
            "router_first_route": first.route,
            "router_first_confidence": first.confidence,
            "threshold_applied": True,
            "model_raw": second.raw or first.raw,
            "model_said": second.raw or first.raw or "(empty model output)",
        }, audit)

    return _attach_audit({
        "route": first.route,
        "confidence": first.confidence,
        "selected_model": router_model,
        "policy_path": "router_direct",
        "latency_ms": first.latency_ms,
        "ok": True,
        "router_first_route": first.route,
        "router_first_confidence": first.confidence,
        "threshold_applied": False,
        "model_raw": first.raw,
        "model_said": first.raw or "(empty model output)",
    }, audit)


def calibrate_threshold(
    rows: list[dict[str, Any]],
    objective: str = "safe",
    max_escalation_rate: float = 0.8,
) -> dict[str, Any]:
    # Uses first-stage router outputs so threshold calibration is independent of cheap/risk shortcuts.
    calibration_rows: list[dict[str, Any]] = []
    for r in rows:
        first_route = r.get("router_first_route")
        first_conf = r.get("router_first_confidence")
        label = r.get("label")
        if (
            first_route in ("local", "cloud")
            and isinstance(first_conf, (int, float))
            and label in ("local", "cloud")
        ):
            calibration_rows.append(
                {
                    "first_route": first_route,
                    "first_confidence": float(first_conf),
                    "label": label,
                }
            )

    if not calibration_rows:
        return {
            "recommended_threshold": 0.88,
            "objective": objective,
            "sample_size": 0,
            "best_metrics": {},
            "sweep": [],
        }

    candidates = [round(x / 100, 2) for x in range(50, 99, 2)]
    best_threshold = 0.88
    best_metrics: dict[str, float] | None = None
    sweep: list[dict[str, float]] = []

    def rank_key(m: dict[str, float]) -> tuple[float, float, float, float]:
        # smaller false-local risk first; then higher accuracy; then lower escalation.
        if objective == "balanced":
            return (m["accuracy"], -m["false_local_rate"], -m["escalation_rate"], m["threshold"])
        return (-m["false_local_rate"], m["accuracy"], -m["escalation_rate"], m["threshold"])

    for t in candidates:
        predicted: list[str] = []
        labels: list[str] = []
        escalations = 0
        for r in calibration_rows:
            first_conf = r["first_confidence"]
            route = r["first_route"]
            if first_conf < t:
                route = "cloud"
                escalations += 1
            predicted.append(route)
            labels.append(r["label"])

        total = len(labels)
        accuracy = sum(1 for p, l in zip(predicted, labels) if p == l) / total
        cloud_labels = sum(1 for l in labels if l == "cloud")
        false_local = sum(1 for p, l in zip(predicted, labels) if p == "local" and l == "cloud")
        false_local_rate = (false_local / cloud_labels) if cloud_labels else 0.0
        escalation_rate = escalations / total
        metrics = {
            "threshold": t,
            "accuracy": round(accuracy, 4),
            "false_local_rate": round(false_local_rate, 4),
            "false_local_count": false_local,
            "cloud_label_count": cloud_labels,
            "escalation_rate": round(escalation_rate, 4),
        }
        sweep.append(metrics)

    allowed = [m for m in sweep if m["escalation_rate"] <= max_escalation_rate]
    ranked_pool = allowed if allowed else sweep
    for metrics in ranked_pool:
        if best_metrics is None or rank_key(metrics) > rank_key(best_metrics):
            best_threshold = float(metrics["threshold"])
            best_metrics = metrics

    return {
        "recommended_threshold": best_threshold,
        "objective": objective,
        "max_escalation_rate": max_escalation_rate,
        "escalation_cap_satisfied": bool(allowed),
        "sample_size": len(calibration_rows),
        "best_metrics": best_metrics or {},
        "sweep": sweep,
    }

