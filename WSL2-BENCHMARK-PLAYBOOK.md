# LocalPilot WSL2 Benchmark Playbook

Goal: run LocalPilot on Linux/WSL2 with **repeatable latency** and **honest cross-OS comparison**. King95 context: Ryzen 9700X (8c/16t), RTX 5070 (~12 GB VRAM).

Current code gaps (do not ignore):

- `tools/run_test_pack_live.py` CPU/RAM sampler is **PowerShell `Get-Counter`** — dead on WSL/Linux.
- `tools/run_multi_model_eval.py` timeout kill is **`taskkill /T /F`** — dead on WSL/Linux.
- GPU sampler (`nvidia-smi --loop-ms=1000`) works in WSL **only if** CUDA-on-WSL is healthy.

Keep the standing llama.cpp server on **port 8090** alive unless Paolo asks to stop it.

---

## 1) WSL2 setup and tuning checklist

Do this **once per bench day**, then snapshot the env into the run artifact.

### Host Windows (controls WSL more than the guest kernel)

```powershell
# High Performance (or Ultimate Performance if present)
powercfg /getactivescheme
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c

# Optional Ultimate Performance clone
powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61

# Confirm no battery/saver profile on a desktop
powercfg /a
```

- Disable core parking / min processor state = 100% in that plan (Power Options → Processor power management).
- Close noisy host apps: browsers, Discord, Steam, game overlays, Windows Update, Defender **full scan**, OneDrive sync storms.
- One inference stack only: either Windows Ollama/`llama-server` **or** WSL copies — not both fighting the 5070.

### `.wslconfig` (`C:\Users\paolo\.wslconfig`)

Pin memory. Dynamic balloon + reclaim is the #1 WSL latency jitter source.

```ini
[wsl2]
memory=24GB
processors=12
swap=0
localhostForwarding=true
nestedVirtualization=false

[experimental]
autoMemoryReclaim=disabled
sparseVhd=true
```

Then:

```powershell
wsl --shutdown
wsl -d Ubuntu   # or your distro
```

24 GB is a starting point on a 32+ GB host. Leave **≥12 GB** for Windows + GPU driver. If the host starts paging, **lower** WSL `memory`, do not add WSL swap.

### Guest Linux

```bash
# Distro + kernel
uname -a
cat /proc/version
systemd-detect-virt   # should look like wsl/microsoft

# CPU governor (often cosmetic in WSL; still record it)
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor | sort | uniq
# If writable (native Linux / custom kernel):
# echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

# Swap must be off for benches
swapon --show
free -h

# Quiet guest daemons (skip anything you actually need)
sudo systemctl stop unattended-upgrades snapd packagekit 2>/dev/null || true
```

**CPU governor truth:** WSL2 frequency is dominated by the **Windows power plan + Ryzen CPPC**, not `cpupower` in the guest. Treat guest governor as a label, not a control.

### GPU passthrough caveats

- Install/update the **Windows NVIDIA driver** only. Do **not** install a Linux `nvidia-driver` package inside WSL.
- CUDA userspace in WSL (toolkit / `libcuda`) is fine; kernel driver lives on Windows.

```bash
nvidia-smi
echo $CUDA_VISIBLE_DEVICES
ls /dev/dxg          # WSL GPU device node
```

- VRAM is **shared with Windows**. A host Chrome tab or a Windows Ollama model will show up as VRAM used.
- NVML clocks / persistence mode / `nvidia-smi -pm 1` are incomplete or no-ops in WSL. Do not tune clocks from the guest.
- For GPU-metric comparability, sample with **Windows `nvidia-smi`** even when inference runs in WSL (same device, same NVML).

### Filesystem (non-negotiable)

Copy the project + GGUF/Ollama blobs onto the **Linux ext4 volume**, not `/mnt/c`.

```bash
# Bad: /mnt/c/Users/paolo/paseo-tool-workspaces/cursor/localpilot
# Good:
rsync -a --exclude runs /mnt/c/Users/paolo/paseo-tool-workspaces/cursor/localpilot/ ~/localpilot/
# Models: ~/.ollama or ~/models on ext4
```

`/mnt/c` (9p/drvfs) adds multi-ms I/O and mmap jitter that will dominate load and sometimes decode.

### LocalPilot bring-up

```bash
cd ~/localpilot
python3 -m pip install -e .
# Point config at WSL loopback; do not keep Windows GGUF paths
# llamacpp.base_url = http://127.0.0.1:8090
# ollama host/port   = 127.0.0.1:11434
localpilot probe --project . --runtime ollama
# or, if llama-server is the Windows one with localhostForwarding:
localpilot probe --project . --runtime llamacpp --model "<server_model_name>"
```

If llama-server stays on Windows:8090 and LocalPilot runs in WSL, that is a **valid hybrid**. Tag it `runtime_location=windows_server,client=wsl`. Do not compare it unlabeled against an in-WSL server.

---

## 2) Benchmark hygiene protocol

Defaults already in-repo (keep them):

| Runtime   | `--warmup-calls` | `--sleep-ms` | Per-run cap |
|-----------|------------------|--------------|-------------|
| ollama    | 2                | 150          | 900s (15 min hard) |
| llamacpp  | 3                | 250          | 900s (15 min hard) |

Roster rule: at 15:00 kill **that trial’s** `localpilot`/Ollama child only. Do not kill `:8090` llama-server. If every trial for a model hits 15:00, skip the model.

### Isolation

1. One model loaded. `ollama stop` everything else. One `llama-server`.
2. No other LocalPilot / OpenCode / Cursor battery on the same GPU.
3. No compile jobs, no Docker Desktop extra stacks, no browser GPU.
4. Confirm idle:

```bash
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv
# expect near-idle util, VRAM = this model only
```

### Pinning (9700X)

Leave 2 threads for WSL/Windows + telemetry.

```bash
# 12 logical CPUs for the server
taskset -c 0-11 llama-server ...     # or
taskset -c 0-11 ollama serve
# client/CLI can float
taskset -c 12-15 localpilot benchmark ...
```

If the server is **Windows-native**, set affinity there (`Start-Process` / Task Manager) to the same idea: 12 threads compute, 4 leftover. Do not pin WSL **and** Windows servers onto overlapping busy cores without measuring.

`isolcpus` is not available on stock WSL kernels. `taskset` is the practical lever.

### Warmup and run shape

```bash
# Single artifact
localpilot benchmark --project . --runtime ollama --model "<model>" \
  --prompts datasets/splits/public_test.json \
  --warmup-calls 2 --sleep-ms 150

# Repeatability campaign (n=3)
python tools/run_multi_model_eval.py --project . \
  --prompts datasets/splits/public_test.json \
  --runs-per-model 3 --start-index 1 --end-index 1 \
  --timeout-s 900 --resume --out runs/multi-eval-wsl2.json
```

- Warmup calls are **unscored**. Do not lower them on WSL; kernel/driver/mmap need them more, not less.
- Discard or tag a cold-start extra run (`run0_cold`) if the model was just loaded.
- `--sleep-ms` reduces back-to-back VRAM/util spikes so p95 GPU is not a sampling artifact.

### Repeatability checks (gate)

For the same model + split + flags, across 3 scored runs:

| Check | Pass |
|-------|------|
| Parse | `parsed == total` every run |
| Accuracy | identical, or Δ ≤ 1 prompt |
| `avg_latency_ms` CV | `(stdev/mean) ≤ 0.10` |
| Per-run `TIMEOUT` | 0 (else invalid for latency compare) |
| VRAM peak | within ~300 MB |

If CV > 10%: re-idle, confirm swap=0 / reclaim=disabled / no host steal, then **one** extra triplet. If still high, mark `JITTER_INVALID` — do not average it into a Windows vs WSL table.

---

## 3) Telemetry on Linux/WSL2

Match the existing live-pack schema so parsers stay unchanged:

**CPU/RAM CSV:** `ts,cpu_pct,mem_avail_mb`  
**GPU CSV:** `nvidia-smi` columns already used by `parse_gpu()`.

### Sampling interval

- **1.0 s** for published benches (same as Windows `Get-Counter` + `nvidia-smi --loop-ms=1000`).
- 250–500 ms GPU **only** for debugging bursts. Never mix intervals in a comparison set.
- Router calls are short (often <2 s). 1 s samples are for **run-level pressure**, not per-prompt GPU %. Trust `latency_ms` on the row for that.

### CPU / RAM (guest)

```bash
# Drop-in replacement for the PowerShell loop
OUT=runs/live-test-pack/wsl-cpu-mem.csv
echo 'ts,cpu_pct,mem_avail_mb' > "$OUT"
# First /proc/stat read is a baseline
prev=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9, $5}' /proc/stat)
while :; do
  sleep 1
  read tot idle <<<$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9, $5}' /proc/stat)
  read ptot pidle <<<"$prev"
  dt=$((tot-ptot)); di=$((idle-pidle))
  cpu=$(awk -v dt="$dt" -v di="$di" 'BEGIN{if(dt<=0) print 0; else printf "%.2f", (100*(dt-di))/dt}')
  mem=$(awk '/MemAvailable/{printf "%.2f", $2/1024}' /proc/meminfo)
  ts=$(date -Iseconds)
  echo "$ts,$cpu,$mem" >> "$OUT"
  prev="$tot $idle"
done
```

`cpu_pct` here is **guest busy%**, not Windows `_Total`. `mem_avail_mb` is **WSL VM MemAvailable**, not host Available MBytes.

Optional: `mpstat 1` or `vmstat 1` as a human sidecar; do not replace the CSV schema.

### GPU / VRAM

```bash
# Prefer this from Windows while WSL infers (same NVML as native benches)
nvidia-smi --query-gpu=timestamp,utilization.gpu,utilization.memory,memory.used,memory.total \
  --format=csv,noheader,nounits --loop-ms=1000
```

In-guest `nvidia-smi` is OK for ops; for **Win vs WSL tables**, always use the **host** sampler.

Add process-level VRAM if more than one NVIDIA user exists:

```bash
nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv,noheader
```

### What to attach on every WSL run

```bash
{
  echo "host_os=windows"
  echo "guest_os=$(. /etc/os-release; echo $PRETTY_NAME)"
  echo "wsl=$(uname -r)"
  echo "power_plan=<from powercfg>"
  echo "wsl_memory=$(free -m | awk '/Mem:/{print $2}')"
  echo "wsl_swap=$(free -m | awk '/Swap:/{print $2}')"
  nvidia-smi --query-gpu=driver_version,name,memory.total --format=csv,noheader
} > runs/env-$(date -u +%Y%m%dT%H%M%SZ).txt
```

Until `run_test_pack_live.py` grows a Linux branch, start the two loggers **around** `localpilot benchmark`, then reuse `parse_cpu_mem` / `parse_gpu`.

---

## 4) Cross-platform comparability (Windows native vs WSL2)

Compare **decisions and end-to-end latency**. Do not compare raw CPU% or RAM available across OS.

### Same-run contract (all must match)

- Prompt file (`datasets/splits/public_test.json` or named split)
- Model id + quant + `n_ctx` + `max_tokens` + temperature 0
- Runtime (`ollama` vs `llamacpp`) and **where the server lives**
- `--warmup-calls`, `--sleep-ms`, `--timeout-s`
- Loaded-model exclusivity (no second model in VRAM)
- GPU sampler: host `nvidia-smi` @ 1 s
- n = 3 scored repeats after warmup

### Comparable fields

| Field | Comparable? | Notes |
|-------|-------------|--------|
| `accuracy`, `accuracy_parsed_only`, parse counts | Yes | Primary quality |
| `policy_path_counts`, risk-axis fields | Yes | Policy, not OS |
| Row `latency_ms` p50 / p95 / max | Yes | Best Win↔WSL metric |
| `avg_latency_ms` (mean of means) | Weak | Prefer p50 of run-means |
| `vram_peak_mb` (host NVML) | Yes | Same GPU |
| `gpu_util_*` (host NVML) | Mostly | Short calls undersample |
| `cpu_avg_pct` / `cpu_p95_pct` | **No** | Guest vs `_Total` |
| `mem_avail_*` | **No** | Different memory domains |

### How to label a pair

`pair_id = {split, model, runtime, quant, n_ctx, warmup, sleep}`  
`os_side = windows_native | wsl2_client_win_server | wsl2_full`

Only `windows_native` vs `wsl2_full` is a runtime-OS comparison.  
`wsl2_client_win_server` measures **client/interop overhead** on an otherwise identical Windows server — useful, but different question.

### Reporting rule

Publish:

1. Quality: accuracy + parse (must match or the pair is invalid).
2. Latency: p50 and p95 of **row** `latency_ms`, plus CV across 3 run-means.
3. Capacity: host VRAM peak %.
4. Footer: env snapshot + `JITTER_INVALID` if CV gate failed.

Do not “normalize” CPU% to invent a common scale. If you need host CPU for both sides, sample **Windows `Get-Counter`** during the WSL run too (host steal is a real confounder).

### Hybrid localhost

`localhostForwarding=true` makes WSL `127.0.0.1:8090` hit Windows llama-server. Extra NAT hop is usually small vs decode, but **tag it**. Mirrored networking changes that hop — do not mix networking modes in one comparison week.

---

## 5) Top 8 failure modes and quick remedies

1. **Live telemetry empty / CPU stuck at 0**  
   Cause: PowerShell sampler on Linux.  
   Fix: use the `/proc` loop above; keep `nvidia-smi` for GPU.

2. **Timeout leaves zombies; next trial is contaminated**  
   Cause: `taskkill` no-op on WSL.  
   Fix: `kill -- -"$PGID"` or `pkill -f "localpilot benchmark"`; `ollama stop <model>`. Never kill `:8090` unless asked. Then start the next trial.

3. **Latency CV explodes, VRAM/CPU sawtooth**  
   Cause: `autoMemoryReclaim` or WSL swap.  
   Fix: `autoMemoryReclaim=disabled`, `swap=0`, `wsl --shutdown`, re-run the triplet.

4. **`nvidia-smi` missing or 0% GPU / OOM with “free” VRAM**  
   Cause: no CUDA-on-WSL, or Windows process owns VRAM.  
   Fix: update Windows NVIDIA driver; `ls /dev/dxg`; `nvidia-smi` on **host**; quit host GPU users; unload extra Ollama models.

5. **Load times huge, decode jitter, random stalls**  
   Cause: project or GGUF on `/mnt/c`.  
   Fix: copy to `~/localpilot` + ext4 model dir; re-probe.

6. **Port fight / two brains on one GPU**  
   Cause: Windows Ollama **and** WSL Ollama, or two llama-servers.  
   Fix: one listener on 11434 and one on 8090 total. `ss -lntp | egrep '11434|8090'` in WSL **and** `netstat -ano` on Windows.

7. **First run fast/slow outlier, later runs stabilize**  
   Cause: cold kernels, graph compile, page cache, clocks.  
   Fix: keep warmup 2/3; optional discarded cold run; do not publish run1 alone.

8. **Windows-native vs WSL “CPU 3× worse” or “RAM cliff”**  
   Cause: comparing incomparable gauges, or host power-saver / Defender scan.  
   Fix: compare row latency + host VRAM only; set High Performance; re-check `.wslconfig`; sample host CPU if you need steal evidence.

---

## Copy-paste bench day

```powershell
# Host
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
# confirm .wslconfig (memory pinned, swap=0, autoMemoryReclaim=disabled)
wsl --shutdown
```

```bash
# Guest
cd ~/localpilot
free -h && swapon --show && nvidia-smi
ollama stop --all 2>/dev/null || true
localpilot probe --project . --runtime ollama --model "<model>"
# start host nvidia-smi logger + guest cpu/mem logger, then:
localpilot benchmark --project . --runtime ollama --model "<model>" \
  --prompts datasets/splits/public_test.json --warmup-calls 2 --sleep-ms 150
# repeat x3 or use run_multi_model_eval.py --timeout-s 900
```

Gate: parse 100%, accuracy stable, latency CV ≤ 10%, no TIMEOUT. Else `JITTER_INVALID`.
