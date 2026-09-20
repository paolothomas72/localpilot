from __future__ import annotations

import json
import time
from pathlib import Path

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent))
from classify_models import family_of, kind_of
from localpilot.explain import decision_steps
from localpilot.local_ops import probe_ollama
from localpilot.router import route_task
from localpilot.runtime_factory import build_adapter
from localpilot.session import load_config_optional, project_dir, route_kwargs
from localpilot.tui_tests import VARIATION_PACK

SCOPE_PACK = [p for p in VARIATION_PACK if p["kind"] in ("easy-new", "hard-new", "hard-known", "hard-novel")]
GEMMA_HARD = [p for p in VARIATION_PACK if p["kind"] in ("hard-new", "hard-novel", "hard-known")]
SWEEP_PACK = [
    VARIATION_PACK[4],  # rename tmp
    VARIATION_PACK[5],  # blank repo company
    VARIATION_PACK[10],  # career five years
    VARIATION_PACK[8],  # threat-model
]
PER_MODEL_S = 90
TOTAL_S = 900


def _route_pack(cfg, project, model: str, pack: list[dict]) -> list[dict]:
    adapter = build_adapter(cfg, runtime_override="ollama")
    rows = []
    try:
        for item in pack:
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


def _print(rows: list[dict], title: str) -> None:
    print(f"\n==== {title} ====")
    for r in rows:
        mark = "OK  " if r["match"] else "MISS"
        print(
            f"{mark}  {r['kind']:11}  want {r['want']:5}  got {str(r['got'] or 'fail'):5}  "
            f"{str(r['path']):28}  llm={r['llm'] or 'none'}  {r['ms']}ms"
        )
        print(f"     {r['prompt']}")
    hits = sum(1 for r in rows if r["match"])
    print(f"score  {hits}/{len(rows)}")


def main() -> int:
    project = project_dir(".")
    cfg = load_config_optional(project)
    ollama = probe_ollama()
    names = list(ollama.get("models") or [])
    inventory = [{"name": n, "family": family_of(n), "kind": kind_of(n)} for n in names]
    print("inventory")
    for row in inventory:
        print(f"  {row['kind']:12}  {row['family']:22}  {row['name']}")

    print("\n-- gemma4:12b novel-hard --")
    gemma = _route_pack(cfg, project, "gemma4:12b", GEMMA_HARD)
    _print(gemma, "gemma4:12b hard pack")

    routable = [n for n in names if kind_of(n) in ("native", "wrapper", "finetune")]
    started = time.time()
    sweep = []
    skipped = [r for r in inventory if r["kind"].startswith("skip")]
    print(f"\n-- sweep {len(routable)} routable, skip {len(skipped)} --")
    for model in routable:
        if time.time() - started > TOTAL_S:
            print(f"TOTAL cap, stop before {model}")
            break
        t0 = time.time()
        try:
            rows = _route_pack(cfg, project, model, SWEEP_PACK)
        except Exception as exc:  # noqa: BLE001
            rows = [{"model": model, "kind": "error", "want": None, "got": None, "match": False, "path": "crash", "ms": 0, "prompt": str(exc), "llm": None, "cheap": None, "abstain": None, "steps": []}]
        if time.time() - t0 > PER_MODEL_S:
            print(f"TIMEOUT  {model}")
        hits = sum(1 for r in rows if r.get("match"))
        print(f"{hits}/{len(rows)}  {kind_of(model):9}  {model}")
        sweep.extend(rows)

    out = Path(project) / "runs" / "model-scope.json"
    payload = {"inventory": inventory, "gemma_hard": gemma, "sweep": sweep}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")

    print("\n-- pair view --")
    by_fam: dict[str, list[str]] = {}
    for r in inventory:
        if r["kind"] in ("native", "wrapper", "finetune"):
            by_fam.setdefault(r["family"], []).append(f"{r['kind']}:{r['name']}")
    for fam, members in sorted(by_fam.items()):
        if len(members) > 1:
            print(f"  {fam}")
            for m in members:
                print(f"    {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
