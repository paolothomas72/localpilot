from __future__ import annotations

from typing import Any


PATH_REASONS = {
    "capability_risk_cloud_rule": "Capability-risk rule forced cloud (hard / high-stakes task signals).",
    "privacy_risk_local_rule": "Privacy topic rule kept this local.",
    "privacy_content_local_rule": "Real secret/PII-shaped content was detected; fail-closed to local.",
    "privacy_fail_closed_local": "Runtime failed, but content looked sensitive, so it stayed local.",
    "cheap_router_direct": "Cheap lexical gate was confident enough to skip the LLM.",
    "uncertain_band_cloud": "Router confidence was below threshold; treated as uncertain and sent to cloud.",
    "low_confidence_escalation": "Low confidence triggered a fallback model classify.",
    "router_direct": "LLM router decided directly.",
    "router_failed_then_fallback": "Primary router failed; fallback model answered.",
    "router_failed_no_fallback": "Router failed and no fallback was allowed (single-model lock or none configured).",
    "risk_keyword_rule": "Legacy risk-keyword rule forced cloud.",
}


def confidence_trust(decision: dict[str, Any]) -> str:
    path = str(decision.get("policy_path") or "")
    if path.endswith("_rule") or path in ("cheap_router_direct", "privacy_content_local_rule", "privacy_fail_closed_local"):
        return "rule"
    conf = decision.get("confidence")
    first = decision.get("router_first_confidence", conf)
    if path == "uncertain_band_cloud":
        return "uncertain"
    if isinstance(first, (int, float)) and abs(float(first) - 0.95) < 1e-9:
        return "uncalibrated"
    if isinstance(conf, (int, float)) and abs(float(conf) - 0.95) < 1e-9:
        return "uncalibrated"
    return "model"


def decision_steps(decision: dict[str, Any]) -> list[str]:
    """Walk the gates in order. This is the measurable LocalPilot output."""
    path = str(decision.get("policy_path") or "")
    axis = str(decision.get("risk_axis_decision") or "defer")
    locked = decision.get("locked_model") or decision.get("reply_model") or decision.get("selected_model")
    steps = [f"1  lock     candidate is {locked or 'none'}"]

    if decision.get("privacy_content_triggered"):
        steps.append("2  secrets   HIT  PII-shaped content  -> stop here, stay LOCAL")
    else:
        steps.append("2  secrets   skip  no secret-shaped content")

    caps = decision.get("capability_risk_keywords") or []
    phrases = decision.get("capability_risk_phrases") or []
    topics = decision.get("privacy_risk_keywords") or decision.get("privacy_topic_keywords") or []
    if axis == "force_cloud":
        steps.append(f"3  hard-task HIT  {caps or phrases or 'capability'}  -> CLOUD, no model classify")
    elif axis == "force_local":
        steps.append(f"3  privacy   HIT  {topics or 'privacy topic'}  -> LOCAL, no model classify")
    else:
        extra = ""
        if caps or phrases:
            extra = f"  saw {caps or phrases} but below threshold"
        steps.append(f"3  rules     skip  not forced{extra}")

    if path == "cheap_router_direct":
        steps.append(
            f"4  cheap     HIT  {decision.get('route')} @ {decision.get('confidence')}  -> skip the LLM"
        )
    elif path.endswith("_rule") or path == "privacy_content_local_rule":
        steps.append("4  cheap     skip  a rule already decided")
    elif decision.get("cheap_abstain"):
        steps.append(
            f"4  cheap     ABSTAIN  {decision.get('cheap_confidence')}  "
            f"{decision.get('cheap_abstain_reason') or 'novel/hard'}  ask the locked LLM"
        )
    else:
        cheap_c = decision.get("cheap_confidence")
        steps.append(
            f"4  cheap     skip  below threshold"
            + (f" (got {cheap_c})" if cheap_c is not None else "")
            + "  ask the locked LLM"
        )

    llm = decision.get("llm_model") or decision.get("selected_model") or locked
    if path in ("router_direct", "uncertain_band_cloud", "low_confidence_escalation", "router_failed_then_fallback", "router_failed_no_fallback", "privacy_fail_closed_local"):
        first = decision.get("router_first_route")
        conf = decision.get("router_first_confidence", decision.get("confidence"))
        if decision.get("ok") is False or path.startswith("router_failed"):
            steps.append(f"5  llm       FAIL  {llm}  {decision.get('error') or 'classify failed'}")
        else:
            steps.append(f"5  llm       {llm}  answered {first or decision.get('route')} @ {conf}")
    else:
        steps.append("5  llm       not called")

    if path == "uncertain_band_cloud":
        steps.append("6  uncertain HIT  confidence below threshold  -> CLOUD")
    elif path == "low_confidence_escalation":
        steps.append(f"6  uncertain HIT  tried fallback {decision.get('selected_model')}")
    else:
        steps.append("6  uncertain skip")

    steps.append(
        f"7  result    {decision.get('route') or 'fail'}   {path or '?'}   {decision.get('latency_ms')}ms"
    )
    return steps


GATE_NAMES = ("LOCK", "SECRETS", "RULES", "CHEAP", "LLM", "UNCERTAIN", "RESULT")


def gate_panel(decision: dict[str, Any] | None) -> str:
    """Right-rail walk of the seven gates. Empty until a Route turn exists."""
    lines = ["HOW DECIDED", ""]
    if not decision:
        for i, name in enumerate(GATE_NAMES, 1):
            lines.append(f"{i}  {name:<10}  waiting")
        return "\n".join(lines)
    steps = decision_steps(decision)
    for i, (name, step) in enumerate(zip(GATE_NAMES, steps), 1):
        body = step.split(None, 2)
        detail = body[2] if len(body) >= 3 else step
        lines.append(f"{i}  {name:<10}  {detail}")
    return "\n".join(lines)


def runtime_label(runtime: str) -> str:
    key = (runtime or "").lower().replace(" ", "")
    if key in ("llamacpp", "llama.cpp", "llama"):
        return "llama.cpp"
    if key == "ollama":
        return "Ollama"
    return runtime or "none"


def model_meta(runtime: str, model: str) -> str:
    return (
        f"routed by\n"
        f"  {runtime_label(runtime)}\n"
        f"\n"
        f"model\n"
        f"  {model or 'none locked'}"
    )


def model_result_block(
    decision: dict[str, Any] | None,
    pending: bool = False,
    runtime: str = "",
    model: str = "",
) -> str:
    if pending:
        who = f"{runtime_label(runtime)} / {model}" if model else "no model locked"
        return f"loading request\n\n{who}\nwalking which way it routes"
    if not decision:
        return "waiting\n\ntype on the left\nLOCAL or CLOUD shows here"
    route = str(decision.get("route") or "fail").upper()
    path = str(decision.get("policy_path") or "")
    reason = PATH_REASONS.get(path, path or "no path")
    lines = [route, "", reason]
    ms = decision.get("latency_ms")
    if ms is not None:
        lines.append(f"{ms} ms")
    said = decision.get("model_said")
    if said:
        lines.append("")
        lines.append(str(said))
    if decision.get("error"):
        lines.append("")
        lines.append(str(decision.get("error")))
    return "\n".join(lines)


def model_bubble(decision: dict[str, Any]) -> str:
    """What the model 'said' in the chat — the routing answer, not generate()."""
    route = str(decision.get("route") or "fail").upper()
    locked = decision.get("locked_model") or decision.get("selected_model") or "localpilot"
    said = decision.get("model_said")
    path = str(decision.get("policy_path") or "")
    reason = PATH_REASONS.get(path, path or "no path")
    lines = [f"MODEL  {locked}", route, reason]
    if said:
        lines.append("")
        lines.append(str(said))
    if decision.get("error"):
        lines.append("")
        lines.append(str(decision.get("error")))
    return "\n".join(lines)


def explain_decision(decision: dict[str, Any]) -> str:
    route = decision.get("route") or "none"
    path = str(decision.get("policy_path") or "unknown")
    trust = decision.get("confidence_trust") or confidence_trust(decision)
    reason = PATH_REASONS.get(path, f"Policy path `{path}`.")
    lines = [
        f"route: {route}",
        f"why: {reason}",
        f"path: {path}",
        f"confidence: {decision.get('confidence')} ({trust})",
        f"latency_ms: {decision.get('latency_ms')}",
        f"selected_model: {decision.get('selected_model')}",
    ]
    caps = decision.get("capability_risk_keywords") or []
    phrases = decision.get("capability_risk_phrases") or []
    topics = decision.get("privacy_risk_keywords") or decision.get("privacy_topic_keywords") or []
    if caps or phrases:
        lines.append(f"capability: keywords={caps} phrases={phrases}")
    if topics:
        lines.append(f"privacy_topic: {topics}")
    if decision.get("privacy_content_triggered"):
        lines.append("privacy_content: triggered (fail-closed local)")
    if decision.get("risk_conflict"):
        lines.append(f"conflict: {decision.get('risk_conflict_mode')} -> {decision.get('risk_axis_decision')}")
    if trust == "uncalibrated":
        lines.append("note: model confidence looks like a favorite float (0.95), not a calibrated probability.")
    if trust == "uncertain":
        lines.append("note: uncertain band treated this as cloud instead of trusting a weak local call.")
    if decision.get("error"):
        lines.append(f"error: {decision.get('error')}")
    return "\n".join(lines)


HELP_TEXT = """\
/models            pick Ollama or llama.cpp, then a model
/models ollama     jump straight to Ollama models
/models llamacpp   jump straight to llama.cpp models
/route             open Route
/about             what LocalPilot is
/theme copper|rgb|ink   or /theme color to cycle
/update            version / how updates will work
/export            write the last decision to runs/last-decision.json
plain text         send to the locked model — LOCAL or CLOUD on the right
"""

def about_text(version: str) -> str:
    return f"LocalPilot  {version}\n\n" + ABOUT_BODY


def update_text(version: str) -> str:
    return f"LocalPilot  {version}\n\n" + UPDATE_BODY


ABOUT_BODY = """\
LocalPilot is a local-first decision gate.

Stay local unless the task actually needs the cloud.
It walks seven gates and answers LOCAL or CLOUD.
It is not a generate() harness. OpenCode / Hermes still own file tools.

1-6    Machine Models Route Runs Testing Errors
w      warmup
k n m  cheap / uncertain / lock
b      warmup-on-route

CLI also has: look init doctor models recommend
warmup unlock probe route benchmark calibrate
version about update export

No phone-home. Paste `localpilot doctor --json` in a GitHub issue if you want to share hardware.
"""

UPDATE_BODY = """\
Public repo: https://github.com/paolothomas72/localpilot

This command does not phone home. It only prints the local version.
Check GitHub for newer tags when they exist.

You are on 0.1.0.
"""


def notes_blurb(version: str) -> str:
    return (
        f"LocalPilot  {version}\n"
        "Ollama + llama.cpp  ·  seven gates  ·  6 pages\n"
        "\n"
        "[#c45c4a]do not use the AMD iGPU[/]\n"
        "\n"
        "[#a89888]click for full release notes[/]"
    )


def release_notes(version: str) -> str:
    return (
        f"LocalPilot  {version}  ·  release notes\n"
        "\n"
        "STANDING\n"
        "  do not use the AMD iGPU for inference\n"
        "  do not kill llama-server on :8090\n"
        "  LocalPilot is a gate, not a generate() harness\n"
        "\n"
        "WHAT IT COMES WITH\n"
        "  Ollama + llama.cpp lock\n"
        "  seven gates → LOCAL or CLOUD\n"
        "  pages 1-6  Machine Models Route Runs Testing Errors\n"
        "  CLI  look init doctor models recommend warmup unlock\n"
        "       probe route benchmark calibrate version about update export\n"
        "\n"
        "0.1.0  first public post\n"
        "  https://github.com/paolothomas72/localpilot\n"
        "  no phone-home — hardware reports are opt-in issues\n"
        "  Route is YOU left / MODEL right\n"
        "\n"
        "Esc or close to go back"
    )
