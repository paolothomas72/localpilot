from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


HOLD_MARKERS = ("private_hold", "private-hold", "/hold/", "\\hold\\")


def hardware_profile() -> str:
    release = (platform.release() or "").lower()
    if os.environ.get("WSL_DISTRO_NAME") or "microsoft" in release:
        return "wsl2"
    if os.name == "nt":
        return "windows_native"
    return "linux_native"


def is_hold_path(path: str | Path) -> bool:
    text = str(path).replace("\\", "/").lower()
    return any(marker.replace("\\", "/") in text for marker in HOLD_MARKERS)


def _http_json(url: str, timeout_s: float = 3.0) -> tuple[bool, Any, str | None]:
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout_s) as res:
            raw = res.read().decode("utf-8", errors="replace")
            if not raw:
                return True, None, None
            try:
                return True, json.loads(raw), None
            except json.JSONDecodeError:
                return True, raw[:200], None
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)


def _bytes_gb(raw: Any) -> float | None:
    if not isinstance(raw, (int, float)) or raw <= 0:
        return None
    return round(float(raw) / (1024**3), 2)


def probe_ollama(host: str = "127.0.0.1", port: int = 11434) -> dict[str, Any]:
    ok_tags, tags, err_tags = _http_json(f"http://{host}:{port}/api/tags")
    ok_ps, ps, err_ps = _http_json(f"http://{host}:{port}/api/ps")
    models: list[str] = []
    disk_gb: dict[str, float] = {}
    if isinstance(tags, dict):
        for item in tags.get("models", []) or []:
            name = item.get("name")
            if not name:
                continue
            models.append(str(name))
            size = _bytes_gb(item.get("size"))
            if size is not None:
                disk_gb[str(name)] = size
    loaded: list[str] = []
    loaded_detail: list[dict[str, Any]] = []
    if isinstance(ps, dict):
        for item in ps.get("models", []) or []:
            name = item.get("name") or item.get("model")
            if not name:
                continue
            loaded.append(str(name))
            loaded_detail.append(
                {
                    "name": str(name),
                    "weight_gb": _bytes_gb(item.get("size")),
                    "vram_gb": _bytes_gb(item.get("size_vram")),
                    "disk_gb": disk_gb.get(str(name)),
                }
            )
    alive = ok_tags
    return {
        "alive": alive,
        "error": None if alive else (err_tags or err_ps),
        "models": models,
        "loaded": loaded,
        "loaded_detail": loaded_detail,
        "disk_gb": disk_gb,
        "multi_model_loaded": len(loaded) > 1,
    }


def probe_llamacpp(base_url: str = "http://127.0.0.1:8090") -> dict[str, Any]:
    base = base_url.rstrip("/")
    ok_health, health, err_health = _http_json(f"{base}/health")
    ok_models, models_payload, err_models = _http_json(f"{base}/v1/models")
    models: list[str] = []
    if isinstance(models_payload, dict):
        models = [str(m.get("id")) for m in models_payload.get("data", []) if m.get("id")]
    alive = ok_health or ok_models
    return {
        "alive": alive,
        "error": None if alive else (err_health or err_models),
        "health": health if isinstance(health, (dict, str)) else None,
        "models": models,
    }


def probe_vram() -> dict[str, Any]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=name,memory.used,memory.total,utilization.gpu,utilization.memory",
        "--format=csv,noheader,nounits",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5, encoding="utf-8", errors="replace")
        if p.returncode != 0 or not (p.stdout or "").strip():
            return {"available": False, "error": (p.stderr or "nvidia-smi failed").strip()[:200]}
        line = p.stdout.strip().splitlines()[0]
        parts = [x.strip() for x in line.split(",")]
        used = float(parts[1]) if len(parts) > 1 else None
        total = float(parts[2]) if len(parts) > 2 else None
        util = float(parts[3]) if len(parts) > 3 else None
        mem_util = float(parts[4]) if len(parts) > 4 else None
        headroom = (total - used) if (used is not None and total is not None) else None
        peak_pct = (100.0 * used / total) if (used is not None and total) else None
        return {
            "available": True,
            "name": parts[0] if parts else None,
            "vram_used_mb": used,
            "vram_total_mb": total,
            "vram_headroom_mb": headroom,
            "vram_used_pct": round(peak_pct, 2) if peak_pct is not None else None,
            "gpu_util_pct": util,
            "gpu_mem_util_pct": mem_util,
            "headroom_ok": bool(headroom is not None and headroom >= 800),
        }
    except FileNotFoundError:
        return {"available": False, "error": "nvidia-smi not found"}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}


def _disk_letter(mount: str) -> str:
    text = (mount or "").replace("/", "\\")
    if len(text) >= 2 and text[1] == ":":
        return text[0].upper()
    return text or "?"


def probe_disks() -> list[dict[str, Any]]:
    try:
        import psutil
    except ImportError:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        return []
    for part in parts:
        kind = (part.fstype or "").upper()
        opts = (part.opts or "").lower()
        if not kind or kind in {"CDFS", "ISO9660"}:
            continue
        if "cdrom" in opts or "removable" in opts:
            continue
        device = (part.device or part.mountpoint or "").upper()
        if device in seen:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        seen.add(device)
        rows.append(
            {
                "letter": _disk_letter(part.mountpoint or part.device),
                "mount": part.mountpoint,
                "fstype": kind,
                "used_gb": round(usage.used / (1024**3), 1),
                "total_gb": round(usage.total / (1024**3), 1),
                "pct": float(usage.percent),
            }
        )
    rows.sort(key=lambda row: row["letter"])
    return rows


def probe_host() -> dict[str, Any]:
    try:
        import psutil
    except ImportError:
        return {"available": False, "error": "psutil not installed"}
    try:
        vm = psutil.virtual_memory()
        disks = probe_disks()
        root = next((d for d in disks if d["letter"] == "C"), disks[0] if disks else None)
        return {
            "available": True,
            "cpu_pct": float(psutil.cpu_percent(interval=None)),
            "cpu_count": int(psutil.cpu_count(logical=True) or 1),
            "ram_pct": float(vm.percent),
            "ram_used_gb": round(vm.used / (1024**3), 1),
            "ram_total_gb": round(vm.total / (1024**3), 1),
            "disk_count": len(disks),
            "disks": disks,
            "disk_pct": float(root["pct"]) if root else 0.0,
            "disk_used_gb": root["used_gb"] if root else None,
            "disk_total_gb": root["total_gb"] if root else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}


_IO_PREV: dict[int, tuple[float, int, int]] = {}
_PROC_NAMES = {
    "ollama.exe": "ollama",
    "ollama_llama_server.exe": "ollama-runner",
    "llama-server.exe": "llama.cpp",
}


def _io_rate(pid: int, read_b: int, write_b: int) -> tuple[float, float]:
    now = time.monotonic()
    prev = _IO_PREV.get(pid)
    _IO_PREV[pid] = (now, read_b, write_b)
    if not prev:
        return 0.0, 0.0
    dt = max(0.05, now - prev[0])
    return max(0.0, (read_b - prev[1]) / dt), max(0.0, (write_b - prev[2]) / dt)


def probe_runtime_activity() -> dict[str, Any]:
    """CPU / RAM / disk I/O for local inference processes. GPU % is the whole card."""
    try:
        import psutil
    except ImportError:
        return {"available": False, "error": "psutil not installed", "procs": []}
    cores = float(psutil.cpu_count(logical=True) or 1)
    procs: list[dict[str, Any]] = []
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            raw = str(proc.info.get("name") or "")
            key = raw.lower()
            role = _PROC_NAMES.get(key)
            if role is None and "ollama" in key and "app" not in key:
                role = "ollama-runner"
            if role is None:
                continue
            try:
                cpu = float(proc.cpu_percent(interval=None))
                mem = proc.memory_info()
                io = proc.io_counters() if hasattr(proc, "io_counters") else None
            except (psutil.Error, AttributeError):
                continue
            read_bps, write_bps = (0.0, 0.0)
            if io is not None:
                read_bps, write_bps = _io_rate(int(proc.info["pid"]), int(io.read_bytes), int(io.write_bytes))
            procs.append(
                {
                    "pid": int(proc.info["pid"]),
                    "name": raw,
                    "role": role,
                    "cpu_pct": cpu,
                    "cpu_machine_pct": round(cpu / cores, 2),
                    "ram_mb": round(mem.rss / (1024**2), 1),
                    "read_mb_s": round(read_bps / (1024**2), 2),
                    "write_mb_s": round(write_bps / (1024**2), 2),
                }
            )
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc), "procs": []}
    cpu_sum = sum(p["cpu_pct"] for p in procs)
    machine = sum(p["cpu_machine_pct"] for p in procs)
    ram = sum(p["ram_mb"] for p in procs)
    read_s = sum(p["read_mb_s"] for p in procs)
    write_s = sum(p["write_mb_s"] for p in procs)
    working = cpu_sum >= 8.0
    return {
        "available": True,
        "procs": procs,
        "cpu_pct": round(cpu_sum, 1),
        "cpu_machine_pct": round(machine, 1),
        "ram_mb": round(ram, 1),
        "read_mb_s": round(read_s, 2),
        "write_mb_s": round(write_s, 2),
        "working": working,
        "gpu_note": "GPU % is the whole 5070. Windows does not split SM% per process.",
    }


def lock_path(project_dir: Path) -> Path:
    return project_dir / "runs" / ".router-lock.json"


def read_lock(project_dir: Path) -> dict[str, Any] | None:
    path = lock_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_lock(project_dir: Path, runtime: str, model: str) -> dict[str, Any]:
    payload = {
        "runtime": runtime,
        "model": model,
        "locked_at": datetime.now(timezone.utc).isoformat(),
    }
    path = lock_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def clear_lock(project_dir: Path) -> None:
    path = lock_path(project_dir)
    if path.exists():
        path.unlink()


def enforce_single_model_lock(
    project_dir: Path,
    runtime: str,
    model: str,
    enabled: bool,
    force: bool = False,
) -> dict[str, Any]:
    if not enabled:
        return {"locked": False, "ok": True, "model": model, "runtime": runtime}
    existing = read_lock(project_dir)
    if existing and not force:
        same = existing.get("runtime") == runtime and existing.get("model") == model
        if not same:
            return {
                "locked": True,
                "ok": False,
                "error": (
                    f"single-model lock active: {existing.get('runtime')} / {existing.get('model')}. "
                    "Use the same model or pass --force-model to replace the lock."
                ),
                "existing": existing,
            }
    payload = write_lock(project_dir, runtime, model)
    return {"locked": True, "ok": True, "model": model, "runtime": runtime, "lock": payload}


def recommend_local_setup(vram: dict[str, Any], ollama: dict[str, Any], llamacpp: dict[str, Any]) -> list[str]:
    tips: list[str] = []
    total = vram.get("vram_total_mb")
    used_pct = vram.get("vram_used_pct")
    loaded = ollama.get("loaded") or []
    if isinstance(total, (int, float)) and total <= 8192:
        tips.append("VRAM is tight: use a 4B/8B instruct model as router; do not load a 30B router.")
    elif isinstance(total, (int, float)) and total <= 13000:
        tips.append("12 GB class: keep one 8B/12B router loaded. Treat 30B as a worker only if already resident.")
    else:
        tips.append("Headroom looks fine for a mid-size local router; still avoid swapping models mid-session.")
    if used_pct is not None and used_pct > 92:
        tips.append("VRAM used >92%. Unload extra Ollama models before routing.")
    if len(loaded) > 1:
        tips.append(f"Multiple Ollama models loaded ({', '.join(loaded)}). Single-model lock wants one resident model.")
    if not ollama.get("alive") and not llamacpp.get("alive"):
        tips.append("No local runtime answered. Start Ollama or llama-server before route/benchmark.")
    elif not ollama.get("alive"):
        tips.append("Ollama is down. llama.cpp can still route if :8090 is up.")
    elif not llamacpp.get("alive"):
        tips.append("llama.cpp :8090 did not answer. Ollama can still route.")
    tips.append("Run `localpilot warmup` after a cold start so the first real classify is not a 45s timeout.")
    tips.append("OpenCode and Hermes write files and run tools. LocalPilot picks the local model and answers here.")
    return tips
