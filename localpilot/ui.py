from __future__ import annotations

import os
import re
import shutil
import sys
from typing import Any


# LocalPilot terminal identity: machine-shop gauge, not neon hacker CLI.
# Page is warm charcoal so color fills the window, not just the glyphs.
C_RESET = "\x1b[0m"
C_PAGE_BG = "\x1b[48;2;32;26;22m"
C_INK = "\x1b[38;2;232;223;208m"
C_DIM = "\x1b[38;2;138;129;120m"
C_COPPER = "\x1b[38;2;201;132;42m"
C_STEEL = "\x1b[38;2;107;140;174m"
C_OXIDE = "\x1b[38;2;196;92;74m"
C_MOSS = "\x1b[38;2;110;139;92m"
C_PAGE = f"{C_PAGE_BG}{C_INK}"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _enable_windows_vt() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        return


def use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def paint(text: str, color: str) -> str:
    if not use_color():
        return text
    return f"{color}{text}{C_PAGE}"


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def frame(body: str, fill_screen: bool = False) -> str:
    cols, rows = shutil.get_terminal_size((100, 32))
    cols = max(60, cols)
    raw_lines = body.splitlines() or [""]
    if not use_color():
        return body
    out: list[str] = []
    if fill_screen:
        out.append("\x1b[2J\x1b[H")
    out.append(C_PAGE)
    for line in raw_lines:
        pad = max(0, cols - _visible_len(line))
        out.append(f"{line}{' ' * pad}")
    if fill_screen:
        used = len(raw_lines) + 1
        while used < rows:
            out.append(" " * cols)
            used += 1
    out.append(C_RESET)
    return "\n".join(out)


def _row(label: str, value: str, tone: str = C_INK) -> str:
    return f"  {paint(label.ljust(12), C_DIM)}{paint(value, tone)}"


def logo_line() -> str:
    # Pilot light: a small lamp, not a plane. Copper = the flame is on.
    mark = paint("(o)", C_COPPER)
    name = paint("localpilot", C_INK)
    tag = paint("local first", C_DIM)
    return f"{mark}  {name}  {tag}"


def banner(title: str, status: str, tone: str) -> str:
    return f"{logo_line()}\n{paint(title, C_INK)}  {paint(status, tone)}"


def render_doctor(payload: dict[str, Any]) -> str:
    vram = payload.get("vram") or {}
    ollama = payload.get("ollama") or {}
    llamacpp = payload.get("llamacpp") or {}
    lock = payload.get("lock")
    tips = payload.get("tips") or []
    profile = payload.get("profile") or "unknown"

    used = vram.get("vram_used_mb")
    total = vram.get("vram_total_mb")
    pct = vram.get("vram_used_pct")
    tight = bool(isinstance(pct, (int, float)) and pct >= 90)
    if vram.get("available"):
        gauge = f"{used:.0f} / {total:.0f} MB"
        if isinstance(pct, (int, float)):
            gauge += f"  ({pct:.1f}%)"
        status = "tight" if tight else "clear"
        tone = C_OXIDE if tight else C_MOSS
    else:
        gauge = "no nvidia-smi"
        status = "unknown"
        tone = C_DIM

    lines = [
        banner("machine", status, tone),
        _row("profile", profile),
        _row("vram", gauge, C_OXIDE if tight else C_MOSS),
        "",
        _row("ollama", "up" if ollama.get("alive") else "down", C_MOSS if ollama.get("alive") else C_OXIDE),
        _row("loaded", ", ".join(ollama.get("loaded") or []) or "none"),
        _row("llama.cpp", "up" if llamacpp.get("alive") else "down", C_MOSS if llamacpp.get("alive") else C_OXIDE),
        _row("gguf", ", ".join(_short_models(llamacpp.get("models") or [])) or "none"),
        _row(
            "lock",
            f"{lock.get('runtime')} / {lock.get('model')}" if lock else "none",
            C_COPPER if lock else C_DIM,
        ),
    ]
    if tight:
        lines.append("")
        lines.append(paint("  card is tight. Unload a resident before a render or a second heavy model.", C_OXIDE))
    if tips:
        lines.append("")
        for tip in tips:
            lines.append(f"  {paint('-', C_DIM)} {paint(tip, C_INK)}")
    return "\n".join(lines)


def _short_models(models: list[str]) -> list[str]:
    out: list[str] = []
    for m in models:
        name = str(m).replace("\\", "/").split("/")[-1]
        out.append(name)
    return out


def render_models(ollama: dict[str, Any], llamacpp: dict[str, Any], lock: dict[str, Any] | None) -> str:
    lines = [
        banner("models", "resident", C_COPPER),
        _row("ollama", ", ".join(ollama.get("loaded") or []) or "none", C_COPPER),
        _row("llama.cpp", ", ".join(_short_models(llamacpp.get("models") or [])) or "none", C_STEEL),
        _row("lock", f"{lock.get('runtime')} / {lock.get('model')}" if lock else "none"),
    ]
    if ollama.get("multi_model_loaded"):
        lines.append(paint("  more than one Ollama model is resident; unload extras.", C_OXIDE))
    return "\n".join(lines)


def render_recommend(profile: str, vram: dict[str, Any], tips: list[str]) -> str:
    used = vram.get("vram_used_mb")
    total = vram.get("vram_total_mb")
    gauge = f"{used:.0f} / {total:.0f} MB" if vram.get("available") else "unknown"
    lines = [
        banner("recommend", profile, C_INK),
        _row("vram", gauge, C_OXIDE if (vram.get("vram_used_pct") or 0) >= 90 else C_MOSS),
        "",
    ]
    for tip in tips:
        lines.append(f"  {paint('-', C_COPPER)} {paint(tip, C_INK)}")
    return "\n".join(lines)


def render_warmup(runtime: str, model: str, times: list[float]) -> str:
    last = times[-1] if times else None
    cold = bool(times and times[0] > 5000)
    tone = C_OXIDE if cold else C_MOSS
    status = "cold first hit" if cold else "warm"
    lines = [
        banner("warmup", status, tone),
        _row("runtime", runtime),
        _row("model", model, C_COPPER),
        _row("calls", str(len(times))),
    ]
    if times:
        lines.append(_row("latencies", ", ".join(f"{t:.0f}ms" for t in times)))
        lines.append(_row("last", f"{last:.0f} ms", tone))
    if cold:
        lines.append(paint("  keep this model loaded; the next classify should drop hard.", C_DIM))
    return "\n".join(lines)


def render_route(text: str, route: str | None) -> str:
    tone = C_COPPER if route == "local" else C_STEEL if route == "cloud" else C_OXIDE
    header = banner("route", route or "failed", tone)
    body = "\n".join(f"  {paint(line, C_INK)}" for line in text.splitlines())
    return f"{header}\n{body}"


def render_lock_block(message: str) -> str:
    return f"{banner('lock', 'held', C_OXIDE)}\n  {paint(message, C_INK)}"


def render_plain_error(message: str) -> str:
    return f"{banner('error', 'stop', C_OXIDE)}\n  {paint(message, C_INK)}"


def render_look() -> str:
    swatches = [
        f"  {paint('copper', C_COPPER)}   stay on the machine (local)",
        f"  {paint('steel', C_STEEL)}    leave the box (cloud)",
        f"  {paint('moss', C_MOSS)}     runtime up / headroom ok",
        f"  {paint('oxide', C_OXIDE)}    lock, fail, or VRAM tight",
        f"  {paint('ink', C_INK)}      body text",
        f"  {paint('dust', C_DIM)}     labels",
    ]
    doctor = render_doctor(
        {
            "profile": "windows_native",
            "vram": {
                "available": True,
                "vram_used_mb": 10257.0,
                "vram_total_mb": 12227.0,
                "vram_used_pct": 83.9,
            },
            "ollama": {"alive": True, "loaded": ["qwen3:8b"]},
            "llamacpp": {"alive": True, "models": ["Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"]},
            "lock": {"runtime": "ollama", "model": "qwen3:8b"},
            "tips": ["one 8B router resident is enough at 10 GB", "run warmup after a cold start"],
        }
    )
    local_route = render_route(
        "route: local\nwhy: Cheap lexical gate skipped the LLM.\npath: cheap_router_direct\nconfidence: 1.0 (rule)\nlatency_ms: 0.02\nselected_model: cheap_router_nb",
        "local",
    )
    cloud_route = render_route(
        "route: cloud\nwhy: Capability-risk rule forced cloud.\npath: capability_risk_cloud_rule\nconfidence: 0.99 (rule)\nlatency_ms: 0.1\nselected_model: qwen3:8b",
        "cloud",
    )
    lock = render_lock_block("single-model lock active: ollama / qwen3:8b")
    parts = [
        paint("how the CLI looks", C_DIM),
        "",
        "palette",
        *swatches,
        "",
        "doctor",
        doctor,
        "",
        "route local",
        local_route,
        "",
        "route cloud",
        cloud_route,
        "",
        "lock",
        lock,
        "",
        paint("live commands: doctor  models  recommend  warmup  route  unlock", C_DIM),
    ]
    return frame("\n".join(parts), fill_screen=True)


_enable_windows_vt()
