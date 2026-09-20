from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    values = sorted(values)
    idx = int(round((pct / 100.0) * (len(values) - 1)))
    return values[idx]


def to_float(text: str) -> float | None:
    try:
        return float(str(text).strip().replace('"', ""))
    except Exception:
        return None


@dataclass
class TelemetryProcess:
    name: str
    process: subprocess.Popen[str]
    output_path: Path


def start_cpu_mem_logger(out_csv: Path) -> TelemetryProcess:
    # PowerShell sampler avoids locale/format instability from typeperf CSV.
    ps = (
        f"$out='{str(out_csv)}'; "
        "Set-Content -Path $out -Value 'ts,cpu_pct,mem_avail_mb'; "
        "while ($true) { "
        "$cpu=(Get-Counter '\\Processor(_Total)\\% Processor Time').CounterSamples[0].CookedValue; "
        "$mem=(Get-Counter '\\Memory\\Available MBytes').CounterSamples[0].CookedValue; "
        "$ts=(Get-Date).ToString('o'); "
        "Add-Content -Path $out -Value ($ts + ',' + [math]::Round($cpu,2) + ',' + [math]::Round($mem,2)); "
        "Start-Sleep -Milliseconds 1000 }"
    )
    cmd = ["powershell", "-NoProfile", "-Command", ps]
    fh = out_csv.open("w", encoding="utf-8", newline="")
    proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True)
    return TelemetryProcess("cpu_mem", proc, out_csv)


def start_gpu_logger(out_csv: Path) -> TelemetryProcess:
    cmd = [
        "nvidia-smi",
        "--query-gpu=timestamp,utilization.gpu,utilization.memory,memory.used,memory.total",
        "--format=csv,noheader,nounits",
        "--loop-ms=1000",
    ]
    fh = out_csv.open("w", encoding="utf-8", newline="")
    proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True)
    return TelemetryProcess("gpu", proc, out_csv)


def stop_logger(t: TelemetryProcess) -> None:
    if t.process.poll() is None:
        t.process.terminate()
        try:
            t.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            t.process.kill()


def parse_cpu_mem(csv_path: Path) -> dict[str, Any]:
    cpu: list[float] = []
    mem_avail_mb: list[float] = []
    if not csv_path.exists():
        return {"samples": 0}
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            # skip header and blank lines
            if i == 0 or len(row) < 3:
                continue
            c = to_float(row[-2])
            m = to_float(row[-1])
            if c is not None:
                cpu.append(c)
            if m is not None:
                mem_avail_mb.append(m)
    return {
        "samples": len(cpu),
        "cpu_avg_pct": round(statistics.mean(cpu), 2) if cpu else None,
        "cpu_p95_pct": round(percentile(cpu, 95), 2) if cpu else None,
        "cpu_max_pct": round(max(cpu), 2) if cpu else None,
        "mem_avail_avg_mb": round(statistics.mean(mem_avail_mb), 2) if mem_avail_mb else None,
        "mem_avail_min_mb": round(min(mem_avail_mb), 2) if mem_avail_mb else None,
    }


def parse_gpu(csv_path: Path) -> dict[str, Any]:
    gpu_util: list[float] = []
    mem_util: list[float] = []
    mem_used: list[float] = []
    mem_total: list[float] = []
    if not csv_path.exists():
        return {"samples": 0}
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            gu = to_float(parts[1])
            mu = to_float(parts[2])
            used = to_float(parts[3])
            total = to_float(parts[4])
            if gu is not None:
                gpu_util.append(gu)
            if mu is not None:
                mem_util.append(mu)
            if used is not None:
                mem_used.append(used)
            if total is not None:
                mem_total.append(total)
    vram_peak_mb = max(mem_used) if mem_used else None
    vram_total_mb = max(mem_total) if mem_total else None
    vram_peak_pct = (100.0 * vram_peak_mb / vram_total_mb) if (vram_peak_mb is not None and vram_total_mb) else None
    return {
        "samples": len(gpu_util),
        "gpu_util_avg_pct": round(statistics.mean(gpu_util), 2) if gpu_util else None,
        "gpu_util_p95_pct": round(percentile(gpu_util, 95), 2) if gpu_util else None,
        "gpu_util_max_pct": round(max(gpu_util), 2) if gpu_util else None,
        "gpu_mem_util_avg_pct": round(statistics.mean(mem_util), 2) if mem_util else None,
        "vram_peak_mb": round(vram_peak_mb, 2) if vram_peak_mb is not None else None,
        "vram_total_mb": round(vram_total_mb, 2) if vram_total_mb is not None else None,
        "vram_peak_pct": round(vram_peak_pct, 2) if vram_peak_pct is not None else None,
    }


def run_benchmark(project_dir: Path, runtime: str, model: str, warmup_calls: int, sleep_ms: int) -> tuple[int, str]:
    cmd = [
        "localpilot",
        "benchmark",
        "--project",
        str(project_dir),
        "--runtime",
        runtime,
        "--model",
        model,
        "--warmup-calls",
        str(warmup_calls),
        "--sleep-ms",
        str(sleep_ms),
    ]
    p = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    return p.returncode, out


def find_artifact_path(benchmark_output: str) -> Path | None:
    m = re.search(r"artifact:\s*(.+)", benchmark_output)
    if not m:
        return None
    return Path(m.group(1).strip())


def hard_critic_assessment(summary: dict[str, Any], telemetry: dict[str, Any], runtime: str) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []

    parse_ok = summary.get("parsed") == summary.get("total")
    if not parse_ok:
        findings.append({"severity": "CRITICAL", "issue": "Parse reliability below 100%", "fix": "Tighten JSON prompting / parser fallback"})

    acc = summary.get("accuracy")
    if isinstance(acc, (int, float)) and acc < 0.85:
        findings.append({"severity": "HIGH", "issue": f"Accuracy low ({acc})", "fix": "Calibrate threshold + add risk rules + tune router model"})

    lat = summary.get("avg_latency_ms")
    if isinstance(lat, (int, float)) and lat > 1800:
        findings.append({"severity": "HIGH", "issue": f"Latency high ({lat} ms)", "fix": "Reduce max tokens / switch router model / warm runtime"})

    gpu_p95 = telemetry.get("gpu", {}).get("gpu_util_p95_pct")
    if isinstance(gpu_p95, (int, float)) and gpu_p95 > 95:
        findings.append({"severity": "MEDIUM", "issue": f"GPU saturation risk (p95 {gpu_p95}%)", "fix": "Throttle concurrency / smaller quant / batching policy"})

    vram_peak = telemetry.get("gpu", {}).get("vram_peak_pct")
    if isinstance(vram_peak, (int, float)) and vram_peak > 92:
        findings.append({"severity": "HIGH", "issue": f"VRAM headroom too low ({vram_peak}%)", "fix": "Lower quant or context / unload unused models"})

    overall = "PASS"
    if any(f["severity"] == "CRITICAL" for f in findings):
        overall = "FAIL"
    elif any(f["severity"] == "HIGH" for f in findings):
        overall = "WARN"
    return {"runtime": runtime, "overall": overall, "findings": findings}


def write_report(out_dir: Path, results: list[dict[str, Any]]) -> Path:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report_path = out_dir / f"hard-critic-live-report-{now.replace(':', '').replace('-', '')}.md"
    lines = [
        "# LocalPilot Live Test Pack Report",
        "",
        f"- Generated: `{now}`",
        "",
        "## Runtime Results",
        "",
    ]
    for r in results:
        s = r["benchmark"]["summary"]
        t = r["telemetry"]
        hc = r["hard_critic"]
        lines.extend(
            [
                f"### {r['runtime']}",
                f"- Model: `{r['model']}`",
                f"- Accuracy: `{s.get('accuracy')}`",
                f"- Avg latency ms: `{s.get('avg_latency_ms')}`",
                f"- Parse: `{s.get('parsed')}/{s.get('total')}`",
                f"- CPU avg/p95/max %: `{t['cpu_mem'].get('cpu_avg_pct')}` / `{t['cpu_mem'].get('cpu_p95_pct')}` / `{t['cpu_mem'].get('cpu_max_pct')}`",
                f"- RAM avail min MB: `{t['cpu_mem'].get('mem_avail_min_mb')}`",
                f"- GPU avg/p95/max %: `{t['gpu'].get('gpu_util_avg_pct')}` / `{t['gpu'].get('gpu_util_p95_pct')}` / `{t['gpu'].get('gpu_util_max_pct')}`",
                f"- VRAM peak: `{t['gpu'].get('vram_peak_mb')}` MB (`{t['gpu'].get('vram_peak_pct')}%`)",
                f"- Hard critic verdict: `{hc['overall']}`",
            ]
        )
        if hc["findings"]:
            lines.append("- Findings:")
            for f in hc["findings"]:
                lines.append(f"  - [{f['severity']}] {f['issue']} -> {f['fix']}")
        else:
            lines.append("- Findings: none")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def append_dataset_row(path: Path, runtime: str, model: str, summary: dict[str, Any], telemetry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "timestamp",
        "runtime",
        "model",
        "accuracy",
        "avg_latency_ms",
        "parsed",
        "total",
        "cpu_avg_pct",
        "cpu_p95_pct",
        "gpu_avg_pct",
        "gpu_p95_pct",
        "vram_peak_pct",
    ]
    row = [
        datetime.now(timezone.utc).isoformat(),
        runtime,
        model,
        summary.get("accuracy"),
        summary.get("avg_latency_ms"),
        summary.get("parsed"),
        summary.get("total"),
        telemetry.get("cpu_mem", {}).get("cpu_avg_pct"),
        telemetry.get("cpu_mem", {}).get("cpu_p95_pct"),
        telemetry.get("gpu", {}).get("gpu_util_avg_pct"),
        telemetry.get("gpu", {}).get("gpu_util_p95_pct"),
        telemetry.get("gpu", {}).get("vram_peak_pct"),
    ]
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(header)
        w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LocalPilot benchmarks with live CPU/RAM/GPU telemetry.")
    parser.add_argument("--project", default=".")
    parser.add_argument("--outdir", default="runs/live-test-pack")
    parser.add_argument("--ollama-model", default="gemma4:12b")
    parser.add_argument(
        "--llamacpp-model",
        default=r"C:\Users\paolo\AppData\Local\llama.cpp\models\Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
    )
    parser.add_argument("--skip-ollama", action="store_true")
    parser.add_argument("--skip-llamacpp", action="store_true")
    parser.add_argument("--runtime", choices=["ollama", "llamacpp"], default=None, help="Run only one runtime")
    parser.add_argument("--model", default=None, help="Override model for the selected runtime")
    parser.add_argument("--warmup-calls", type=int, default=2, help="Warmup requests before each benchmark")
    parser.add_argument("--sleep-ms", type=int, default=150, help="Pause between tasks to reduce utilization spikes")
    parser.add_argument("--dataset-csv", default="runs/model-dataset.csv", help="Append summary row for each run")
    args = parser.parse_args()

    project_dir = Path(args.project).resolve()
    out_dir = (project_dir / args.outdir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    runs: list[tuple[str, str]] = []
    if args.runtime:
        if args.runtime == "ollama":
            runs.append(("ollama", args.model or args.ollama_model))
        else:
            runs.append(("llamacpp", args.model or args.llamacpp_model))
    else:
        if not args.skip_ollama:
            runs.append(("ollama", args.ollama_model))
        if not args.skip_llamacpp:
            runs.append(("llamacpp", args.llamacpp_model))

    all_results: list[dict[str, Any]] = []
    for runtime, model in runs:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        cpu_file = out_dir / f"{runtime}-{stamp}-cpu-mem.csv"
        gpu_file = out_dir / f"{runtime}-{stamp}-gpu.csv"

        cpu_logger = start_cpu_mem_logger(cpu_file)
        gpu_logger = start_gpu_logger(gpu_file)
        time.sleep(1.5)
        try:
            rc, output = run_benchmark(project_dir, runtime, model, args.warmup_calls, args.sleep_ms)
        finally:
            stop_logger(cpu_logger)
            stop_logger(gpu_logger)

        artifact = find_artifact_path(output)
        if rc != 0 or artifact is None or not artifact.exists():
            fail = {
                "runtime": runtime,
                "model": model,
                "error": "benchmark_failed",
                "return_code": rc,
                "output_tail": output[-1200:],
                "telemetry": {"cpu_mem": parse_cpu_mem(cpu_file), "gpu": parse_gpu(gpu_file)},
            }
            all_results.append(fail)
            continue

        bench = json.loads(artifact.read_text(encoding="utf-8"))
        telemetry = {"cpu_mem": parse_cpu_mem(cpu_file), "gpu": parse_gpu(gpu_file)}
        critic = hard_critic_assessment(bench["summary"], telemetry, runtime)
        run_payload = {
            "runtime": runtime,
            "model": model,
            "benchmark_artifact": str(artifact),
            "benchmark": bench,
            "telemetry": telemetry,
            "hard_critic": critic,
        }
        all_results.append(run_payload)
        append_dataset_row(
            (project_dir / args.dataset_csv).resolve(),
            runtime=runtime,
            model=model,
            summary=bench["summary"],
            telemetry=telemetry,
        )

    summary_path = out_dir / "live-test-pack-summary.json"
    summary_path.write_text(json.dumps({"runs": all_results}, indent=2), encoding="utf-8")
    report_path = write_report(out_dir, [r for r in all_results if "benchmark" in r])
    print(f"summary_json={summary_path}")
    print(f"report_md={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

