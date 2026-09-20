from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
CANDIDATES_PATH = PROJECT_DIR / "model-candidates-dataset.json"
DATASET_CSV = PROJECT_DIR / "runs" / "top10-dataset.csv"
SUMMARY_JSON = PROJECT_DIR / "runs" / "top10-summary.json"


def run_one(runtime: str, model: str) -> dict:
    cmd = [
        "python",
        "tools\\run_test_pack_live.py",
        "--project",
        ".",
        "--runtime",
        runtime,
        "--model",
        model,
        "--dataset-csv",
        str(DATASET_CSV.relative_to(PROJECT_DIR)),
        "--warmup-calls",
        "3" if runtime == "llamacpp" else "2",
        "--sleep-ms",
        "250" if runtime == "llamacpp" else "150",
    ]
    started = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed_s = round(time.perf_counter() - started, 2)
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return {
        "runtime": runtime,
        "model": model,
        "returncode": proc.returncode,
        "elapsed_s": elapsed_s,
        "output_tail": combined[-2000:],
    }


def main() -> int:
    if DATASET_CSV.exists():
        DATASET_CSV.unlink()

    data = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    candidates = data["priority_order"]
    results = []

    for c in candidates:
        runtime = c["runtime"]
        model = c["model"]
        print(f'[{c["id"]}/10] runtime={runtime} model={model}')
        res = run_one(runtime=runtime, model=model)
        results.append({"candidate": c, "run": res})
        print(f'  rc={res["returncode"]} elapsed={res["elapsed_s"]}s')

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_csv": str(DATASET_CSV),
        "results": results,
    }
    SUMMARY_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"summary_json={SUMMARY_JSON}")
    print(f"dataset_csv={DATASET_CSV}")
    failed = sum(1 for r in results if r["run"]["returncode"] != 0)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

