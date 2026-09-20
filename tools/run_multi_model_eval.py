from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    i = int(round((pct / 100.0) * (len(values) - 1)))
    return values[i]


def parse_artifact_path(output: str) -> Path | None:
    m = re.search(r"artifact:\s*(.+)", output)
    if not m:
        return None
    return Path(m.group(1).strip())


def run_benchmark(
    project_dir: Path,
    runtime: str,
    model: str,
    prompts_path: Path,
    warmup_calls: int,
    sleep_ms: int,
    timeout_s: int,
) -> dict[str, Any]:
    cmd = [
        "localpilot",
        "benchmark",
        "--project",
        str(project_dir),
        "--runtime",
        runtime,
        "--model",
        model,
        "--prompts",
        str(prompts_path),
        "--warmup-calls",
        str(warmup_calls),
        "--sleep-ms",
        str(sleep_ms),
    ]
    t0 = time.perf_counter()
    p = subprocess.Popen(
        cmd,
        cwd=project_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = p.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # Kill full process tree on Windows so localpilot/python children do not linger.
        subprocess.run(
            ["taskkill", "/PID", str(p.pid), "/T", "/F"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = p.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            stdout, stderr = "", ""
        out = (stdout or "") + "\n" + (stderr or "")
        return {
            "ok": False,
            "returncode": 124,
            "elapsed_s": round(time.perf_counter() - t0, 2),
            "error": "timeout",
            "output_tail": out[-2000:],
        }

    elapsed_s = round(time.perf_counter() - t0, 2)
    out = (stdout or "") + "\n" + (stderr or "")
    artifact = parse_artifact_path(out)
    if p.returncode != 0 or artifact is None or not artifact.exists():
        return {
            "ok": False,
            "returncode": p.returncode,
            "elapsed_s": elapsed_s,
            "output_tail": out[-2000:],
        }

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    latencies = [float(r["latency_ms"]) for r in payload.get("rows", []) if isinstance(r.get("latency_ms"), (int, float))]
    return {
        "ok": True,
        "returncode": p.returncode,
        "elapsed_s": elapsed_s,
        "artifact": str(artifact),
        "summary": payload.get("summary", {}),
        "latency_p95_ms": round(percentile(latencies, 95), 2) if latencies else None,
        "latency_max_ms": round(max(latencies), 2) if latencies else None,
    }


def aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    ok_runs = [r for r in runs if r.get("ok")]
    timeout_runs = [r for r in runs if r.get("error") == "timeout" or r.get("returncode") == 124]
    failed_runs = [r for r in runs if not r.get("ok")]
    if not ok_runs:
        return {
            "ok_runs": 0,
            "failed_runs": len(failed_runs),
            "timeout_runs": len(timeout_runs),
            "incomplete": True,
        }
    acc = [r["summary"].get("accuracy") for r in ok_runs if isinstance(r["summary"].get("accuracy"), (int, float))]
    avg_latency = [r["summary"].get("avg_latency_ms") for r in ok_runs if isinstance(r["summary"].get("avg_latency_ms"), (int, float))]
    p95 = [r.get("latency_p95_ms") for r in ok_runs if isinstance(r.get("latency_p95_ms"), (int, float))]
    parsed_ratio = []
    router_direct_acc = []
    for r in ok_runs:
        s = r["summary"]
        total = s.get("total")
        parsed = s.get("parsed")
        rda = s.get("router_direct_accuracy")
        if isinstance(total, int) and total > 0 and isinstance(parsed, int):
            parsed_ratio.append(parsed / total)
        if isinstance(rda, (int, float)):
            router_direct_acc.append(rda)
    return {
        "ok_runs": len(ok_runs),
        "failed_runs": len(failed_runs),
        "timeout_runs": len(timeout_runs),
        "incomplete": len(ok_runs) != len(runs),
        "accuracy_mean": round(statistics.mean(acc), 4) if acc else None,
        "accuracy_min": round(min(acc), 4) if acc else None,
        "avg_latency_mean_ms": round(statistics.mean(avg_latency), 2) if avg_latency else None,
        "avg_latency_min_ms": round(min(avg_latency), 2) if avg_latency else None,
        "p95_latency_mean_ms": round(statistics.mean(p95), 2) if p95 else None,
        "parse_rate_mean": round(statistics.mean(parsed_ratio), 4) if parsed_ratio else None,
        "router_direct_accuracy_mean": round(statistics.mean(router_direct_acc), 4) if router_direct_acc else None,
    }


def save_payload(
    out_path: Path,
    prompts_path: Path,
    runs_per_model: int,
    start_index: int,
    end_index: int,
    timeout_s: int,
    results: list[dict[str, Any]],
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prompts_path": str(prompts_path),
        "runs_per_model": runs_per_model,
        "candidate_range": [start_index, end_index],
        "timeout_s": timeout_s,
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def save_heartbeat(
    heartbeat_path: Path,
    candidate_id: int,
    runtime: str,
    model: str,
    run_idx: int,
    runs_per_model: int,
    done_runs: int,
    total_runs: int,
    eta_s: int,
    state: str,
) -> None:
    hb = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "candidate_id": candidate_id,
        "runtime": runtime,
        "model": model,
        "run_index": run_idx,
        "runs_per_model": runs_per_model,
        "progress": f"{done_runs}/{total_runs}",
        "eta_s": eta_s,
    }
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(json.dumps(hb, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeated LocalPilot evaluations across candidate models.")
    parser.add_argument("--project", default=".")
    parser.add_argument("--candidates", default="model-candidates-dataset.json")
    parser.add_argument("--prompts", default="datasets/prompts.expanded.120.json")
    parser.add_argument("--out", default="runs/multi-eval-results.json")
    parser.add_argument("--runs-per-model", type=int, default=3)
    parser.add_argument("--start-index", type=int, default=1, help="1-based start candidate id")
    parser.add_argument("--end-index", type=int, default=10, help="1-based end candidate id")
    parser.add_argument("--timeout-s", type=int, default=900, help="Hard timeout per benchmark run")
    parser.add_argument("--resume", action="store_true", help="Resume from existing out file if present")
    parser.add_argument("--max-consecutive-timeouts", type=int, default=2, help="Skip model after this many consecutive timeouts")
    parser.add_argument("--heartbeat", default="runs/multi-eval-heartbeat.json", help="Live status heartbeat JSON path")
    args = parser.parse_args()

    project_dir = Path(args.project).resolve()
    candidates_path = (project_dir / args.candidates).resolve()
    prompts_path = (project_dir / args.prompts).resolve()
    out_path = (project_dir / args.out).resolve()
    heartbeat_path = (project_dir / args.heartbeat).resolve()

    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))["priority_order"]
    selected = [c for c in candidates if args.start_index <= int(c["id"]) <= args.end_index]

    results: list[dict[str, Any]] = []
    if args.resume and out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        results = existing.get("results", [])

    completed_map: dict[int, int] = {}
    for item in results:
        cid = int(item.get("candidate", {}).get("id", -1))
        if cid >= 0:
            completed_map[cid] = len(item.get("runs", []))

    total_runs = len(selected) * args.runs_per_model
    done_runs = sum(min(completed_map.get(int(c["id"]), 0), args.runs_per_model) for c in selected)
    overall_start = time.perf_counter()

    for c in selected:
        runtime = c["runtime"]
        model = c["model"]
        warmup_calls = 3 if runtime == "llamacpp" else 2
        sleep_ms = 250 if runtime == "llamacpp" else 150
        cid = int(c["id"])
        consecutive_timeouts = 0

        existing_item = next((r for r in results if int(r.get("candidate", {}).get("id", -1)) == cid), None)
        per_runs = existing_item.get("runs", []) if existing_item else []
        already = len(per_runs)
        if already >= args.runs_per_model:
            print(f'[candidate {c["id"]}] {runtime} :: {model} SKIP ({already}/{args.runs_per_model})', flush=True)
            continue

        print(f'[candidate {c["id"]}] {runtime} :: {model}', flush=True)
        for i in range(already, args.runs_per_model):
            elapsed_total = time.perf_counter() - overall_start
            avg_run_s = elapsed_total / done_runs if done_runs else 0.0
            remaining = max(0, total_runs - done_runs)
            eta_s = int(avg_run_s * remaining) if avg_run_s > 0 else 0
            save_heartbeat(
                heartbeat_path=heartbeat_path,
                candidate_id=cid,
                runtime=runtime,
                model=model,
                run_idx=i + 1,
                runs_per_model=args.runs_per_model,
                done_runs=done_runs,
                total_runs=total_runs,
                eta_s=eta_s,
                state="running",
            )
            run = run_benchmark(
                project_dir=project_dir,
                runtime=runtime,
                model=model,
                prompts_path=prompts_path,
                warmup_calls=warmup_calls,
                sleep_ms=sleep_ms,
                timeout_s=args.timeout_s,
            )
            per_runs.append(run)
            done_runs += 1
            elapsed_total = time.perf_counter() - overall_start
            avg_run_s = elapsed_total / done_runs if done_runs else 0.0
            remaining = max(0, total_runs - done_runs)
            eta_s = int(avg_run_s * remaining) if avg_run_s > 0 else 0
            if run.get("error") == "timeout":
                consecutive_timeouts += 1
            else:
                consecutive_timeouts = 0
            print(
                f'  run {i+1}/{args.runs_per_model} ok={run.get("ok")} rc={run.get("returncode")} elapsed_s={run.get("elapsed_s")} progress={done_runs}/{total_runs} eta_s~{eta_s}',
                flush=True,
            )
            save_heartbeat(
                heartbeat_path=heartbeat_path,
                candidate_id=cid,
                runtime=runtime,
                model=model,
                run_idx=i + 1,
                runs_per_model=args.runs_per_model,
                done_runs=done_runs,
                total_runs=total_runs,
                eta_s=eta_s,
                state="running",
            )

            if existing_item:
                existing_item["runs"] = per_runs
                existing_item["aggregate"] = aggregate_runs(per_runs)
            else:
                existing_item = {"candidate": c, "runs": per_runs, "aggregate": aggregate_runs(per_runs)}
                results.append(existing_item)

            save_payload(
                out_path=out_path,
                prompts_path=prompts_path,
                runs_per_model=args.runs_per_model,
                start_index=args.start_index,
                end_index=args.end_index,
                timeout_s=args.timeout_s,
                results=results,
            )

            if consecutive_timeouts >= args.max_consecutive_timeouts:
                print(
                    f"  model_timeout_skip: consecutive_timeouts={consecutive_timeouts} threshold={args.max_consecutive_timeouts}",
                    flush=True,
                )
                break

        if existing_item:
            existing_item["aggregate"] = aggregate_runs(per_runs)

    save_payload(
        out_path=out_path,
        prompts_path=prompts_path,
        runs_per_model=args.runs_per_model,
        start_index=args.start_index,
        end_index=args.end_index,
        timeout_s=args.timeout_s,
        results=results,
    )
    print(f"wrote={out_path}", flush=True)
    heartbeat_path.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "state": "finished",
                "out": str(out_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

