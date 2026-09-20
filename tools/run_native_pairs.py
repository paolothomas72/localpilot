from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_models import family_of, kind_of
from localpilot.explain import decision_steps
from localpilot.local_ops import probe_ollama
from localpilot.router import route_task
from localpilot.runtime_factory import build_adapter
from localpilot.session import load_config_optional, project_dir, route_kwargs
from localpilot.tui_tests import VARIATION_PACK

PACK = [
    next(p for p in VARIATION_PACK if "Rename tmp" in p["prompt"]),
    next(p for p in VARIATION_PACK if "Threat-model" in p["prompt"]),
    next(p for p in VARIATION_PACK if "blank repo" in p["prompt"]),
    next(p for p in VARIATION_PACK if "five years" in p["prompt"]),
    next(p for p in VARIATION_PACK if "sells experiences" in p["prompt"]),
    next(p for p in VARIATION_PACK if "million readers" in p["prompt"]),
]
PER_MODEL_S = 90
TOTAL_S = 900


def _route_pack(cfg, project, model: str) -> list[dict]:
    adapter = build_adapter(cfg, runtime_override="ollama")
    rows = []
    try:
        for item in PACK:
            print(f"    prompt  {item['kind']}  {item['prompt'][:64]}", flush=True)
            d = route_task(
                adapter=adapter,
                task=item["prompt"],
                router_model=model,
                **route_kwargs(cfg, project, None),
            )
            d["task"] = item["prompt"]
            d["locked_model"] = f"ollama / {model}"
            rows.append(
                {
                    "model": model,
                    "family": family_of(model),
                    "role": kind_of(model),
                    "kind": item["kind"],
                    "want": item["label"],
                    "got": d.get("route"),
                    "match": d.get("route") == item["label"],
                    "path": d.get("policy_path"),
                    "cheap": d.get("cheap_confidence"),
                    "abstain": d.get("cheap_abstain"),
                    "llm": d.get("llm_model"),
                    "ms": d.get("latency_ms"),
                    "prompt": item["prompt"][:80],
                    "steps": decision_steps(d),
                }
            )
    finally:
        adapter.close()
    return rows


def main() -> int:
    project = project_dir(".")
    cfg = load_config_optional(project)
    names = list(probe_ollama().get("models") or [])
    natives = [n for n in names if kind_of(n) == "native"]
    native_fams = {family_of(n) for n in natives}
    wrappers = [n for n in names if kind_of(n) in ("wrapper", "finetune") and family_of(n) in native_fams]
    queue = natives + [w for w in wrappers if w not in natives]
    print(f"queue  {len(natives)} native + {len(wrappers)} paired wrapper/ft", flush=True)
    for n in queue:
        print(f"  {kind_of(n):9}  {n}", flush=True)
    started = time.time()
    all_rows = []
    for i, model in enumerate(queue, start=1):
        if time.time() - started > TOTAL_S:
            print(f"TOTAL cap  stop before {model}", flush=True)
            break
        print(f"\nCURRENT  {i}/{len(queue)}  {kind_of(model)}  {model}", flush=True)
        t0 = time.time()
        try:
            rows = _route_pack(cfg, project, model)
        except Exception as exc:  # noqa: BLE001
            print(f"CRASH  {model}  {exc}", flush=True)
            rows = []
        elapsed = time.time() - t0
        hits = sum(1 for r in rows if r.get("match"))
        tag = "TIMEOUT" if elapsed > PER_MODEL_S else "done"
        print(f"{tag}  {hits}/{len(rows) or 0}  {elapsed:.1f}s  {model}", flush=True)
        all_rows.extend(rows)
    out = Path(project) / "runs" / "native-pairs.json"
    out.write_text(json.dumps(all_rows, indent=2), encoding="utf-8")
    print(f"\nwrote {out}", flush=True)
    print("-- scores --", flush=True)
    by: dict[str, list[dict]] = {}
    for r in all_rows:
        by.setdefault(r["model"], []).append(r)
    for model, rows in by.items():
        hits = sum(1 for r in rows if r["match"])
        print(f"{hits}/{len(rows)}  {kind_of(model):9}  {model}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
