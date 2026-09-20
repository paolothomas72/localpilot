from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

from . import __version__
from .core import init_workspace, load_json, save_json, summarize_rows
from .explain import about_text, explain_decision, update_text
from .local_ops import (
    clear_lock,
    hardware_profile,
    is_hold_path,
    probe_llamacpp,
    probe_ollama,
    probe_vram,
    read_lock,
    recommend_local_setup,
)
from .router import calibrate_threshold, route_task
from .runtime_factory import build_adapter, model_for_runtime
from .session import (
    apply_lock as _apply_lock,
    load_config as _load_config,
    load_config_optional as _load_config_optional,
    page_is_openable,
    project_dir as _project_dir,
    route_kwargs as _route_kwargs,
    warmup as _warmup,
)
from .ui import (
    frame,
    render_doctor,
    render_lock_block,
    render_look,
    render_models,
    render_plain_error,
    render_recommend,
    render_route,
    render_warmup,
)


def _open_app(
    page: str = "machine",
    project: str | None = None,
    route_text: str | None = None,
    auto_warmup: bool = False,
    auto_unlock: bool = False,
) -> int | None:
    if not page_is_openable():
        return None
    from .app import run_app

    return run_app(
        project=project,
        page=page,
        route_text=route_text,
        auto_warmup=auto_warmup,
        auto_unlock=auto_unlock,
    )


def cmd_look(args: argparse.Namespace) -> int:
    opened = _open_app("machine", getattr(args, "project", None))
    if opened is not None:
        return opened
    print(render_look())
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    info = init_workspace(_project_dir(args.project))
    print("Initialized LocalPilot workspace:")
    for k, v in info.items():
        print(f"- {k}: {v}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    project_dir = _project_dir(args.project)
    cfg = _load_config_optional(project_dir)
    ollama = probe_ollama(cfg.get("ollama", {}).get("host", "127.0.0.1"), int(cfg.get("ollama", {}).get("port", 11434)))
    llamacpp = probe_llamacpp(cfg.get("llamacpp", {}).get("base_url", "http://127.0.0.1:8090"))
    vram = probe_vram()
    payload = {
        "profile": hardware_profile(),
        "vram": vram,
        "ollama": ollama,
        "llamacpp": llamacpp,
        "lock": read_lock(project_dir),
        "tips": recommend_local_setup(vram, ollama, llamacpp),
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, default=str))
        return 0 if (ollama.get("alive") or llamacpp.get("alive")) else 2
    opened = _open_app("machine", str(project_dir))
    if opened is not None:
        return opened
    print(frame(render_doctor(payload), fill_screen=True))
    return 0 if (ollama.get("alive") or llamacpp.get("alive")) else 2


def cmd_models(args: argparse.Namespace) -> int:
    project_dir = _project_dir(args.project)
    opened = _open_app("models", str(project_dir))
    if opened is not None:
        return opened
    cfg = _load_config_optional(project_dir)
    ollama = probe_ollama(cfg.get("ollama", {}).get("host", "127.0.0.1"), int(cfg.get("ollama", {}).get("port", 11434)))
    llamacpp = probe_llamacpp(cfg.get("llamacpp", {}).get("base_url", "http://127.0.0.1:8090"))
    print(frame(render_models(ollama, llamacpp, read_lock(project_dir)), fill_screen=True))
    return 0


def cmd_recommend(args: argparse.Namespace) -> int:
    project_dir = _project_dir(args.project)
    opened = _open_app("machine", str(project_dir))
    if opened is not None:
        return opened
    cfg = _load_config_optional(project_dir)
    ollama = probe_ollama(cfg.get("ollama", {}).get("host", "127.0.0.1"), int(cfg.get("ollama", {}).get("port", 11434)))
    llamacpp = probe_llamacpp(cfg.get("llamacpp", {}).get("base_url", "http://127.0.0.1:8090"))
    vram = probe_vram()
    print(frame(render_recommend(hardware_profile(), vram, recommend_local_setup(vram, ollama, llamacpp)), fill_screen=True))
    return 0


def cmd_warmup(args: argparse.Namespace) -> int:
    project_dir = _project_dir(args.project)
    cfg = _load_config(project_dir)
    runtime_name = (args.runtime or cfg.get("runtime") or "ollama").lower()
    model = model_for_runtime(cfg, runtime_name, args.model, "router")
    ok, err, _ = _apply_lock(project_dir, cfg, runtime_name, model, force=bool(getattr(args, "force_model", False)))
    if not ok:
        opened = _open_app("models", str(project_dir))
        if opened is None:
            print(frame(render_lock_block(err or "lock blocked this call"), fill_screen=True))
        return 2
    opened = _open_app("machine", str(project_dir), auto_warmup=True)
    if opened is not None:
        return opened
    calls = int(args.calls or cfg.get("local", {}).get("warmup_calls", 1))
    adapter = build_adapter(cfg, runtime_override=runtime_name)
    try:
        times = _warmup(adapter, model, calls)
    finally:
        adapter.close()
    print(frame(render_warmup(runtime_name, model, times), fill_screen=True))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    project_dir = _project_dir(args.project)
    cfg = _load_config(project_dir)
    runtime_name = (args.runtime or cfg.get("runtime") or "ollama").lower()
    model = model_for_runtime(cfg, runtime_name, args.model, "router")
    ok, err, _ = _apply_lock(project_dir, cfg, runtime_name, model, force=bool(getattr(args, "force_model", False)))
    if not ok:
        print(frame(render_lock_block(err or "lock blocked this call"), fill_screen=True))
        return 2
    adapter = build_adapter(cfg, runtime_override=runtime_name)
    tests = [
        "Rename one variable in a single file.",
        "Diagnose intermittent production deadlock.",
        "Review auth token logic for security holes.",
    ]
    rows = []
    try:
        warmup_calls = int(cfg.get("local", {}).get("warmup_calls", 1)) if cfg.get("local", {}).get("warmup_on_route", True) else 0
        _warmup(adapter, model, warmup_calls)
        for task in tests:
            r = adapter.classify(model, task)
            rows.append({"task": task, "ok": r.ok, "route": r.route, "confidence": r.confidence, "latency_ms": r.latency_ms, "error": r.error})
            print(f"ok={r.ok} route={r.route} conf={r.confidence} latency_ms={r.latency_ms} task={task}")
    finally:
        adapter.close()
    ok_count = sum(1 for r in rows if r["ok"])
    print(f"probe_result: {ok_count}/{len(rows)} successful")
    print(f"hardware_profile: {hardware_profile()}")
    return 0 if ok_count == len(rows) else 2


def cmd_route(args: argparse.Namespace) -> int:
    project_dir = _project_dir(args.project)
    cfg = _load_config(project_dir)
    runtime_name = (args.runtime or cfg.get("runtime") or "ollama").lower()
    router_model = model_for_runtime(cfg, runtime_name, args.model, "router")
    ok, err, fallback_model = _apply_lock(project_dir, cfg, runtime_name, router_model, force=bool(args.force_model))
    if not ok:
        opened = _open_app("models", str(project_dir))
        if opened is None:
            print(frame(render_lock_block(err or "lock blocked this call"), fill_screen=True))
        return 2
    if not args.json:
        opened = _open_app("route", str(project_dir), route_text=args.task)
        if opened is not None:
            return opened
    adapter = build_adapter(cfg, runtime_override=runtime_name)
    kwargs = _route_kwargs(cfg, project_dir, fallback_model)
    try:
        if cfg.get("local", {}).get("warmup_on_route", True):
            _warmup(adapter, router_model, int(cfg.get("local", {}).get("warmup_calls", 1)))
        out = route_task(
            adapter=adapter,
            task=args.task,
            router_model=router_model,
            **kwargs,
        )
    finally:
        adapter.close()
    if args.json:
        print(out)
    else:
        print(frame(render_route(explain_decision(out), out.get("route")), fill_screen=True))
    return 0 if out.get("ok") else 2


def cmd_benchmark(args: argparse.Namespace) -> int:
    project_dir = _project_dir(args.project)
    cfg = _load_config(project_dir)
    runtime_name = (args.runtime or cfg.get("runtime") or "ollama").lower()
    router_model = model_for_runtime(cfg, runtime_name, args.model, "router")
    ok, err, fallback_model = _apply_lock(project_dir, cfg, runtime_name, router_model, force=bool(getattr(args, "force_model", False)))
    if not ok:
        print(frame(render_lock_block(err or "lock blocked this call"), fill_screen=True))
        return 2
    prompts_path = Path(args.prompts).resolve() if args.prompts else (project_dir / "prompts.seed.json")
    if is_hold_path(prompts_path):
        print("HOLD note: scoring hold is allowed; cheap-router/calibrate still must not train on it.")
    prompts = load_json(prompts_path)
    adapter = build_adapter(cfg, runtime_override=runtime_name)
    kwargs = _route_kwargs(cfg, project_dir, fallback_model)
    rows: list[dict[str, Any]] = []
    try:
        _warmup(adapter, router_model, max(0, int(args.warmup_calls)))
        for item in prompts:
            decision = route_task(
                adapter=adapter,
                task=item["prompt"],
                router_model=router_model,
                **kwargs,
            )
            row = {
                "id": item["id"],
                "prompt": item["prompt"],
                "label": item.get("label"),
                "route": decision.get("route"),
                "confidence": decision.get("confidence"),
                "confidence_trust": decision.get("confidence_trust"),
                "router_first_route": decision.get("router_first_route"),
                "router_first_confidence": decision.get("router_first_confidence"),
                "threshold_applied": decision.get("threshold_applied"),
                "capability_risk_score": decision.get("capability_risk_score"),
                "privacy_risk_score": decision.get("privacy_risk_score"),
                "capability_risk_keywords": decision.get("capability_risk_keywords"),
                "capability_risk_phrases": decision.get("capability_risk_phrases"),
                "privacy_risk_keywords": decision.get("privacy_risk_keywords"),
                "privacy_content_triggered": decision.get("privacy_content_triggered"),
                "privacy_topic_triggered": decision.get("privacy_topic_triggered"),
                "capability_risk_triggered": decision.get("capability_risk_triggered"),
                "privacy_risk_triggered": decision.get("privacy_risk_triggered"),
                "risk_conflict": decision.get("risk_conflict"),
                "risk_conflict_mode": decision.get("risk_conflict_mode"),
                "risk_axis_decision": decision.get("risk_axis_decision"),
                "latency_ms": decision.get("latency_ms"),
                "policy_path": decision.get("policy_path"),
                "selected_model": decision.get("selected_model"),
                "ok": decision.get("ok"),
                "error": decision.get("error"),
            }
            rows.append(row)
            print(f'{item["id"]:>2} route={row["route"]} conf={row["confidence"]} latency={row["latency_ms"]}ms path={row["policy_path"]}')
            if int(args.sleep_ms) > 0:
                time.sleep(float(args.sleep_ms) / 1000.0)
    finally:
        adapter.close()

    summary = summarize_rows(rows)
    policy_counts: dict[str, int] = {}
    axis_decision_counts: dict[str, int] = {}
    conflict_count = 0
    for r in rows:
        key = str(r.get("policy_path") or "unknown")
        policy_counts[key] = policy_counts.get(key, 0) + 1
        axis_key = str(r.get("risk_axis_decision") or "unknown")
        axis_decision_counts[axis_key] = axis_decision_counts.get(axis_key, 0) + 1
        if r.get("risk_conflict") is True:
            conflict_count += 1
    direct_rows = [r for r in rows if r.get("policy_path") == "router_direct"]
    direct_acc = (
        sum(1 for r in direct_rows if r.get("label") == r.get("route")) / len(direct_rows)
        if direct_rows
        else None
    )
    payload = {
        "summary": {
            "runtime": runtime_name,
            "router_model": router_model,
            "fallback_model": fallback_model,
            "hardware_profile": hardware_profile(),
            "single_model_lock": bool(cfg.get("local", {}).get("single_model_lock", True)),
            "total": summary.total,
            "parsed": summary.parsed,
            "accuracy": round(summary.accuracy, 4) if summary.accuracy is not None else None,
            "accuracy_parsed_only": round(summary.accuracy_parsed_only, 4) if summary.accuracy_parsed_only is not None else None,
            "avg_latency_ms": round(summary.avg_latency_ms, 2) if summary.avg_latency_ms is not None else None,
            "local_count": summary.local_count,
            "cloud_count": summary.cloud_count,
            "ok_count": summary.ok_count,
            "failed_count": summary.failed_count,
            "policy_path_counts": policy_counts,
            "risk_axis_decision_counts": axis_decision_counts,
            "risk_conflict_count": conflict_count,
            "router_direct_accuracy": round(direct_acc, 4) if direct_acc is not None else None,
        },
        "rows": rows,
    }
    run_name = f"benchmark-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path = project_dir / "runs" / run_name
    save_json(out_path, payload)
    print("\nsummary:", payload["summary"])
    print("artifact:", out_path)
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    bench_path = Path(args.benchmark).resolve()
    if is_hold_path(bench_path) and not args.allow_hold:
        print(frame(render_plain_error("HOLD freeze: refusing to calibrate from a hold artifact. Pass --allow-hold only if you intend to break that rule."), fill_screen=True))
        return 2
    data = load_json(bench_path)
    rows = data.get("rows", [])
    rec = calibrate_threshold(rows, objective=args.objective, max_escalation_rate=float(args.max_escalation_rate))
    if args.out:
        save_json(Path(args.out).resolve(), rec)
    if args.apply and args.project:
        project_dir = _project_dir(args.project)
        cfg = _load_config(project_dir)
        cfg.setdefault("policy", {})["confidence_threshold"] = float(rec["recommended_threshold"])
        save_json(project_dir / "localpilot.config.json", cfg)
    print(rec)
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def cmd_about(args: argparse.Namespace) -> int:
    print(about_text(__version__))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    print(update_text(__version__))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .model_board import load_route_history

    project_dir = _project_dir(args.project)
    history = load_route_history(project_dir, limit=1)
    if not history:
        print("no route history yet  ·  run a task first")
        return 1
    out = Path(args.out) if getattr(args, "out", None) else project_dir / "runs" / "last-decision.json"
    save_json(out, history[0])
    print(f"wrote {out}")
    return 0


def cmd_unlock(args: argparse.Namespace) -> int:
    project_dir = _project_dir(args.project)
    opened = _open_app("models", str(project_dir), auto_unlock=True)
    if opened is not None:
        return opened
    clear_lock(project_dir)
    print("single-model lock cleared")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="localpilot", description="Local-first model routing CLI")
    p.add_argument("--project", default=".")
    p.set_defaults(func=cmd_look)
    sub = p.add_subparsers(dest="cmd", required=False)

    s_look = sub.add_parser("look", help="Open the LocalPilot terminal app")
    s_look.add_argument("--project", default=".")
    s_look.set_defaults(func=cmd_look)

    s_init = sub.add_parser("init", help="Initialize LocalPilot config and seed prompts")
    s_init.add_argument("--project", default=".")
    s_init.set_defaults(func=cmd_init)

    s_doctor = sub.add_parser("doctor", help="Check local runtimes, VRAM headroom, and lock state")
    s_doctor.add_argument("--project", default=".")
    s_doctor.add_argument("--json", action="store_true", help="Print a local hardware snapshot. Nothing is uploaded.")
    s_doctor.set_defaults(func=cmd_doctor)

    s_models = sub.add_parser("models", help="List loaded and available local models")
    s_models.add_argument("--project", default=".")
    s_models.set_defaults(func=cmd_models)

    s_rec = sub.add_parser("recommend", help="Recommend a local-model setup for this machine")
    s_rec.add_argument("--project", default=".")
    s_rec.set_defaults(func=cmd_recommend)

    s_warm = sub.add_parser("warmup", help="Warm the selected router so the first real call is not cold")
    s_warm.add_argument("--project", default=".")
    s_warm.add_argument("--runtime", default=None, choices=["ollama", "llamacpp"])
    s_warm.add_argument("--model", default=None)
    s_warm.add_argument("--calls", type=int, default=None)
    s_warm.add_argument("--force-model", action="store_true")
    s_warm.set_defaults(func=cmd_warmup)

    s_unlock = sub.add_parser("unlock", help="Clear the single-model lock")
    s_unlock.add_argument("--project", default=".")
    s_unlock.set_defaults(func=cmd_unlock)

    s_probe = sub.add_parser("probe", help="Probe selected model and runtime health")
    s_probe.add_argument("--project", default=".")
    s_probe.add_argument("--runtime", default=None, choices=["ollama", "llamacpp"], help="Override runtime for this command")
    s_probe.add_argument("--model", default=None)
    s_probe.add_argument("--force-model", action="store_true")
    s_probe.set_defaults(func=cmd_probe)

    s_route = sub.add_parser("route", help="Route one task")
    s_route.add_argument("task")
    s_route.add_argument("--project", default=".")
    s_route.add_argument("--runtime", default=None, choices=["ollama", "llamacpp"], help="Override runtime for this command")
    s_route.add_argument("--model", default=None)
    s_route.add_argument("--json", action="store_true", help="Print raw decision dict instead of explanation")
    s_route.add_argument("--force-model", action="store_true", help="Replace the single-model lock")
    s_route.set_defaults(func=cmd_route)

    s_bench = sub.add_parser("benchmark", help="Run benchmark on prompt set with labels")
    s_bench.add_argument("--project", default=".")
    s_bench.add_argument("--runtime", default=None, choices=["ollama", "llamacpp"], help="Override runtime for this command")
    s_bench.add_argument("--prompts", default=None, help="Path to JSON prompts with id,prompt,label")
    s_bench.add_argument("--model", default=None)
    s_bench.add_argument("--warmup-calls", type=int, default=1, help="Unscored warmup calls before benchmark")
    s_bench.add_argument("--sleep-ms", type=int, default=0, help="Pause between tasks to smooth resource peaks")
    s_bench.add_argument("--force-model", action="store_true")
    s_bench.set_defaults(func=cmd_benchmark)

    s_cal = sub.add_parser("calibrate", help="Recommend confidence threshold from benchmark artifact")
    s_cal.add_argument("benchmark", help="Path to benchmark JSON artifact")
    s_cal.add_argument("--objective", choices=["safe", "balanced"], default="safe", help="safe=minimize false-local risk; balanced=maximize accuracy then risk")
    s_cal.add_argument("--max-escalation-rate", type=float, default=0.8, help="Upper bound for threshold-triggered escalations (0..1).")
    s_cal.add_argument("--out", default=None, help="Optional output path for calibration JSON")
    s_cal.add_argument("--project", default=".", help="Project path when using --apply")
    s_cal.add_argument("--apply", action="store_true", help="Apply recommended threshold into localpilot.config.json")
    s_cal.add_argument("--allow-hold", action="store_true", help="Override HOLD freeze (not recommended)")
    s_cal.set_defaults(func=cmd_calibrate)

    s_ver = sub.add_parser("version", help="Print LocalPilot version")
    s_ver.set_defaults(func=cmd_version)

    s_about = sub.add_parser("about", help="What LocalPilot is")
    s_about.set_defaults(func=cmd_about)

    s_upd = sub.add_parser("update", help="Check for a newer release")
    s_upd.set_defaults(func=cmd_update)

    s_exp = sub.add_parser("export", help="Write the last route decision to JSON")
    s_exp.add_argument("--project", default=".")
    s_exp.add_argument("--out", default=None)
    s_exp.set_defaults(func=cmd_export)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
