# llama.cpp Router Tuning (LocalPilot)

Target: OpenAI-compatible `llama-server` on `127.0.0.1:8090`, strict JSON `{route, confidence}`.

Hardware assumed: King95 — RTX 5070 12 GB, Ryzen 9700X (8c/16t), Windows native + optional WSL2.

This is a **classifier**, not a coder. Every flag that helps long-context generation hurts this path.

## What is live today (do not treat as router-optimal)

`LlamaServer_Startup.vbs` currently starts:

```text
llama-server.exe --jinja -fa on -ctk q8_0 -ctv q8_0 -ncmoe 36 --no-mmap -ngl 99 -c 65536 --port 8090 --host 127.0.0.1 -m Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf
```

LocalPilot client (`runtime_llamacpp.py` + `localpilot.config.json`):

| Field | Current | Problem |
|---|---|---|
| `temperature` | `0.0` | Correct for greedy classify |
| `top_p` | `0.9` | Unused at temp 0; server still has `min_p=0.05`, `top_k=40` defaults |
| `max_tokens` | `64` | Fine if thinking is off |
| `timeout_s` | `300` | Too long for a hang; router should fail fast |
| `response_format` | `{type: json_object, schema: ...}` | Working form on this llama.cpp build |
| seed / top_k / min_p / repeat_penalty | not sent | Server defaults leak in |
| server `-c` | `65536` | Main stall/VRAM risk |
| server `-ncmoe` | `36` | First 36 MoE layers on CPU (Qwen3-30B-A3B is 48 layers) |
| server `-sps` | default `0.10` | Slot prefix reuse can contaminate classifications |

Measured on this model (120×3): parse rate `1.0`, accuracy `0.758`, avg `~506 ms`, p95 `~710 ms`. Parse is not the bottleneck. Score quality and tail stability are.

---

## 1) Parameter sweep matrix

Request-body fields (`/v1/chat/completions`) and llama.cpp analogs.

| Param | llama.cpp analog | Sweep values | Router default | Effect on JSON / score | Effect on stalls |
|---|---|---|---|---|---|
| `temperature` | `--temp` / `temperature` | `0.0`, `0.1`, `0.2` | **`0.0`** | `0.0` = greedy, repeatable route. `0.1` only if a model refuses greedy or collapses to one class. `>=0.3` = route flip noise. | None directly |
| `top_p` | `--top-p` | `1.0`, `0.9` | **`1.0`** | Disabled at `1.0`. At temp `0` it is ignored. Do not pair `0.9` with temp `0` and assume it is doing work. | None |
| `top_k` | `--top-k` | `1`, `20`, `40` | **`1`** | `1` locks greedy even if a later build applies nucleus at temp 0. `40` is the server default and can leak if client omits it. | None |
| `min_p` | `--min-p` | `0.0`, `0.05` | **`0.0`** | Server default is `0.05`. Some builds still apply min-p at temp 0 and drop valid JSON tokens (`{`, `"`, `0`). Always send `0.0`. | Can cause empty/truncated JSON |
| `repeat_penalty` | `--repeat-penalty` | `1.0`, `1.05`, `1.1` | **`1.0`** | `>1.0` punishes `"`, `{`, key repeats. This **breaks JSON**. Never use for schema output. | Can loop if grammar fights penalty |
| `presence_penalty` / `frequency_penalty` | same names | `0.0` only | **`0.0`** | Same JSON-break risk as repeat penalty. | Rare loops |
| `max_tokens` | `-n` / `--n-predict` | `32`, `48`, `64`, `96` | **`48`** | Payload is ~`{"route":"local","confidence":0.87}` (~20–30 tokens). `32` is tight if the model emits spaces. `64` is the current ceiling. `96+` only if thinking leaks. | High values + thinking = hang-like tails |
| `seed` | `--seed` | `7`, `13`, `42` | **`7`** | Fixed seed makes sweeps comparable. Server default `-1` is random. | None |
| grammar / schema | `--json-schema` / `response_format` | on / off | **on** | Primary parse-rate lever. Off → markdown fences, prose, extra keys. | Grammar can stall if schema is too loose (`{}`) and max_tokens is high |

Do **not** sweep these for routing: `dynatemp-*`, `xtc`, `typical_p`, `mirostat`, `dry`. They add variance and do not improve a 2-class enum.

### Practical sweep order (cheap → expensive)

Run on `datasets/splits/dev.json` (not public/hold). One change per cell. 3 repeats.

```powershell
# From localpilot/
# A. Confirm schema is actually enforced (hash of output must differ from no-schema)
# B. Then sweep only: temperature x max_tokens
# C. Then lock those and sweep seed {7,13,42} for variance
```

| Cell | temperature | max_tokens | top_k | min_p | repeat_penalty | What you are testing |
|---|---:|---:|---:|---:|---:|---|
| Greedy-tight (recommended start) | 0.0 | 48 | 1 | 0.0 | 1.0 | Stability + parse |
| Greedy-wide (current-ish) | 0.0 | 64 | 1 | 0.0 | 1.0 | Truncation vs 48 |
| Soft-greedy | 0.1 | 48 | 1 | 0.0 | 1.0 | Models that collapse at 0 |
| Sampled (expect worse) | 0.2 | 48 | 20 | 0.05 | 1.0 | Noise ceiling |
| Penalty-broken (negative control) | 0.0 | 64 | 40 | 0.05 | 1.1 | Proves JSON breakage |

Success metrics (in this order):

1. `parse_rate == 1.0`
2. `router_direct` accuracy (ignore cheap/keyword shortcuts)
3. confidence spread (not a constant `0.95`)
4. p95 latency
5. timeout / empty_response count = 0

Expected on Qwen3-Coder-30B-A3B: cells 1–2 stay at parse `1.0`; accuracy will not jump from `0.76` to `0.93` from sampling alone. Prompt + policy + smaller dedicated router move accuracy. Sampling only protects reliability.

### Recommended client payload (send ALL of these)

```json
{
  "temperature": 0.0,
  "top_p": 1.0,
  "top_k": 1,
  "min_p": 0.0,
  "repeat_penalty": 1.0,
  "presence_penalty": 0.0,
  "frequency_penalty": 0.0,
  "max_tokens": 48,
  "seed": 7,
  "stream": false,
  "response_format": {
    "type": "json_object",
    "schema": {
      "type": "object",
      "properties": {
        "route": { "type": "string", "enum": ["local", "cloud"] },
        "confidence": { "type": "number", "minimum": 0.0, "maximum": 1.0 }
      },
      "required": ["route", "confidence"],
      "additionalProperties": false
    }
  }
}
```

Also send native llama.cpp field as a belt-and-suspenders copy (same schema object) if you add it to the adapter:

```json
{ "json_schema": { "...same schema..." } }
```

Do **not** send `{ "type": "json_schema", "schema": {...} }` without the inner `json_schema` wrapper. Some llama.cpp builds accept that, return HTTP 200, and silently ignore the schema.

Working forms on current llama-server:

- `{ "type": "json_object", "schema": {...} }` — what LocalPilot already sends
- `{ "type": "json_schema", "json_schema": { "name": "route", "strict": true, "schema": {...} } }`

---

## 2) Runtime and server flags checklist

Goal: no 64k KV, no slot mix-ups, no thinking, no infinite predict, fail in seconds not minutes.

### Must change on the standing router profile

| Flag | Bad (current / default) | Router value | Why |
|---|---|---|---|
| `-c` / `--ctx-size` | `65536` | **`2048`** (max `4096`) | Classifier prompt is tiny. 64k KV is the hang/OOM source. |
| `-np` / `--parallel` | `-1` auto | **`1`** | One slot. No cross-request contamination. |
| `-sps` / `--slot-prompt-similarity` | `0.10` | **`0.0`** | Default reuses a “close enough” slot prefix. Router prompts share the same system prefix, so this can attach the previous TASK’s KV. |
| `--cache-reuse` | `0` (ok) | **`0`** | Keep off. KV shifting on near-identical system prompts is how silent misroutes happen. |
| `--n-predict` | `-1` infinite | **`48`** server cap | Client `max_tokens` should win, but a server cap stops runaway if the client omits it. |
| `--timeout` / `-to` | `3600` | **`30`** | HTTP I/O timeout. Pair with client `timeout_s=20`. |
| `--reasoning` | `auto` | **`off`** | Thinking models eat `max_tokens` then return `empty_response`. |
| `--reasoning-budget` | unrestricted | **`0`** | Belt and suspenders if a thinking template leaks. |
| `--seed` | `-1` random | **`7`** | Reproducible classify. |
| `--temp --top-k --top-p --min-p --repeat-penalty` | `0.8 / 40 / 0.95 / 0.05 / 1.0` | **`0 / 1 / 1.0 / 0.0 / 1.0`** | Server defaults apply when a field is omitted. |
| `--jinja` | on | **keep on** | Needed for Qwen chat template. |
| `-fa` | `on` | **`on`** | RTX 5070: keep flash-attn. |
| `--no-context-shift` | default off | **keep off / explicit `--no-context-shift`** | Shifting a 2k classify window is pointless and can corrupt prefix cache. |
| `--no-webui` | UI on | **`--no-webui`** | Less HTTP surface, fewer accidental slots. |
| `--metrics` + `--slots` | slots on | **keep `--slots`**, add `--metrics` | Watch busy/idle/processing without guessing. |
| `--sleep-idle-seconds` | `-1` | **`-1`** | Do not let the router sleep mid-battery. Cold-start looks like a hang. |
| `--warmup` | on | **keep on** | First-request stall otherwise. |
| `-b` / `-ub` | `2048` / `512` | **`512` / `256`** | Router batches are small. Lower = less VRAM spike. |
| `-t` / `--threads` | auto | **`8`** | 9700X physical cores. SMT (16) often worse for decode. |
| `-tb` / `--threads-batch` | same | **`8`** | Prompt is short; extra threads do not help. |
| `--threads-http` | auto | **`4`** | Enough for probe + one client. |
| `--prio` | `0` | **`0`** | Do not realtime-prio a long-lived server. |
| `--host` | `127.0.0.1` | **`127.0.0.1`** | Privacy. Do not bind `0.0.0.0` unless WSL→Windows needs it. |
| `--api-key` | empty | set if anything else is on the box | LocalPilot already supports `llamacpp.api_key`. |

### Windows-specific load flags

| Flag | Value | Why |
|---|---|---|
| `--load-mode` | **`none`** | Replaces deprecated `--no-mmap`. Windows mmap of large GGUF causes first-token / page-fault stalls. |
| `--no-mmap` | only if old binary | Current VBS still uses this. Prefer `--load-mode none`. |
| `--load-mode mmap+mlock` | avoid on Windows | `mlock` often fails without privilege and then looks like a hang on start. |
| Defender | exclude `llama.cpp\bin` + `models` | AV scanning GGUF = multi-minute “stalls”. |

### MoE / VRAM flags (Qwen3-30B-A3B on 12 GB)

`-ncmoe N` = **first N layers’ expert weights stay on CPU**, not N experts.

| Mode | `-ngl` | `-ncmoe` | `-ctk/-ctv` | `-c` | Fit / cost |
|---|---|---|---|---|---|
| Quality (if VRAM allows) | `99` | `0` | `q8_0` | `2048` | Fastest decode; may not fit 30B-A3B Q4 on 12 GB |
| Current (unstable) | `99` | `36` | `q8_0` | `65536` | Fits, but 36 CPU MoE layers + 64k KV = tails/hangs |
| Router-stable | `99` | `24` | `q8_0` | `2048` | Start here on 5070 12 GB |
| Lower VRAM | `99` | `36` | `q4_0` | `2048` | More CPU MoE + smaller KV |
| Dedicated small router | `99` | `0` | `q8_0` | `2048` | Use 4B/8B GGUF instead of 30B |

If VRAM still blows up: drop the 30B coder as the **router**. Keep it as a worker. Route with Qwen3-4B/8B Instruct Q4_K_M.

### Health checks (do these before any sweep)

```powershell
# 1) Server alive
curl.exe -s http://127.0.0.1:8090/health

# 2) Slots (look for idle=1, processing=0; not 64k tokens used)
curl.exe -s http://127.0.0.1:8090/slots

# 3) Schema actually constrained? Compare hashes.
# If no-schema and schema outputs are identical prose, grammar is NOT on.
```

```powershell
# Probe through LocalPilot
localpilot probe --project . --runtime llamacpp --model "C:\Users\paolo\AppData\Local\llama.cpp\models\Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"
```

Watchdog: client `timeout_s=20`. Eval harness already tree-kills. Do **not** kill the standing `llama-server` on 8090 unless Paolo asks.

---

## 3) Prompting pattern upgrades

Current prompt is one blob: policy + task, system = “You are a strict JSON classifier.” That is why parse works and **confidence is stuck at 0.95**.

Greedy models do not invent calibrated probabilities. You have to give anchors and forbid the default.

### Rules that actually move `local|cloud`

1. **Split system vs user.** System = output contract only. User = policy + few-shot + TASK.
2. **Prefill the assistant** with `{` so the first generated token is already inside JSON. llama-server supports assistant prefill.
3. **Ban thinking / markdown / extra keys** in the contract, not as a hope.
4. **Name the privacy override** in the user policy (matches `privacy_first`). Difficulty does not beat secrets.
5. **Confidence rubric with 4 anchors.** Without this, greedy decode emits one favorite float forever.
6. **Few-shot 4 examples:** easy-local, easy-cloud, ambiguous-low-conf, privacy-force-local.
7. **Do not put identifier bait in the policy.** The word `auth` inside `authGateway` already false-clouds via the keyword rule; the LLM prompt should not repeat that trap.

### Drop-in prompts

**System**

```text
You output only a JSON object that matches the schema.
Keys: route, confidence.
route is exactly local or cloud.
confidence is a JSON number in [0,1], not a string.
No markdown. No extra keys. No reasoning text.
```

**User**

```text
Classify this developer task for model routing.

route=local when the work is mechanical, narrow, already-specified, or low-stakes
(rename, format, docstring, regex, single-file translate, obvious off-by-one).

route=cloud when the work needs architecture, security review, incident diagnosis,
multi-step tradeoffs, or the failure cost is high.

Privacy override: if the task involves secrets, credentials, PII, PHI, customer data,
or production dumps, choose local even if the work looks hard.

Confidence anchors (use these, do not default to 0.95):
- 0.55 = could reasonably be either class
- 0.70 = lean, but one key fact could flip it
- 0.85 = clear, typical case
- 0.97 = textbook case, no reasonable other class

Examples:
TASK: Rename tmp to retryCount in one file.
{"route":"local","confidence":0.97}

TASK: Design caching for a read-heavy multi-region service.
{"route":"cloud","confidence":0.97}

TASK: Refactor a 300-line class into smaller units.
{"route":"local","confidence":0.70}

TASK: Summarize a stack trace that includes a customer auth token.
{"route":"local","confidence":0.85}

TASK: {task}
```

**Assistant prefill** (add as last message)

```text
{
```

Then the model only has to complete `"route":...`. Combined with schema grammar, parse failures should stay at zero.

### Confidence quality (separate from route accuracy)

Model-reported confidence will still be poorly calibrated. LocalPilot already has the right downstream:

- `policy.confidence_threshold` (currently `0.94`)
- `uncertain_band.low_confidence_to_cloud`
- `calibrate` on **router_first_*** only

If after the rubric the model still emits a single float, treat confidence as unused and let cheap-router + uncertain-band own the threshold. Do not keep raising temperature to “get more interesting confidences.”

---

## 4) Failure taxonomy → mitigation

Mapped to `LlamaCppAdapter` error strings plus runtime faults the adapter does not name yet.

| Code / symptom | What it is | Typical cause on this stack | Mitigation |
|---|---|---|---|
| `request_failed: timed out` | Client socket timeout | 64k ctx, CPU MoE, thinking, server sleep, first-load mmap | `-c 2048`, lower `-ncmoe`, `--reasoning off`, `--sleep-idle-seconds -1`, `--load-mode none`, `timeout_s=20` |
| `request_failed: Connection refused / reset` | Server died or not bound | OOM, crash after KV alloc, wrong port | `/health` before probe; shrink ctx/KV; do not restart 8090 unless asked |
| `http_400` | Bad request | Wrong `response_format` shape, unknown model name | Use `json_object+schema` form; `model` = exact `-m` path or server alias |
| `http_401` | Auth | `--api-key` set, config empty | Set `llamacpp.api_key` |
| `http_503` / slot busy | No free slot | `-np` auto + another client; previous request hung | `-np 1`, `-to 30`, kill hung **client** not server |
| `http_500` | Server exception | OOM during eval, grammar compile fail | Smaller ctx; validate schema; check server log |
| `empty_response` | `choices[0].message.content` empty | Thinking consumed `max_tokens`; content in `reasoning_content` | `--reasoning off`, `--reasoning-budget 0`, read `reasoning_content` only as a debug fallback |
| `parse_failed` | Content is not JSON | Markdown fences, leading prose, schema ignored | Keep working `response_format`; prefill `{`; strip ``` only as last-resort salvage |
| `invalid_route` | JSON parsed, route not enum | `Local`, `on-device`, `both` | Schema enum + grammar; lowercase+strip salvage |
| `invalid_confidence_type` | `"0.9"` string | Model quoted the number | Schema `type: number`; salvage `float()` once |
| `invalid_confidence_range` | `<0` or `>1` | `95` or `1.5` | Clamp only after a failed parse path; do not silently clamp good rows |
| Truncated JSON | `{"route":"loc` | `max_tokens` too low or thinking | `48` without thinking; never raise to 256 “just in case” on a thinking model |
| Constant `confidence=0.95` | Parse OK, score useless | Greedy favorite token | Rubric + few-shot; then ignore and use `calibrate` |
| Wrong class, high conf | Score OK, decision bad | Vague policy; identifier bait (`auth`, `token`) | Prompt upgrade; two-axis keywords are a **separate** false-cloud source |
| Slot contamination | Stable but wrong vs previous task | `-sps 0.10` shared system prefix | `-sps 0.0`, `-np 1` |
| Slow-first / hang-first | Only trial 1 is huge | No warmup, mmap, AV scan | `--warmup`, `--load-mode none`, 3 warmup classify calls (eval already does this) |
| All trials TIMEOUT | Model/runtime wedged | 15-min battery cap | Kill **opencode/trial** only; `ollama stop` if Ollama; do not kill 8090; skip model |
| Privacy leak | Task text left the box | Bound `0.0.0.0`, cloud fallback, logs | `--host 127.0.0.1`, no `--api-key` on LAN, do not log full TASK to remote |

Salvage order if you add a parser fallback (do not add until schema+prefill still fail):

1. take `content` or `reasoning_content`
2. extract first `{...}` 
3. `json.loads`
4. lowercase route, `float(confidence)`
5. else fail closed → `uncertain_band_cloud` / `router_failed_no_fallback`

Fail **closed to cloud** for capability uncertainty, **closed to local** for privacy keywords. That is already the policy. Do not retry the same llama.cpp classify more than once per task.

---

## 5) Concrete default profiles

Shared client block for all three (put in `localpilot.config.json` → `llamacpp`):

```json
{
  "base_url": "http://127.0.0.1:8090",
  "endpoint": "/v1/chat/completions",
  "api_key": "",
  "timeout_s": 20,
  "temperature": 0.0,
  "top_p": 1.0,
  "top_k": 1,
  "min_p": 0.0,
  "repeat_penalty": 1.0,
  "max_tokens": 48,
  "seed": 7
}
```

`top_k` / `min_p` / `repeat_penalty` / `seed` are not wired in the adapter yet. Until they are, set the same values as **server** defaults so omitted client fields cannot resurrect `min_p=0.05`.

Policy stays: `confidence_threshold=0.94`, `uncertain_band.low_confidence_to_cloud=true`, `two_axis.conflict_mode=privacy_first`.

### A) Desktop Windows native (recommended daily)

Model: keep 30B-A3B only if you accept ~500 ms. Prefer a 4B/8B Instruct GGUF for the router.

```powershell
$exe = "C:\Users\paolo\AppData\Local\llama.cpp\bin\llama-server.exe"
$gguf = "C:\Users\paolo\AppData\Local\llama.cpp\models\Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"

& $exe `
  --host 127.0.0.1 --port 8090 `
  -m $gguf `
  --jinja --no-webui `
  -fa on `
  --load-mode none `
  -ngl 99 -ncmoe 24 `
  -c 2048 -n 48 `
  -b 512 -ub 256 `
  -ctk q8_0 -ctv q8_0 `
  -np 1 -sps 0.0 --cache-reuse 0 --no-context-shift `
  -t 8 -tb 8 --threads-http 4 `
  --timeout 30 --sleep-idle-seconds -1 `
  --reasoning off --reasoning-budget 0 `
  --temp 0 --top-k 1 --top-p 1.0 --min-p 0.0 --repeat-penalty 1.0 --seed 7 `
  --metrics --slots
```

Replace the VBS command with the block above when you are ready to recycle 8090. Until then, do not kill the standing server.

### B) WSL2 Linux

Put the GGUF on the **Linux filesystem**, not `/mnt/c`. NTFS-via-9p will look like hangs.

```bash
# ~/.wslconfig on Windows (once):
# [wsl2]
# memory=16GB
# processors=8

# Inside WSL, CUDA llama-server (example paths)
./llama-server \
  --host 127.0.0.1 --port 8090 \
  -m /home/paolo/models/Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf \
  --jinja --no-webui \
  -fa on \
  --load-mode mmap \
  -ngl 99 -ncmoe 24 \
  -c 2048 -n 48 \
  -b 512 -ub 256 \
  -ctk q8_0 -ctv q8_0 \
  -np 1 -sps 0.0 --cache-reuse 0 --no-context-shift \
  -t 8 -tb 8 --threads-http 4 \
  --timeout 30 --sleep-idle-seconds -1 \
  --reasoning off --reasoning-budget 0 \
  --temp 0 --top-k 1 --top-p 1.0 --min-p 0.0 --repeat-penalty 1.0 --seed 7 \
  --metrics --slots
```

Windows LocalPilot → WSL server:

- `localhost:8090` usually forwards
- if it does not: `--host 0.0.0.0` **inside WSL only**, Windows firewall scoped to WSL, `base_url` stays `http://127.0.0.1:8090`
- `--load-mode mmap` is correct on ext4; `none` is the Windows workaround
- expect +10–30% latency vs native for the same GGUF

### C) Lower VRAM mode (8 GB or 5070 crowded with other GPU work)

Use a small dedicated router. Do not fight 30B-A3B + games/Blender.

```powershell
$exe = "C:\Users\paolo\AppData\Local\llama.cpp\bin\llama-server.exe"
$gguf = "C:\Users\paolo\AppData\Local\llama.cpp\models\Qwen3-8B-Instruct-Q4_K_M.gguf"  # or 4B

& $exe `
  --host 127.0.0.1 --port 8090 `
  -m $gguf `
  --jinja --no-webui `
  -fa on `
  --load-mode none `
  -ngl 99 -ncmoe 0 `
  -c 2048 -n 48 `
  -b 256 -ub 128 `
  -ctk q4_0 -ctv q4_0 `
  -np 1 -sps 0.0 --cache-reuse 0 --no-context-shift `
  -t 8 -tb 8 --threads-http 4 `
  --timeout 20 --sleep-idle-seconds -1 `
  --reasoning off --reasoning-budget 0 `
  --temp 0 --top-k 1 --top-p 1.0 --min-p 0.0 --repeat-penalty 1.0 --seed 7 `
  --metrics --slots
```

If you must keep 30B-A3B on a crowded 12 GB card:

```text
-ngl 99 -ncmoe 36 -c 2048 -n 48 -ctk q4_0 -ctv q4_0 -b 256 -ub 128
```

Never combine that with `-c 65536`.

---

## Verify after a profile change

```powershell
curl.exe -s http://127.0.0.1:8090/health
localpilot probe --project . --runtime llamacpp
localpilot benchmark --project . --runtime llamacpp --prompts datasets\splits\dev.json --warmup-calls 3 --sleep-ms 50
```

Pass bar for a router profile:

- parse_rate `1.0`
- no `empty_response` / timeout
- p95 well under `2000 ms` (current 30B-A3B p95 ~710 ms is already OK **if** ctx is 2k)
- `router_direct` accuracy tracked separately from keyword/cheap shortcuts

Accuracy to `>=0.93` will not come from temperature. Next levers after this profile: prompt+prefill in the adapter, send `top_k/min_p/seed`, stop using 30B as the router, then re-calibrate threshold on `router_first_*`.
