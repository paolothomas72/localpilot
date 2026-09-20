# LocalPilot (v0.1, Ollama-first)

Local-first router. It walks seven gates and answers **LOCAL** or **CLOUD**. It is not a generate() harness. OpenCode / Hermes still own file tools.

```powershell
python -m pip install -e .
localpilot
```

That opens the TUI. `localpilot route "your task"` works without the TUI.

This v0.1 build talks to Ollama and llama.cpp on localhost. If those run in Docker or Podman and publish the ports, LocalPilot already works. It does not start containers.

## Privacy

LocalPilot does not phone home. No analytics, no crash reporter, no hardware upload.

If you want to help optimize other PCs, open a [Hardware report](https://github.com/paolothomas72/localpilot/issues/new?template=hardware.yml) and paste `localpilot doctor --json`. That is opt-in. Redact model names if you want.

## Goals

- Prove local routing can be fast, reliable, and measurable.
- Keep privacy local by default.
- Produce benchmark artifacts you can compare over time.

## Quick Start

From this folder:

```powershell
python -m pip install -e .
localpilot init --project .
localpilot probe --project .
localpilot route --project . "Diagnose intermittent production deadlock in Postgres."
localpilot benchmark --project .
```

Use `llama.cpp` runtime explicitly:

```powershell
localpilot probe --project . --runtime llamacpp --model "<llama_server_model_name>"
localpilot route --project . --runtime llamacpp "Review auth token changes for security issues."
localpilot benchmark --project . --runtime llamacpp --model "<llama_server_model_name>"
```

To calibrate threshold from a benchmark output:

```powershell
localpilot calibrate runs\benchmark-YYYYMMDDTHHMMSSZ.json --objective safe --max-escalation-rate 0.8 --out runs\calibration.json
localpilot calibrate runs\benchmark-YYYYMMDDTHHMMSSZ.json --objective safe --max-escalation-rate 0.8 --project . --apply
```

Run live benchmark pack with CPU/RAM/GPU telemetry + hard-critic report:

```powershell
python tools\run_test_pack_live.py --project .
```

Create expanded dataset and grouped benchmark splits (DEV / PUBLIC / PRIVATE):

```powershell
python tools\generate_expanded_dataset.py
python tools\split_dataset.py
```

Generate a realistic tiered prompt pack (`very_small`, `medium_small`, `heavy_small`) and split it:

```powershell
python tools\generate_realistic_prompt_pack.py --out datasets\promptpack-realistic-v1.json --seed 42
python tools\split_dataset.py --in datasets\promptpack-realistic-v1.json --outdir datasets\splits-realistic-v1
```

Run repeated evaluation on a specific split:

```powershell
python tools\run_multi_model_eval.py --project . --prompts datasets\splits\public_test.json --runs-per-model 3 --start-index 1 --end-index 10 --timeout-s 1200 --resume --out runs\multi-eval-public.json
python tools\build_leaderboard_report.py --in runs\multi-eval-public.json --out-md runs\leaderboard-public.md --out-json runs\leaderboard-public.json
```

Build a publish-ready benchmark card from a single benchmark artifact:

```powershell
python tools\build_benchmark_card.py --in runs\benchmark-YYYYMMDDTHHMMSSZ.json --title "LocalPilot Benchmark Card (Public Test)" --out-md runs\benchmark-card.md --out-json runs\benchmark-card.json
```

## Commands

- `look`: opens the six-page TUI (this is the app)
- `version` / `about` / `update`: version string, what it is, release check (honest: no public channel yet)
- `export`: write the last Route decision to `runs/last-decision.json`
- `init`: writes `localpilot.config.json`, `prompts.seed.json`, and `runs/`
- `doctor`: checks Ollama/llama.cpp, VRAM headroom, hardware profile, lock
- `models`: lists loaded vs available local models
- `recommend`: hardware-aware local-model advice
- `warmup`: cold-start the router so the first real classify is not a hang
- `unlock`: clear the single-model lock
- `probe`: checks runtime and strict-JSON classifier behavior
- `route`: routes one task and explains why (use `--json` for raw dict)
- `benchmark`: runs labeled prompt suite and emits artifact JSON (includes `hardware_profile`)
- `calibrate`: recommends a confidence threshold from benchmark data (refuses HOLD unless `--allow-hold`)
  - uses first-stage router confidence for threshold sweep
  - supports `--objective safe|balanced`
  - supports escalation cap via `--max-escalation-rate`
  - can write artifact with `--out` and update config with `--apply`

## Runtime Details (v0.1)

- Runtime: Ollama HTTP API (`/api/generate`)
- Runtime: llama.cpp server OpenAI-compatible API (`/v1/chat/completions`)
- Mode: JSON output enforced (`format: "json"`)
- Deterministic options for routing (`temperature=0`)
- Policy fallback:
  - two-axis risk guardrails (capability risk vs privacy risk)
  - capability-risk can hard-route to `cloud`
  - privacy-risk can hard-route to `local`
  - conflict mode is configurable (`privacy_first`, `capability_first`, `model_decides`)
  - low-confidence escalation from router model to fallback model

## Two-Axis Risk Policy (Phase C3)

Config section: `policy.two_axis`

- `enabled`: turn two-axis policy on/off
- `capability_threshold`: number of matched capability keywords needed to trigger cloud rule
- `privacy_threshold`: number of matched privacy keywords needed to trigger local rule
- `conflict_mode`: how to resolve when both axes trigger:
  - `privacy_first`
  - `capability_first`
  - `model_decides`
- `privacy_keywords`: lexical indicators for sensitive/private tasks

Explicit audit fields are emitted per decision row:
- `capability_risk_score`, `privacy_risk_score`
- `capability_risk_keywords`, `privacy_risk_keywords`
- `capability_risk_triggered`, `privacy_risk_triggered`
- `risk_conflict`, `risk_conflict_mode`, `risk_axis_decision`

## llama.cpp Notes

- Configure in `localpilot.config.json` under `llamacpp`.
- Default URL is `http://127.0.0.1:8090`.
- Set `models.llamacpp_router` to the model name exposed by your running llama.cpp server.
- If your server requires auth, set `llamacpp.api_key`.

