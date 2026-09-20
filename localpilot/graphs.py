from __future__ import annotations

from typing import Any

_BARS = "▁▂▃▄▅▆▇█"


def smooth_series(samples: list[float], window: int = 5) -> list[float]:
    if len(samples) < 2:
        return list(samples)
    out: list[float] = []
    for i in range(len(samples)):
        chunk = samples[max(0, i - window + 1) : i + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def _span(vals: list[float], width: int, pad: str) -> list[float]:
    if not vals:
        return [0.0] * width
    if len(vals) >= width:
        return vals[-width:]
    if pad == "stretch":
        n = len(vals)
        if n == 1 or width == 1:
            return [vals[0]] * width
        out: list[float] = []
        for i in range(width):
            t = i * (n - 1) / (width - 1)
            lo = int(t)
            hi = min(n - 1, lo + 1)
            frac = t - lo
            out.append(vals[lo] * (1.0 - frac) + vals[hi] * frac)
        return out
    if pad == "hold":
        fill = vals[-1]
        return [fill] * (width - len(vals)) + list(vals)
    return [0.0] * (width - len(vals)) + list(vals)


def sparkline(samples: list[float], width: int = 24) -> str:
    width = max(8, int(width))
    vals = [max(0.0, min(100.0, float(v))) for v in (samples or [0.0])][-width:]
    out: list[str] = []
    for v in vals:
        if v <= 0:
            out.append("·")
        else:
            out.append(_BARS[max(1, min(7, int(v / 100.0 * 7)))])
    return "".join(out).ljust(width, "·")


def line_graph(
    samples: list[float],
    width: int = 46,
    height: int = 8,
    title: str = "CPU",
    suffix: str = "%",
    pad: str = "hold",
    boost: float = 0.0,
    extra: str = "",
) -> str:
    width = max(16, int(width))
    raw = [max(0.0, min(100.0, float(v))) for v in (samples or [0.0])]
    current = raw[-1]
    vals = _span(raw, width, pad)
    head = f"{title:<5} {current:5.1f}{suffix}"
    if extra:
        head = f"{head}  {extra}"
    lines = [head]
    floor = 100.0 / max(1, height)
    mark = max(boost, floor) if boost else 0.0
    for row in range(height, 0, -1):
        hi = (row / height) * 100.0
        lo = ((row - 1) / height) * 100.0
        axis = f"{int(hi):3d}|"
        cells: list[str] = []
        for value in vals:
            shown = mark if (mark and 0 < value < mark) else value
            if shown >= hi:
                cells.append("█")
            elif shown >= lo or (row == 1 and shown > 0):
                cells.append("▄" if shown < floor else "█")
            else:
                cells.append(" ")
        lines.append(axis + "".join(cells))
    lines.append("  0+" + "-" * width)
    return "\n".join(lines)


def mini_meters(cpu: float, ram: float, disk: float, gpu: float, vram: str) -> str:
    return (
        f"this call   CPU {cpu:.0f}%   RAM {ram:.0f}%   DISK {disk:.0f}%   GPU {gpu:.0f}%   {vram}"
    )


def disk_card(disk: dict[str, Any] | None, letter: str, samples: list[float] | None = None, width: int = 32, height: int = 7) -> str:
    if not disk:
        return f"{letter}:  DRIVE\n  not connected to this PC"
    used = float(disk.get("used_gb") or 0)
    total = float(disk.get("total_gb") or 0)
    pct = float(disk.get("pct") or 0)
    free = max(0.0, total - used)
    kind = disk.get("fstype") or ""
    mount = str(disk.get("mount") or f"{letter}:\\")
    bar_w = max(16, width - 2)
    filled = max(0, min(bar_w, int(round(bar_w * pct / 100.0))))
    bar = "[" + "█" * filled + "░" * (bar_w - filled) + "]"
    head = (
        f"{letter}:  DRIVE  {kind}\n"
        f"{pct:.0f}% used   {used:.0f} / {total:.0f} GB\n"
        f"{free:.0f} GB free   {mount}\n"
        f"{bar}"
    )
    series = list(samples or [pct])
    if not series:
        series = [pct]
    return head + "\n" + line_graph(series, width=width, height=height, title=f"{letter}:", pad="hold")


def format_disks(host: dict[str, Any]) -> str:
    disks = list(host.get("disks") or [])
    count = int(host.get("disk_count") or len(disks))
    if not disks:
        return "disks  unknown"
    bits = [f"{row['letter']}: {row['used_gb']:.0f}/{row['total_gb']:.0f} GB ({row['pct']:.0f}%)" for row in disks]
    noun = "disk" if count == 1 else "disks"
    return f"{count} {noun}  ·  " + "  ·  ".join(bits)


def format_activity(
    activity: dict[str, Any],
    vram: dict[str, Any],
    ollama: dict[str, Any] | None,
    lock_model: str | None,
    busy: bool,
) -> str:
    ollama = ollama or {}
    gpu = vram.get("gpu_util_pct")
    gpu_txt = f"{float(gpu):.0f}%" if isinstance(gpu, (int, float)) else "n/a"
    mem_util = vram.get("gpu_mem_util_pct")
    mem_txt = f"  mem {float(mem_util):.0f}%" if isinstance(mem_util, (int, float)) else ""
    used = vram.get("vram_used_mb")
    total = vram.get("vram_total_mb")
    card = ""
    if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total:
        card = f"  VRAM {used / 1024:.1f}/{total / 1024:.1f} GB"
    loaded = list(ollama.get("loaded") or [])
    details = {str(row.get("name")): row for row in (ollama.get("loaded_detail") or [])}
    detail = details.get(lock_model or "") or (details[loaded[0]] if loaded else {})
    weight = detail.get("vram_gb")
    disk = detail.get("disk_gb") or (ollama.get("disk_gb") or {}).get(lock_model or "")
    model_bits = []
    if lock_model:
        model_bits.append(lock_model)
    if isinstance(weight, (int, float)):
        model_bits.append(f"in VRAM {weight:.1f} GB")
    if isinstance(disk, (int, float)):
        model_bits.append(f"on disk {disk:.1f} GB")
    if loaded:
        model_bits.append("resident " + ", ".join(loaded))
    else:
        model_bits.append("no Ollama model in VRAM")

    procs = list(activity.get("procs") or [])
    if procs:
        lines = []
        for proc in procs:
            lines.append(
                f"{proc['role']}\n"
                f"  pid {proc['pid']}    {proc['cpu_pct']:.0f}% of one core\n"
                f"  {proc['cpu_machine_pct']:.1f}% of the PC    {proc['ram_mb']:.0f} MB RAM\n"
                f"  disk {proc['read_mb_s']:.1f}r / {proc['write_mb_s']:.1f}w MB/s"
            )
        proc_block = "\n\n".join(lines)
    else:
        proc_block = "no ollama / llama-server process found"

    pushing = bool(busy or activity.get("working") or (isinstance(gpu, (int, float)) and gpu >= 15))
    state = "WORKING" if pushing else "idle"
    model_block = "\n".join(model_bits) if model_bits else "no model locked"
    note = str(activity.get("gpu_note") or "").strip()
    vram_line = card.strip() if card else ""
    return "\n".join(
        part
        for part in (
            state,
            "",
            f"5070   SM {gpu_txt}{mem_txt}",
            vram_line,
            "",
            model_block,
            "",
            proc_block,
            "",
            note,
        )
        if part is not None
    ).rstrip()
