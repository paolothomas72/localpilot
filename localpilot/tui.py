from __future__ import annotations

import os
import re
import shutil
import sys
from typing import Callable


# Visible workshop page — not near-black, or a photo still looks like the default terminal.
RGB_PAGE = (58, 46, 36)
RGB_PANEL = (74, 60, 48)
RGB_HEADER = (201, 132, 42)
RGB_HEADER_INK = (36, 24, 14)
RGB_FOOTER = (36, 28, 22)
RGB_INK = (232, 223, 208)
RGB_DIM = (168, 152, 136)
RGB_COPPER = (201, 132, 42)
RGB_STEEL = (107, 140, 174)
RGB_OXIDE = (196, 92, 74)
RGB_MOSS = (110, 139, 92)
RGB_CHIP_INK = (24, 18, 14)

C_RESET = "\x1b[0m"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
ALT_ON = "\x1b[?1049h\x1b[?25l"
ALT_OFF = "\x1b[?25h\x1b[?1049l"


def _fg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _bg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _enable_windows_vt() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004 | 0x0008)
    except Exception:
        return


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _box_chars() -> tuple[str, str, str, str, str, str]:
    chars = ("┌", "┐", "└", "┘", "─", "│")
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "".join(chars).encode(enc)
        return chars
    except Exception:
        return ("+", "+", "+", "+", "-", "|")


def _clip_pad(text: str, width: int, fill_bg: tuple[int, int, int], fill_fg: tuple[int, int, int]) -> str:
    out: list[str] = []
    visible = 0
    i = 0
    while i < len(text) and visible < width:
        if text[i] == "\x1b":
            m = _ANSI_RE.match(text, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
        out.append(text[i])
        visible += 1
        i += 1
    if visible < width:
        out.append(f"{_bg(fill_bg)}{_fg(fill_fg)}{' ' * (width - visible)}")
    return "".join(out)


def _size() -> tuple[int, int]:
    cols, rows = shutil.get_terminal_size((100, 32))
    return max(60, cols), max(16, rows)


def _paint_row(text: str, width: int, fill_bg: tuple[int, int, int], fill_fg: tuple[int, int, int]) -> str:
    return _clip_pad(f"{_bg(fill_bg)}{_fg(fill_fg)}{text}", width, fill_bg, fill_fg)


def _chip(label: str, rgb: tuple[int, int, int]) -> str:
    return f"{_bg(rgb)}{_fg(RGB_CHIP_INK)} {label} {_bg(RGB_PANEL)}{_fg(RGB_INK)}"


def _read_key() -> str:
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            extra = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(extra, extra)
        if ch == "\x1b":
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        return ch
    import tty
    import termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch == "\x1b":
        rest = sys.stdin.read(2) if sys.stdin.isatty() else ""
        return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(rest, "esc")
    if ch in ("\r", "\n"):
        return "enter"
    return ch


def build_screen(
    title: str,
    status: str,
    status_rgb: tuple[int, int, int],
    body: list[str],
    footer: str,
    cols: int,
    rows: int,
) -> list[str]:
    tl, tr, bl, br, hz, vt = _box_chars()
    lines: list[str] = []

    header_left = f" (o)  localpilot   {title}"
    header_right = f"{status} "
    gap = max(1, cols - _visible_len(header_left) - _visible_len(header_right))
    header = f"{header_left}{' ' * gap}{_fg(status_rgb)}{header_right}"
    lines.append(_paint_row(header, cols, RGB_HEADER, RGB_HEADER_INK))

    inner_w = max(20, cols - 4)
    title_bit = f" {title} "
    rule = hz * max(0, inner_w - 2 - _visible_len(title_bit))
    top = f"  {tl}{title_bit}{rule}{tr}"
    bottom = f"  {bl}{hz * (inner_w - 2)}{br}"
    lines.append(_paint_row("", cols, RGB_PAGE, RGB_INK))
    lines.append(_paint_row(top, cols, RGB_PAGE, RGB_DIM))

    body_rows = max(1, rows - 5)
    padded_body = list(body[:body_rows])
    while len(padded_body) < body_rows:
        padded_body.append("")
    for raw in padded_body:
        inner = f"  {vt} {_bg(RGB_PANEL)}{_fg(RGB_INK)}{_clip_pad(raw, inner_w - 4, RGB_PANEL, RGB_INK)}{_bg(RGB_PAGE)}{_fg(RGB_DIM)} {vt}"
        lines.append(_paint_row(inner, cols, RGB_PAGE, RGB_DIM))

    lines.append(_paint_row(bottom, cols, RGB_PAGE, RGB_DIM))
    lines.append(_paint_row(f" {footer}", cols, RGB_FOOTER, RGB_DIM))
    return lines[:rows]


def render_buffer(lines: list[str]) -> str:
    return "\x1b[H" + "\n".join(lines) + C_RESET


def _draw(lines: list[str]) -> None:
    sys.stdout.write(render_buffer(lines))
    sys.stdout.flush()


def _run_loop(draw: Callable[[], list[str]], on_key: Callable[[str], bool]) -> None:
    _enable_windows_vt()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.stdout.write(ALT_ON)
    sys.stdout.flush()
    try:
        while True:
            _draw(draw())
            key = _read_key()
            if key in ("q", "Q", "esc"):
                break
            if not on_key(key):
                break
    finally:
        sys.stdout.write(ALT_OFF)
        sys.stdout.flush()


def _look_pages() -> list[tuple[str, str, tuple[int, int, int], list[str]]]:
    palette = [
        "This is the LocalPilot page. It takes the whole terminal, then waits.",
        "",
        f"{_chip('copper', RGB_COPPER)}  stay on the machine (local)",
        f"{_chip('steel', RGB_STEEL)}  leave the box (cloud)",
        f"{_chip('moss', RGB_MOSS)}  runtime up / headroom ok",
        f"{_chip('oxide', RGB_OXIDE)}  lock, fail, or VRAM tight",
        "",
        "Header bar = copper. Body = workshop brown. Footer = keys.",
        "Doctor, models, route, and lock use this same frame.",
    ]
    doctor = [
        "profile     windows_native",
        "vram        10257 / 12227 MB  (83.9%)",
        "",
        f"ollama      {_fg(RGB_MOSS)}up{_fg(RGB_INK)}",
        "loaded      qwen3:8b",
        f"llama.cpp   {_fg(RGB_MOSS)}up{_fg(RGB_INK)}",
        "gguf        Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
        f"lock        {_fg(RGB_COPPER)}ollama / qwen3:8b{_fg(RGB_INK)}",
        "",
        "- one 8B router resident is enough at 10 GB",
        "- run warmup after a cold start",
    ]
    local_route = [
        f"route       {_fg(RGB_COPPER)}local{_fg(RGB_INK)}",
        "why         Cheap lexical gate skipped the LLM.",
        "path        cheap_router_direct",
        "confidence  1.0 (rule)",
        "latency_ms  0.02",
        "model       cheap_router_nb",
    ]
    cloud_route = [
        f"route       {_fg(RGB_STEEL)}cloud{_fg(RGB_INK)}",
        "why         Capability-risk rule forced cloud.",
        "path        capability_risk_cloud_rule",
        "confidence  0.99 (rule)",
        "latency_ms  0.1",
        "model       qwen3:8b",
    ]
    lock = [
        f"status      {_fg(RGB_OXIDE)}held{_fg(RGB_INK)}",
        "runtime     ollama",
        "model       qwen3:8b",
        "",
        "single-model lock is on. Unlock only when you mean to swap.",
    ]
    return [
        ("look", "local first", RGB_HEADER_INK, palette),
        ("doctor", "clear", RGB_MOSS, doctor),
        ("route", "local", RGB_COPPER, local_route),
        ("route", "cloud", RGB_STEEL, cloud_route),
        ("lock", "held", RGB_OXIDE, lock),
    ]


def run_look_app() -> int:
    pages = _look_pages()
    idx = {"i": 0}

    def draw() -> list[str]:
        cols, rows = _size()
        title, status, tone, body = pages[idx["i"]]
        footer = f"q quit   ← → screens   {idx['i'] + 1}/{len(pages)}"
        return build_screen(title, status, tone, body, footer, cols, rows)

    def on_key(key: str) -> bool:
        if key in ("right", "l", "n", " ", "enter"):
            idx["i"] = (idx["i"] + 1) % len(pages)
        elif key in ("left", "h", "p"):
            idx["i"] = (idx["i"] - 1) % len(pages)
        elif key.isdigit() and 1 <= int(key) <= len(pages):
            idx["i"] = int(key) - 1
        return True

    _run_loop(draw, on_key)
    return 0


def run_page_app(
    title: str,
    status: str,
    status_rgb: tuple[int, int, int],
    body: list[str],
) -> int:
    def draw() -> list[str]:
        cols, rows = _size()
        return build_screen(title, status, status_rgb, body, "q quit", cols, rows)

    def on_key(_key: str) -> bool:
        return True

    _run_loop(draw, on_key)
    return 0


def page_is_openable() -> bool:
    if os.environ.get("LOCALPILOT_PLAIN"):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def tone_for_route(route: str | None) -> tuple[int, int, int]:
    if route == "local":
        return RGB_COPPER
    if route == "cloud":
        return RGB_STEEL
    return RGB_OXIDE
