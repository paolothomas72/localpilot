from __future__ import annotations

import json
import sys
from pathlib import Path

from localpilot.explain import decision_steps
from localpilot.local_ops import probe_ollama, read_lock
from localpilot.router import route_task
from localpilot.runtime_factory import build_adapter
from localpilot.session import load_config_optional, project_dir, route_kwargs
from localpilot.tui_tests import VARIATION_PACK


def _run_one(project, cfg, runtime: str, model: str) -> list[dict]:
    adapter = build_adapter(cfg, runtime_override=runtime)
    rows = []
    try:
        for item in VARIATION_PACK:
            d = route_task(
                adapter=adapter,
                task=item["prompt"],
                router_model=model,
                **route_kwargs(cfg, project, None),
            )
            d["task"] = item["prompt"]
            d["locked_model"] = f"{runtime} / {model}"
            rows.append(
                {
                    "model": model,
                    "kind": item["kind"],
                    "want": item["label"],
                    "got": d.get("route"),
                    "match": d.get("route") == item["label"],
                    "path": d.get("policy_path"),
                    "cheap": d.get("cheap_confidence"),
                    "abstain": d.get("cheap_abstain"),
                    "abstain_reason": d.get("cheap_abstain_reason"),
                    "llm": d.get("llm_model"),
                    "ms": d.get("latency_ms"),
                    "prompt": item["prompt"],
                    "steps": decision_steps(d),
                }
            )
    finally:
        adapter.close()
    return rows


def main() -> int:
    project = project_dir(".")
    cfg = load_config_optional(project)
    lock = read_lock(project) or {}
    ollama = probe_ollama()
    have = set(ollama.get("models") or [])
    models = []
    if lock.get("model"):
        models.append(str(lock["model"]))
    for extra in ("qwen3:8b", "gemma4:12b"):
        if extra in have and extra not in models:
            models.append(extra)
    if len(sys.argv) > 1:
        models = [sys.argv[1]]
    all_rows = []
    print(f"models {models}")
    for model in models[:2]:
        print(f"\n==== {model} ====")
        rows = _run_one(project, cfg, "ollama", model)
        all_rows.extend(rows)
        for r in rows:
            mark = "OK  " if r["match"] else "MISS"
            llm = r["llm"] or "none"
            ab = "abstain" if r["abstain"] else ""
            print(
                f"{mark}  {r['kind']:11}  want {r['want']:5}  got {str(r['got'] or 'fail'):5}  "
                f"{str(r['path']):28}  cheap={r['cheap']}  {ab}  llm={llm}  {r['ms']}ms"
            )
            print(f"     {r['prompt']}")
        hits = sum(1 for r in rows if r["match"])
        print(f"score  {hits}/{len(rows)}  {model}")
    out = Path(project) / "runs" / "gate-variations.json"
    out.write_text(json.dumps(all_rows, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
