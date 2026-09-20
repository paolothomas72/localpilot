from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static, TabbedContent, TabPane
from textual.worker import Worker, WorkerState

from . import __version__ as APP_VERSION
from .core import save_json
from .explain import (
    HELP_TEXT,
    about_text,
    decision_steps,
    gate_panel,
    model_meta,
    model_result_block,
    notes_blurb,
    release_notes,
    runtime_label,
    update_text,
)
from .graphs import disk_card, format_activity, format_disks, line_graph, mini_meters, smooth_series, sparkline
from .local_ops import clear_lock, read_lock, write_lock
from .model_board import append_route_history, inspect_block, kind_of, score_text
from .router import route_task
from .runtime_factory import build_adapter, model_for_runtime
from .session import (
    apply_lock,
    collect_live,
    collect_snapshot,
    last_benchmark,
    load_config_optional,
    project_dir,
    route_kwargs,
    short_model,
    warmup,
)
from .tui_tests import (
    COMPAT_PROMPT,
    LARGE_PACK,
    SMALL_PACK,
    SUITE_ORDER,
    SUITE_PACKS,
    SWEEP_PER_MODEL_S,
    SWEEP_TOTAL_S,
    rank_board,
    score_rows,
)


PAGES = ("machine", "models", "route", "runs", "testing", "errors")
THEMES = ("copper", "rgb", "ink")
PAGE_HEADS = {
    "machine": "MACHINE TELEMETRY",
    "models": "MODEL LIST",
    "route": "ROUTE",
    "runs": "RUNS",
    "testing": "TESTING",
    "errors": "ERRORS",
}
PAGE_KEYS = {
    "machine": "1-6 pages   /theme copper|rgb|ink   w warmup   k cheap  n uncertain  m lock   q quit",
    "models": "o Ollama  c llama.cpp   Enter/s switches   u unlock   q quit",
    "route": "type a message  Enter walks the gates   /models /theme /about   q quit",
    "runs": "left = this PC   right = this model   1-6   q quit",
    "testing": "< model >   then single / simple / hard / suite / all models   q quit",
    "errors": "model / interface / server failures   they do not kill the app   1-6   q quit",
}
_BARS = "▁▂▃▄▅▆▇█"
_WORD = "LOCALPILOT"
_WORD_STEM = "LOCALPILO"


def _vram_label(vram: dict[str, Any]) -> tuple[str, float, bool]:
    if not vram.get("available"):
        return "VRAM  unknown", 0.0, False
    used = float(vram.get("vram_used_mb") or 0)
    total = float(vram.get("vram_total_mb") or 1)
    pct = float(vram.get("vram_used_pct") or (100.0 * used / total))
    return f"VRAM  {used / 1024:.1f}/{total / 1024:.1f} GB  ({pct:.0f}%)", pct, pct >= 90


def _pills_text(snap: dict[str, Any]) -> str:
    vram = snap.get("vram") or {}
    ollama = snap.get("ollama") or {}
    llamacpp = snap.get("llamacpp") or {}
    label, _, _ = _vram_label(vram)
    gpu = vram.get("gpu_util_pct")
    gpu_txt = f"GPU {gpu:.0f}%" if isinstance(gpu, (int, float)) else "GPU n/a"
    host = snap.get("host") or {}
    disks = host.get("disk_count")
    disk_txt = f"  ·  {disks} disks" if disks else ""
    oll = "ollama up" if ollama.get("alive") else "ollama down"
    llc = "llama.cpp up" if llamacpp.get("alive") else "llama.cpp down"
    return f"{label}  ·  {gpu_txt}{disk_txt}    {oll}    {llc}"


def _spark(samples: list[float], width: int = 28) -> str:
    if not samples:
        return "·" * width
    vals = list(samples)[-width:]
    out = []
    for v in vals:
        idx = max(0, min(7, int(float(v) / 100.0 * 7)))
        out.append(_BARS[idx])
    return "".join(out).ljust(width, "·")


def _onoff(flag: bool) -> str:
    return "ON " if flag else "off"


def _error_bucket(title: str, detail: str) -> str:
    blob = f"{title}\n{detail}".lower()
    if any(w in blob for w in ("ollama", "llama.cpp", "llama-server", "server", "warmup blocked", "lock blocked")):
        return "server"
    if any(w in blob for w in ("route", "classify", "model", "compat", "sweep", "suite")):
        return "model"
        return "interface"


class NotesScreen(ModalScreen[None]):
    """Full release notes. Click Notes on Machine to open."""

    BINDINGS = [Binding("escape", "dismiss", "close", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(id="notes-modal"):
            yield Static(release_notes(APP_VERSION), id="notes-full")
            yield Button("close", id="close-notes")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-notes":
            self.dismiss()


class LocalPilotApp(App[int]):
    """Persistent operator console. Six pages. Route is the chat."""

    CSS_PATH = Path(__file__).with_name("app.tcss")
    TITLE = "localpilot"
    BINDINGS = [
        Binding("q", "quit", "quit", show=False),
        Binding("ctrl+q", "quit", "quit", show=False, priority=True),
        Binding("1", "go_machine", "machine", show=False),
        Binding("2", "go_models", "models", show=False),
        Binding("3", "go_route", "route", show=False),
        Binding("4", "go_runs", "runs", show=False),
        Binding("5", "go_testing", "testing", show=False),
        Binding("6", "go_errors", "errors", show=False),
        Binding("ctrl+1", "go_machine", "machine", show=False, priority=True),
        Binding("ctrl+2", "go_models", "models", show=False, priority=True),
        Binding("ctrl+3", "go_route", "route", show=False, priority=True),
        Binding("ctrl+4", "go_runs", "runs", show=False, priority=True),
        Binding("ctrl+5", "go_testing", "testing", show=False, priority=True),
        Binding("ctrl+6", "go_errors", "errors", show=False, priority=True),
        Binding("w", "warmup", "warmup", show=False),
        Binding("t", "run_test", "test", show=False),
        Binding("a", "sweep_models", "sweep", show=False),
        Binding("s", "lock_selected", "switch", show=False),
        Binding("l", "lock_selected", "lock", show=False),
        Binding("u", "unlock", "unlock", show=False),
        Binding("o", "focus_ollama", "ollama", show=False),
        Binding("c", "c_key", "c", show=False),
        Binding("k", "toggle_cheap", "cheap", show=False),
        Binding("n", "toggle_uncertain", "uncertain", show=False),
        Binding("m", "toggle_lockflag", "lockflag", show=False),
        Binding("b", "toggle_warmup_flag", "warmupflag", show=False),
        Binding("escape", "blur_composer", "blur", show=False, priority=True),
    ]

    def __init__(
        self,
        project: str | Path | None = None,
        page: str = "machine",
        route_text: str | None = None,
        auto_warmup: bool = False,
        auto_unlock: bool = False,
    ) -> None:
        super().__init__()
        self.project = project_dir(str(project) if project else None)
        self.cfg = load_config_optional(self.project)
        if page == "lock":
            page = "models"
        self.start_page = page if page in PAGES else "machine"
        self.route_text = route_text
        self.auto_warmup = auto_warmup
        self.auto_unlock = auto_unlock
        self.snap: dict[str, Any] = {}
        self.history: list[dict[str, Any]] = []
        self.unlock_armed = False
        self.busy = False
        self.lock_runtime = "ollama"
        self.model_filter: str | None = None
        self.inspect_key = ""
        self.errors: list[str] = []
        self.pending_task = ""
        self.pending_test = ""
        self.route_inflight = False
        self._queued_route = ""
        self.picker_active = False
        self._live_busy = False
        self._wm_i = 0
        self._wm_hold = 0
        self._wm_phase = "type"
        self.test_note = "single · simple · hard · suite · all models"
        self.leaderboard: list[dict[str, Any]] = []
        self.activity_log: deque[str] = deque(maxlen=14)
        self._seen_loaded: list[str] = []
        self._wm_timer = None
        self._cpu_skip = 2
        self._series_len = 96
        self.series: dict[str, deque[float]] = {
            "vram": deque(maxlen=self._series_len),
            "cpu": deque(maxlen=self._series_len),
            "ram": deque(maxlen=self._series_len),
            "disk": deque(maxlen=self._series_len),
            "gpu": deque(maxlen=self._series_len),
            "model_cpu": deque(maxlen=self._series_len),
            "disk_C": deque(maxlen=self._series_len),
            "disk_D": deque(maxlen=self._series_len),
        }

    def compose(self) -> ComposeResult:
        with Vertical(id="chrome"):
            with Horizontal(id="chrome-top"):
                yield Static("█  L", id="wordmark")
                yield Static("", id="pills")
            yield Static("no model locked", id="model-banner")
        with TabbedContent(initial="machine", id="tabs"):
            with TabPane("Machine", id="machine"):
                with Vertical(classes="page"):
                    yield Static(PAGE_HEADS["machine"], classes="page-head")
                    with Horizontal(id="machine-row1"):
                        with Vertical(classes="panel box"):
                            yield Static("TELEMETRY", classes="box-head")
                            yield Static("", id="tele-box")
                        with Vertical(classes="panel box"):
                            yield Static("IDLE / RESIDENT SPEC", classes="box-head")
                            yield Static("", id="idle-box")
                    with Horizontal(id="machine-row2"):
                        with Vertical(classes="panel box"):
                            yield Static("CURRENT MODEL LIST", classes="box-head")
                            yield Static("", id="list-box")
                        with Vertical(classes="panel box"):
                            yield Static("THIS PC", classes="box-head")
                            yield Static("", id="pc-box")
                    with Horizontal(id="machine-row3"):
                        with Vertical(classes="panel box"):
                            yield Static("FEATURES", classes="box-head")
                            yield Static("", id="feats-box")
                            with Horizontal(id="theme-row"):
                                yield Button("copper", id="theme-copper")
                                yield Button("rgb", id="theme-rgb")
                                yield Button("ink", id="theme-ink")
                        with Vertical(classes="panel box", id="notes-box"):
                            yield Static("NOTES", classes="box-head")
                            yield Static("", id="tips")
                    yield Static("", id="machine-status")
            with TabPane("Models", id="models"):
                with Vertical(classes="page"):
                    yield Static(PAGE_HEADS["models"], classes="page-head")
                    with Horizontal(id="models-split"):
                        with Vertical(id="models-left"):
                            with Vertical(classes="panel"):
                                yield Static("ACTIVE MODEL", classes="box-head")
                                yield Static("", id="models-current")
                            yield DataTable(id="models-table", cursor_type="row")
                            yield Static("arrow a row   Enter or s switches LocalPilot onto that model", id="models-hint")
                        with Vertical(id="models-inspect", classes="panel"):
                            yield Static("SELECTED MODEL", classes="box-head")
                            yield Static("", id="models-inspect-body")
            with TabPane("Route", id="route"):
                with Vertical(classes="page"):
                    yield Static(PAGE_HEADS["route"], classes="page-head")
                    with Horizontal(id="route-split"):
                        with Vertical(id="you-col", classes="panel"):
                            yield Static("YOU", classes="box-head")
                            yield Static("", id="you-log")
                            yield Static("", id="route-live", classes="meter")
                        with Vertical(id="model-col", classes="panel"):
                            yield Static("MODEL", classes="box-head")
                            yield Static("", id="model-meta")
                            yield Static("", id="model-result")
                            yield Static("", id="gates-box")
            with TabPane("Runs", id="runs"):
                with Vertical(classes="page"):
                    yield Static(PAGE_HEADS["runs"], classes="page-head")
                    with Horizontal(id="runs-split"):
                        with Vertical(id="runs-left"):
                            yield Static("", id="graph-cpu", classes="panel graph")
                            yield Static("", id="graph-gpu", classes="panel graph")
                            yield Static("", id="graph-ram", classes="panel graph")
                            with Horizontal(id="disk-row"):
                                yield Static("", id="disk-c", classes="panel graph")
                                yield Static("", id="disk-d", classes="panel graph")
                        with Vertical(id="runs-right"):
                            yield Static("", id="runs-model", classes="panel")
                            yield Static("", id="runs-activity", classes="panel")
                            yield Static("", id="runs-log", classes="panel")
            with TabPane("Testing", id="testing"):
                with Vertical(classes="page"):
                    yield Static(PAGE_HEADS["testing"], classes="page-head")
                    yield Static("", id="test-status", classes="panel")
                    yield Static("", id="test-live", classes="panel meter")
                    yield Static("", id="test-models", classes="panel")
                    yield Static("", id="test-result", classes="panel")
                    yield DataTable(id="board-table", cursor_type="row")
            with TabPane("Errors", id="errors"):
                with Vertical(classes="page"):
                    yield Static(PAGE_HEADS["errors"], classes="page-head")
                    yield Static("no errors yet", id="errors-body", classes="panel")
        with Vertical(id="footer-stack"):
            yield Input(placeholder="YOU  ·  type a message  ·  /models to switch", id="composer")
            with Vertical(id="test-actions"):
                with Horizontal(id="test-model-row"):
                    yield Button("< model", id="test-model-prev")
                    yield Static("no model", id="test-model-label")
                    yield Button("model >", id="test-model-next")
                with Horizontal(id="test-kind-row"):
                    yield Button("single", id="test-single")
                    yield Button("simple", id="test-simple")
                    yield Button("hard", id="test-hard")
                    yield Button("suite", id="test-suite")
                    yield Button("all models", id="test-sweep")
            yield Static(PAGE_KEYS["machine"], id="keys")

    def on_mount(self) -> None:
        self._load_error_log()
        self.refresh_snapshot()
        self.refresh_live()
        self.show_page(self.start_page)
        self.set_interval(0.3, self.refresh_live)
        self.set_interval(2.0, self.refresh_snapshot)
        self._wm_timer = self.set_interval(0.076, self._type_wordmark)
        self._smooth_tab_bar()
        self._apply_theme(str((self.cfg.get("ui") or {}).get("theme") or "copper"), persist=False)
        self.query_one("#tabs", TabbedContent).focus()
        existing = self.snap.get("lock") if self.snap else None
        if existing and existing.get("runtime") in ("ollama", "llamacpp"):
            self.lock_runtime = str(existing.get("runtime"))
        if self.auto_unlock:
            clear_lock(self.project)
            self.unlock_armed = False
            self.refresh_snapshot()
            self.show_page("models")
            self.query_one("#models-hint", Static).update("lock cleared")
        if self.auto_warmup:
            self.action_warmup()
        if self.route_text:
            self.begin_route(self.route_text)

    def _smooth_tab_bar(self) -> None:
        try:
            from textual.widgets._tabs import Underline

            bar = self.query_one(Underline)
            orig = bar.animate

            def animate(name, *args, **kwargs):
                if name in ("highlight_start", "highlight_end"):
                    kwargs["duration"] = 0.72
                    kwargs["easing"] = "in_out_cubic"
                return orig(name, *args, **kwargs)

            bar.animate = animate  # type: ignore[method-assign]
        except Exception:
            pass

    def _type_wordmark(self) -> None:
        mark = self.query_one("#wordmark", Static)
        if self._wm_phase == "done":
            return
        if self._wm_phase == "type":
            self._wm_i = min(len(_WORD_STEM), self._wm_i + 1)
            shown = _WORD_STEM[: self._wm_i]
            mark.update(f"█  {shown}_")
            mark.remove_class("pulse")
            if self._wm_i >= len(_WORD_STEM):
                self._wm_phase = "pause"
                self._wm_hold = 0
            return
        if self._wm_phase == "pause":
            mark.update(f"█  {_WORD_STEM}_")
            self._wm_hold += 1
            if self._wm_hold >= 13:
                self._wm_phase = "t"
            return
        mark.update(f"█  {_WORD}")
        mark.remove_class("pulse")
        self._wm_phase = "done"
        if self._wm_timer is not None:
            self._wm_timer.stop()
            self._wm_timer = None

    def show_page(self, name: str) -> None:
        if name == "lock":
            name = "models"
        if name not in PAGES:
            return
        self.start_page = name
        tabs = self.query_one("#tabs", TabbedContent)
        if tabs.active != name:
            tabs.active = name
        self.query_one("#keys", Static).update(PAGE_KEYS[name])
        self._sync_composer()
        if name == "models":
            self.fill_models()
        elif name == "route":
            self.fill_chat()
            self.fill_route_live()
        elif name == "runs":
            self.fill_runs()
        elif name == "testing":
            self.fill_testing()
        elif name == "errors":
            self.fill_errors()

    def _sync_composer(self) -> None:
        inp = self.query_one("#composer", Input)
        actions = self.query_one("#test-actions", Vertical)
        foot = self.query_one("#footer-stack", Vertical)
        if self.start_page == "route":
            inp.display = True
            inp.disabled = False
            inp.placeholder = "YOU  ·  type a message  ·  /models /theme"
            actions.display = False
            foot.styles.height = 5
        elif self.start_page == "testing":
            inp.display = False
            inp.disabled = True
            actions.display = True
            foot.styles.height = 8
            self._paint_test_model()
        elif self.start_page == "machine":
            inp.display = True
            inp.disabled = False
            inp.placeholder = "/theme copper | rgb | ink"
            actions.display = False
            foot.styles.height = 5
        else:
            inp.display = False
            inp.disabled = True
            actions.display = False
            foot.styles.height = 2

    def _note(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.activity_log.appendleft(f"{stamp}  {text}")

    def _lock_label(self) -> tuple[str, str]:
        lock = (self.snap.get("lock") if self.snap else None) or read_lock(self.project)
        if lock and lock.get("runtime") and lock.get("model"):
            return str(lock["runtime"]), str(lock["model"])
        return "", ""

    def _paint_banner(self) -> None:
        runtime, model = self._lock_label()
        activity = (self.snap.get("activity") or {}) if self.snap else {}
        vram = (self.snap.get("vram") or {}) if self.snap else {}
        gpu = vram.get("gpu_util_pct")
        pushing = bool(self.busy or activity.get("working") or (isinstance(gpu, (int, float)) and gpu >= 15))
        banner = self.query_one("#model-banner", Static)
        banner.set_class(pushing, "working")
        banner.set_class(not pushing, "idle")
        if not model:
            banner.update("no model locked  ·  /models on Route or Testing")
            return
        state = "WORKING on this PC" if pushing else "resident / idle"
        banner.update(f"{state}   {runtime} / {model}")

    def _record_live(self, vram: dict[str, Any], host: dict[str, Any], activity: dict[str, Any]) -> None:
        _, pct, tight = _vram_label(vram)
        self.series["vram"].append(pct)
        if host.get("available"):
            cpu = float(host.get("cpu_pct") or 0)
            if self._cpu_skip > 0:
                self._cpu_skip -= 1
            else:
                self.series["cpu"].append(cpu)
            self.series["ram"].append(float(host.get("ram_pct") or 0))
            self.series["disk"].append(float(host.get("disk_pct") or 0))
        self.series["gpu"].append(float(vram.get("gpu_util_pct") or 0))
        self.series["model_cpu"].append(float(activity.get("cpu_machine_pct") or 0))
        for disk in host.get("disks") or []:
            letter = str(disk.get("letter") or "")
            key = f"disk_{letter}"
            if key not in self.series:
                self.series[key] = deque(maxlen=self._series_len)
            self.series[key].append(float(disk.get("pct") or 0))
        pills = self.query_one("#pills", Static)
        pills.update(_pills_text(self.snap))
        pills.set_class(tight, "tight")
        self._paint_banner()

    def refresh_live(self) -> None:
        if self._live_busy:
            return
        self._live_busy = True
        try:
            live = collect_live()
            if not self.snap:
                self.snap = {}
            self.snap["vram"] = live["vram"]
            self.snap["host"] = live["host"]
            self.snap["activity"] = live.get("activity") or {}
            self._record_live(live["vram"], live["host"], live.get("activity") or {})
            self._paint_live()
        finally:
            self._live_busy = False

    def _paint_live(self) -> None:
        try:
            self.fill_machine()
            self.fill_runs()
            self.fill_testing_live()
            self.fill_route_live()
        except Exception:
            pass

    def refresh_snapshot(self) -> None:
        live_vram = (self.snap or {}).get("vram")
        live_host = (self.snap or {}).get("host")
        live_activity = (self.snap or {}).get("activity")
        self.snap = collect_snapshot(self.project, self.cfg)
        if live_vram:
            self.snap["vram"] = live_vram
        if live_host:
            self.snap["host"] = live_host
        if live_activity:
            self.snap["activity"] = live_activity
        loaded = list((self.snap.get("ollama") or {}).get("loaded") or [])
        if loaded != self._seen_loaded:
            if loaded:
                self._note("Ollama loaded  " + ", ".join(loaded))
            elif self._seen_loaded:
                self._note("Ollama unloaded  " + ", ".join(self._seen_loaded))
            self._seen_loaded = loaded
        self._paint_live()

    def _save_cfg(self) -> None:
        path = self.project / "localpilot.config.json"
        if path.exists():
            save_json(path, self.cfg)
        self.refresh_snapshot()
        self.fill_machine()

    def _theme_name(self) -> str:
        name = str((self.cfg.get("ui") or {}).get("theme") or "copper").lower()
        return name if name in THEMES else "copper"

    def _apply_theme(self, name: str, persist: bool = True) -> None:
        name = name if name in THEMES else "copper"
        for theme in THEMES:
            self.screen.remove_class(f"theme-{theme}")
        if name != "copper":
            self.screen.add_class(f"theme-{name}")
        self.cfg.setdefault("ui", {})["theme"] = name
        if persist:
            path = self.project / "localpilot.config.json"
            if path.exists():
                save_json(path, self.cfg)
            try:
                self.fill_machine()
            except Exception:
                pass
        self._note(f"theme  {name}")

    def _paint_test_model(self) -> None:
        try:
            runtime, model = self._active_runtime_model()
            label = f"{runtime_label(runtime)}  /  {model}" if model else "no model  ·  < model >"
            self.query_one("#test-model-label", Static).update(label)
        except Exception:
            pass

    def _cycle_test_model(self, delta: int) -> None:
        found = self._candidate_models()
        if not found:
            self.log_error("no models", "start Ollama or llama.cpp first")
            return
        runtime, model = self._active_runtime_model()
        current = (runtime, model)
        idx = 0
        for i, pair in enumerate(found):
            if pair == current:
                idx = i
                break
        nxt = found[(idx + delta) % len(found)]
        write_lock(self.project, nxt[0], nxt[1])
        self.lock_runtime = nxt[0]
        self.refresh_snapshot()
        self._paint_test_model()
        self.fill_testing()
        self._note(f"test model  {nxt[0]} / {nxt[1]}")

    def fill_machine(self) -> None:
        snap = self.snap
        vram = snap.get("vram") or {}
        ollama = snap.get("ollama") or {}
        llamacpp = snap.get("llamacpp") or {}
        activity = snap.get("activity") or {}
        host = snap.get("host") or {}
        label, _, _ = _vram_label(vram)
        gpu_name = vram.get("name") or "GPU"
        gpu = vram.get("gpu_util_pct")
        gpu_txt = f"{float(gpu):.0f}%" if isinstance(gpu, (int, float)) else "n/a"
        host_line = ""
        if host.get("available"):
            host_line = (
                f"CPU {host.get('cpu_pct'):.0f}%   "
                f"RAM {host.get('ram_used_gb')}/{host.get('ram_total_gb')} GB"
            )
        runtime, model = self._lock_label()
        loaded = list(ollama.get("loaded") or [])
        vals = list(self.series["vram"])
        climbing = False
        if len(vals) >= 4:
            recent = vals[-6:]
            climbing = (max(recent) - min(recent)) > 4
        resident = bool(loaded) or bool(llamacpp.get("alive") and (llamacpp.get("models") or []))
        loading = bool(self.busy or activity.get("working") or (climbing and not loaded))
        if resident and not loading:
            vram_state = "  [#7cb87c]LOADED[/]"
            vram_spark = ""
        else:
            vram_state = "  [#e8a040]loading[/]"
            vram_spark = f"\nvram  {_spark(vals)}"

        self.query_one("#tele-box", Static).update(
            f"[#e8a040]{gpu_name}[/]\n"
            f"{label}{vram_state}\n"
            f"GPU SM  [#e8a040]{gpu_txt}[/]  (whole card — Windows does not split per process)\n"
            f"{host_line}\n"
            f"{format_disks(host)}"
            f"{vram_spark}"
        )

        details = {str(row.get("name")): row for row in (ollama.get("loaded_detail") or [])}
        if loaded:
            bits = []
            for name in loaded:
                detail = details.get(name) or {}
                weight = detail.get("vram_gb")
                extra = f"  {weight:.1f} GB VRAM" if isinstance(weight, (int, float)) else ""
                bits.append(f"  [#7cb87c]{name}[/]{extra}")
            oll_res = "[#7cb87c]Ollama loaded[/]\n" + "\n".join(bits)
        else:
            oll_res = "Ollama loaded\n  [#a89888]none  (idle — nothing resident)[/]"
        ggufs = list(llamacpp.get("models") or [])
        if llamacpp.get("alive") and ggufs:
            llc_res = "[#7cb87c]llama.cpp loaded[/]\n" + "\n".join(f"  {short_model(n)}" for n in ggufs[:4])
        elif llamacpp.get("alive"):
            llc_res = "[#7cb87c]llama.cpp loaded[/]\n  server up  ·  no GGUF listed"
        else:
            llc_res = "[#c45c4a]llama.cpp down[/]"
        state = "[#e8a040]WORKING[/]" if (self.busy or activity.get("working")) else "[#7cb87c]IDLE[/]"
        oll_up = "[#7cb87c]UP[/]" if ollama.get("alive") else "[#c45c4a]DOWN[/]"
        llc_up = "[#7cb87c]UP[/]" if llamacpp.get("alive") else "[#c45c4a]DOWN[/]"
        self.query_one("#idle-box", Static).update(
            f"{state}\n"
            f"Ollama     {oll_up}\n{oll_res}\n"
            f"llama.cpp  {llc_up}\n{llc_res}"
        )

        oll_models = list(ollama.get("models") or [])
        llc_models = list(llamacpp.get("models") or [])
        self.query_one("#list-box", Static).update(
            f"active   [#e8a040]{runtime + ' / ' + model if model else 'none locked'}[/]\n"
            f"Ollama   [#7cb87c]{len(oll_models)}[/]  "
            + (", ".join(short_model(n) for n in oll_models[:3]) or "none")
            + (" …" if len(oll_models) > 3 else "")
            + "\n"
            f"llama.cpp [#7cb87c]{len(llc_models)}[/]  "
            + (", ".join(short_model(n) for n in llc_models[:3]) or "none")
            + "\n"
            "full list lives on MODEL LIST (page 2)"
        )

        self.query_one("#pc-box", Static).update(
            f"[#e8a040]{snap.get('profile') or 'unknown'}[/]\n"
            f"LocalPilot  [#e8a040]{APP_VERSION}[/]\n"
            f"{gpu_name}\n"
            f"{format_disks(host)}\n"
            "[#c45c4a]do not use the AMD iGPU for inference[/]"
        )

        cheap = bool((self.cfg.get("policy") or {}).get("cheap_router", {}).get("enabled", False))
        uncertain = bool((self.cfg.get("policy") or {}).get("uncertain_band", {}).get("low_confidence_to_cloud", True))
        lock_on = bool((self.cfg.get("local") or {}).get("single_model_lock", True))
        warm_on = bool((self.cfg.get("local") or {}).get("warmup_on_route", False))
        keep = (self.cfg.get("local") or {}).get("keep_alive") or "off"
        two = bool((self.cfg.get("policy") or {}).get("two_axis", {}).get("enabled", True))
        def _flag(on: bool) -> str:
            return "[#7cb87c]ON[/] " if on else "[#a89888]off[/]"

        self.query_one("#feats-box", Static).update(
            "[#a89888]real gates — not fake turbo[/]\n"
            f"(k) cheap router      {_flag(cheap)}  skip LLM on easy/known\n"
            f"(n) uncertain band    {_flag(uncertain)}  weak local → CLOUD\n"
            f"(m) single-model lock {_flag(lock_on)}  one model answers\n"
            f"(b) warmup on route   {_flag(warm_on)}  first classify warmer\n"
            f"    keep-alive        [#e8a040]{keep}[/]   stay resident\n"
            f"    two-axis rules    {_flag(two)}  hard/privacy skip classify\n"
            f"    theme             [#e8a040]{self._theme_name()}[/]   /theme or the buttons\n"
            "w still runs a warmup now"
        )
        self.query_one("#tips", Static).update(notes_blurb(APP_VERSION))

    def fill_models(self) -> None:
        table = self.query_one("#models-table", DataTable)
        if not table.columns:
            table.add_columns("source", "model", "kind", "gate", "loaded", "active")
        table.clear()
        lock = (self.snap.get("lock") or {}) if self.snap else {}
        current = f"{lock.get('runtime')} / {lock.get('model')}" if lock else "none — pick a row and press Enter"
        scores = (self.snap.get("scores") or {}) if self.snap else {}
        hint = ""
        if lock and lock.get("model"):
            mine = scores.get(str(lock.get("model")))
            if mine:
                hint = f"\ngate pack  {score_text(mine)}  from {mine.get('source')}"
            else:
                hint = "\nno pack yet for this lock  ·  a new user sees this empty"
        filt = f"\nfilter  {self.model_filter}" if self.model_filter else ""
        self.query_one("#models-current", Static).update(
            f"[#e8a040]{current}[/]{hint}{filt}\n"
            "source is Ollama or llama.cpp   Enter or s switches"
        )
        ollama = self.snap.get("ollama") or {}
        llamacpp = self.snap.get("llamacpp") or {}
        loaded = set(ollama.get("loaded") or [])
        rows: list[tuple[str, str, str]] = []
        if self.model_filter in (None, "ollama"):
            for name in ollama.get("models") or []:
                rows.append(("ollama", str(name), "yes" if name in loaded else "no"))
            for name in loaded:
                if name not in (ollama.get("models") or []):
                    rows.append(("ollama", str(name), "yes"))
        if self.model_filter in (None, "llamacpp"):
            for name in llamacpp.get("models") or []:
                rows.append(("llamacpp", str(name), "yes" if llamacpp.get("alive") else "no"))
        if not rows:
            table.add_row("—", "no models found", "—", "—", "—", "—")
            self.fill_inspect()
            return
        for runtime, model, is_loaded in rows:
            locked = "YES" if lock.get("runtime") == runtime and lock.get("model") == model else ""
            table.add_row(
                runtime,
                short_model(model),
                kind_of(model),
                score_text(scores.get(model)),
                is_loaded,
                locked,
                key=f"{runtime}\t{model}",
            )
        if not self.inspect_key and lock.get("runtime") and lock.get("model"):
            self.inspect_key = f"{lock.get('runtime')}\t{lock.get('model')}"
        self.fill_inspect()

    def fill_inspect(self) -> None:
        scores = (self.snap.get("scores") or {}) if self.snap else {}
        runtime, model = "", ""
        if self.inspect_key and "\t" in self.inspect_key:
            runtime, model = self.inspect_key.split("\t", 1)
        else:
            runtime, model = self._lock_label()
        body = inspect_block(self.project, runtime, model, scores, self.history)
        if body.startswith("SELECTED MODEL\n"):
            body = body.split("\n", 1)[1]
        self.query_one("#models-inspect-body", Static).update(body)

    def _inner_width(self, widget_id: str, fallback: int, pad: int = 8) -> int:
        try:
            width = int(self.query_one(f"#{widget_id}").size.width)
            if width > pad + 16:
                return width - pad
        except Exception:
            pass
        return fallback

    def fill_runs(self) -> None:
        host = (self.snap.get("host") or {}) if self.snap else {}
        vram = (self.snap.get("vram") or {}) if self.snap else {}
        activity = (self.snap.get("activity") or {}) if self.snap else {}
        ollama = (self.snap.get("ollama") or {}) if self.snap else {}
        ram_txt = f"{host.get('ram_used_gb', '?')}/{host.get('ram_total_gb', '?')} GB" if host.get("available") else ""
        vlabel, _, _ = _vram_label(vram)
        runtime, model = self._lock_label()
        scores = (self.snap.get("scores") or {}) if self.snap else {}
        disks = {str(d.get("letter")): d for d in (host.get("disks") or [])}
        raw = self._inner_width("graph-cpu", 56, pad=4)
        gw = max(24, raw - 4)
        dw = max(18, self._inner_width("disk-c", 32, pad=4) - 4)
        gpu_vals = list(self.series["gpu"])
        cpu_vals = smooth_series(list(self.series["cpu"]), window=5)
        self.query_one("#graph-cpu", Static).update(
            line_graph(cpu_vals, title="CPU", height=6, width=gw, pad="stretch")
        )
        self.query_one("#graph-gpu", Static).update(
            line_graph(
                gpu_vals,
                title="GPU",
                height=5,
                width=gw,
                pad="stretch",
                boost=22.0,
                extra=f"{vlabel}  {sparkline(gpu_vals, min(28, gw))}",
            )
        )
        self.query_one("#graph-ram", Static).update(
            line_graph(list(self.series["ram"]), title="RAM", height=5, width=gw, pad="hold")
            + (f"\n{ram_txt}" if ram_txt else "")
        )
        self.query_one("#disk-c", Static).update(
            disk_card(disks.get("C"), "C", list(self.series.get("disk_C") or []), width=dw, height=5)
        )
        self.query_one("#disk-d", Static).update(
            disk_card(disks.get("D"), "D", list(self.series.get("disk_D") or []), width=dw, height=5)
        )
        if model:
            self.query_one("#runs-model", Static).update(
                f"THIS MODEL\n\n{runtime_label(runtime)}\n{model}\n\ngate pack  {score_text(scores.get(model))}"
            )
        else:
            self.query_one("#runs-model", Static).update("THIS MODEL\n\nnone locked")
        self.query_one("#runs-activity", Static).update(
            format_activity(activity, vram, ollama, model or None, self.busy)
        )
        log_lines = ["what it did on this PC", ""]
        if self.activity_log:
            log_lines.extend(list(self.activity_log)[:10])
        else:
            log_lines.append("waiting")
            log_lines.append("open Route and type, or press w to warmup")
        card = last_benchmark(self.project)
        if card:
            summary = card.get("summary") or {}
            log_lines.append(
                f"last bench  {summary.get('runtime')} / {summary.get('router_model')}  "
                f"acc {summary.get('accuracy')}"
            )
        self.query_one("#runs-log", Static).update("\n".join(log_lines))

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        pane_id = event.pane.id if event.pane is not None else None
        if pane_id == "lock":
            pane_id = "models"
        if pane_id in PAGES:
            self.start_page = pane_id
            self.query_one("#keys", Static).update(PAGE_KEYS[pane_id])
            self._sync_composer()
            if pane_id == "models":
                self.fill_models()
            elif pane_id == "route":
                self.fill_chat()
                self.fill_route_live()
            elif pane_id == "runs":
                self.fill_runs()
            elif pane_id == "testing":
                self.fill_testing()
            elif pane_id == "errors":
                self.fill_errors()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "models-table":
            self.action_lock_selected()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "models-table":
            return
        key = event.row_key
        value = getattr(key, "value", key)
        if value and "\t" in str(value):
            self.inspect_key = str(value)
            try:
                self.fill_inspect()
            except Exception:
                pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        task = (event.value or "").strip()
        if not task:
            return
        event.input.value = ""
        if task.startswith("/"):
            self.handle_slash(task)
            return
        if self.start_page != "route":
            self.show_page("route")
        self.begin_route(task)

    def on_click(self, event) -> None:
        node = event.widget
        while node is not None:
            if getattr(node, "id", None) == "notes-box":
                event.stop()
                self.push_screen(NotesScreen())
                return
            node = getattr(node, "parent", None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid in ("theme-copper", "theme-rgb", "theme-ink"):
            self._apply_theme(bid.split("-", 1)[1], persist=True)
            return
        if bid == "test-model-prev":
            self._cycle_test_model(-1)
            return
        if bid == "test-model-next":
            self._cycle_test_model(1)
            return
        if bid == "test-single":
            self._start_test("single", "single test  one easy classify")
        elif bid == "test-simple":
            self._start_test("simple", "simple pack  small local tasks")
        elif bid == "test-hard":
            self._start_test("hard", "hard pack  architecture / security / rollback")
        elif bid == "test-suite":
            self.action_run_test()
        elif bid == "test-sweep":
            self.action_sweep_models()

    def handle_slash(self, raw: str) -> None:
        parts = raw[1:].strip().split()
        cmd = (parts[0] if parts else "").lower()
        arg = parts[1].lower() if len(parts) > 1 else ""
        if cmd in ("models", "model"):
            runtime = arg if arg in ("ollama", "llamacpp", "llama", "llama.cpp") else None
            if runtime in ("llama", "llama.cpp"):
                runtime = "llamacpp"
            self.start_model_picker(runtime)
            return
        if cmd == "ollama":
            self.start_model_picker("ollama")
            return
        if cmd in ("llamacpp", "llama", "llama.cpp"):
            self.start_model_picker("llamacpp")
            return
        if cmd == "route":
            self.show_page("route")
            return
        if cmd == "help":
            self._show_route_info("/help", "commands", HELP_TEXT)
            return
        if cmd == "about":
            self._show_route_info("/about", f"LocalPilot  {APP_VERSION}", about_text(APP_VERSION))
            return
        if cmd == "update":
            self._show_route_info("/update", f"LocalPilot  {APP_VERSION}", update_text(APP_VERSION))
            return
        if cmd == "export":
            path = self._export_last_decision()
            if path:
                self._show_route_info("/export", "wrote last decision", str(path))
            else:
                self._show_route_info("/export", "nothing to export", "route a task first")
            return
        if cmd == "theme":
            if arg in ("color", "next", "cycle", ""):
                cur = self._theme_name()
                nxt = THEMES[(THEMES.index(cur) + 1) % len(THEMES)] if cur in THEMES else "rgb"
                self._apply_theme(nxt, persist=True)
            elif arg in THEMES:
                self._apply_theme(arg, persist=True)
            else:
                self.log_error("unknown theme", "use /theme copper | rgb | ink")
                return
            if self.start_page == "route":
                self._show_route_info("/theme", f"theme  {self._theme_name()}", "copper  rgb  ink\n/theme color cycles")
            else:
                self.show_page("machine")
            return
        self.log_error("unknown command", f"{raw}\ntry /help or /models")

    def _show_route_info(self, you: str, result: str, body: str) -> None:
        self.show_page("route")
        runtime, model = self._lock_label()
        self.query_one("#you-log", Static).update(you)
        self.query_one("#model-meta", Static).update(model_meta(runtime, model))
        self.query_one("#model-result", Static).update(result)
        self.query_one("#gates-box", Static).update(body)

    def _export_last_decision(self) -> Path | None:
        if not self.history:
            return None
        path = self.project / "runs" / "last-decision.json"
        save_json(path, self.history[0])
        return path

    def action_go_machine(self) -> None:
        self.show_page("machine")

    def action_go_models(self) -> None:
        self.show_page("models")

    def action_go_route(self) -> None:
        self.show_page("route")

    def action_go_runs(self) -> None:
        self.show_page("runs")

    def action_go_testing(self) -> None:
        self.show_page("testing")

    def action_go_errors(self) -> None:
        self.show_page("errors")

    def action_blur_composer(self) -> None:
        self.query_one("#tabs", TabbedContent).focus()

    def _toggle_path(self, *keys: str) -> bool:
        cur: Any = self.cfg
        for key in keys[:-1]:
            nxt = cur.get(key)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[key] = nxt
            cur = nxt
        last = keys[-1]
        cur[last] = not bool(cur.get(last, False))
        self._save_cfg()
        self._note(f"{last}  {_onoff(bool(cur[last]))}")
        return bool(cur[last])

    def action_toggle_cheap(self) -> None:
        if self.start_page != "machine":
            return
        self._toggle_path("policy", "cheap_router", "enabled")

    def action_toggle_uncertain(self) -> None:
        if self.start_page != "machine":
            return
        self._toggle_path("policy", "uncertain_band", "low_confidence_to_cloud")

    def action_toggle_lockflag(self) -> None:
        if self.start_page not in ("machine", "models"):
            return
        local = self.cfg.setdefault("local", {})
        on = bool(local.get("single_model_lock", True))
        if on:
            local["single_model_lock"] = False
            self._save_cfg()
            self._note("single-model lock  off")
            if self.start_page == "models":
                self.query_one("#models-hint", Static).update("single-model lock off")
            return
        local["single_model_lock"] = True
        self._save_cfg()
        self._note("single-model lock  ON  pick a model")
        self.start_model_picker(None)
        self.query_one("#models-hint", Static).update(
            "LOCK ON  ·  which model?  arrow a row  Enter locks it"
        )

    def action_toggle_warmup_flag(self) -> None:
        if self.start_page != "machine":
            return
        self._toggle_path("local", "warmup_on_route")

    def log_error(self, title: str, detail: str, jump: bool = True) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        bucket = _error_bucket(title, detail)
        block = f"{stamp}  [{bucket}]  {title}\n{detail}"
        self.errors.insert(0, block)
        self.errors = self.errors[:30]
        try:
            path = self.project / "runs" / "tui-errors.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": stamp, "title": title, "detail": detail, "bucket": bucket}) + "\n")
        except Exception:
            pass
        try:
            self.fill_errors()
            if jump:
                self.show_page("errors")
        except Exception:
            pass

    def fill_errors(self) -> None:
        buckets = {"model": [], "interface": [], "server": []}
        for block in self.errors:
            if "[model]" in block:
                buckets["model"].append(block)
            elif "[server]" in block:
                buckets["server"].append(block)
            else:
                buckets["interface"].append(block)
        if not self.errors:
            self.query_one("#errors-body", Static).update(
                "CURRENT ERRORS\n"
                "none right now\n\n"
                "MODEL       classify / route / test failures\n"
                "INTERFACE   display or TUI worker failures\n"
                "SERVER      Ollama / llama.cpp / lock / warmup"
            )
            return
        parts = ["CURRENT ERRORS", ""]
        for name in ("model", "interface", "server"):
            rows = buckets[name]
            parts.append(name.upper())
            parts.extend(rows[:6] if rows else ["  none"])
            parts.append("")
        self.query_one("#errors-body", Static).update("\n".join(parts))

    def fill_testing(self) -> None:
        lock = (self.snap.get("lock") if self.snap else None) or None
        target = f"{lock.get('runtime')} / {lock.get('model')}" if lock else "no lock — switch a model on Models first"
        self.query_one("#test-status", Static).update(
            f"active  {target}\n"
            f"{self.test_note}\n"
            "pick a model with < model >   then a test button"
        )
        self.fill_testing_live()
        self.fill_board()
        self._paint_test_model()

    def fill_board(self) -> None:
        table = self.query_one("#board-table", DataTable)
        if not table.columns:
            table.add_columns("rank", "source", "model", "score")
        table.clear()
        if not self.leaderboard:
            table.add_row("—", "—", "no sweep yet  ·  press all models", "—")
            return
        for row in self.leaderboard:
            acc = row.get("accuracy")
            acc_txt = f"{float(acc):.0%}" if isinstance(acc, (int, float)) else str(row.get("compat") or "—")
            table.add_row(
                str(row.get("rank") or "—"),
                str(row.get("runtime") or ""),
                short_model(str(row.get("model") or "")),
                acc_txt,
            )

    def fill_route_live(self) -> None:
        try:
            host = (self.snap.get("host") or {}) if self.snap else {}
            vram = (self.snap.get("vram") or {}) if self.snap else {}
            last = self.history[0] if self.history else {}
            pc = last.get("pc") or {}
            self.query_one("#route-live", Static).update(
                mini_meters(
                    float(pc.get("cpu", host.get("cpu_pct") or 0)),
                    float(pc.get("ram", host.get("ram_pct") or 0)),
                    float(pc.get("disk", host.get("disk_pct") or 0)),
                    float(pc.get("gpu", vram.get("gpu_util_pct") or 0)),
                    _vram_label(vram)[0],
                )
            )
        except Exception:
            pass

    def fill_gates(self, decision: dict[str, Any] | None = None) -> None:
        self.query_one("#gates-box", Static).update(gate_panel(decision or (self.history[0] if self.history else None)))

    def fill_chat(self, pending: str | None = None, decision: dict[str, Any] | None = None) -> None:
        runtime, model = self._lock_label()
        if pending is None and self.route_inflight and self.pending_task:
            pending = self.pending_task
        you_blocks: list[str] = []
        for item in reversed(self.history[:8]):
            you_blocks.append(str(item.get("task") or ""))
        if pending:
            you_blocks.append(pending)
        if you_blocks:
            you_body = "\n\n".join(you_blocks)
        else:
            you_body = "type a task below\n\nThis is the routing chat, not a generate() harness."
        self.query_one("#you-log", Static).update(you_body)
        shown = decision if decision is not None else (None if pending else (self.history[0] if self.history else None))
        self.query_one("#model-meta", Static).update(model_meta(runtime, model))
        result = self.query_one("#model-result", Static)
        result.update(model_result_block(shown, pending=bool(pending), runtime=runtime, model=model))
        result.remove_class("local")
        result.remove_class("cloud")
        result.remove_class("loading")
        if pending:
            result.add_class("loading")
        elif shown:
            route = str(shown.get("route") or "").lower()
            if route == "local":
                result.add_class("local")
            elif route == "cloud":
                result.add_class("cloud")
        self.fill_gates(None if pending else shown)

    def fill_transcript(self) -> None:
        self.fill_chat()

    def _load_error_log(self) -> None:
        path = self.project / "runs" / "tui-errors.jsonl"
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-12:]
            loaded = []
            for line in lines:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                bucket = row.get("bucket") or _error_bucket(str(row.get("title") or ""), str(row.get("detail") or ""))
                loaded.append(f"{row.get('at', '?')}  [{bucket}]  {row.get('title')}\n{row.get('detail')}")
            self.errors = list(reversed(loaded)) + self.errors
            self.errors = self.errors[:30]
        except Exception:
            pass

    def _pc_now(self) -> dict[str, float]:
        live = collect_live()
        host = live.get("host") or {}
        vram = live.get("vram") or {}
        return {
            "cpu": float(host.get("cpu_pct") or 0),
            "ram": float(host.get("ram_pct") or 0),
            "disk": float(host.get("disk_pct") or 0),
            "gpu": float(vram.get("gpu_util_pct") or 0),
            "vram": float(vram.get("vram_used_pct") or 0),
        }

    def fill_testing_live(self) -> None:
        try:
            host = (self.snap.get("host") or {}) if self.snap else {}
            vram = (self.snap.get("vram") or {}) if self.snap else {}
            self.query_one("#test-live", Static).update(
                f"now  CPU {float(host.get('cpu_pct') or 0):.0f}%  "
                f"RAM {float(host.get('ram_pct') or 0):.0f}%  "
                f"DISK {float(host.get('disk_pct') or 0):.0f}%  "
                f"GPU {float(vram.get('gpu_util_pct') or 0):.0f}%  "
                f"{_vram_label(vram)[0]}"
            )
            ollama = (self.snap.get("ollama") or {}) if self.snap else {}
            llamacpp = (self.snap.get("llamacpp") or {}) if self.snap else {}
            oll = list(ollama.get("models") or [])
            llc = list(llamacpp.get("models") or [])
            self.query_one("#test-models", Static).update(
                "MODEL LIST\n"
                f"Ollama     {', '.join(short_model(n) for n in oll[:8]) or 'none'}"
                + (" …" if len(oll) > 8 else "")
                + "\n"
                f"llama.cpp  {', '.join(short_model(n) for n in llc[:8]) or 'none'}"
                + (" …" if len(llc) > 8 else "")
            )
        except Exception:
            pass

    def _active_runtime_model(self) -> tuple[str, str]:
        lock = (self.snap.get("lock") if self.snap else None) or read_lock(self.project)
        if lock and lock.get("runtime") and lock.get("model"):
            return str(lock["runtime"]), str(lock["model"])
        rt = str(self.cfg.get("runtime") or "ollama").lower()
        model = model_for_runtime(self.cfg, rt, None, "router") or ""
        return rt, model

    def begin_route(self, task: str) -> None:
        self.pending_task = task
        self.route_inflight = True
        if self.start_page != "route":
            self.show_page("route")
        self.fill_chat(pending=task)
        self._note(f"gate walk  {task[:72]}")
        if self.busy:
            self._queued_route = task
            return
        self._queued_route = ""
        self.run_worker(self._route_worker, exclusive=True, thread=True, exit_on_error=False)

    def _route_worker(self) -> None:
        task = self.pending_task
        self.busy = True
        decision: dict[str, Any] = {"ok": False, "route": None, "error": "route did not start", "task": task}
        try:
            cfg = self.cfg
            if not cfg.get("policy"):
                decision = {"ok": False, "route": None, "error": "no localpilot.config.json", "task": task}
                self.call_from_thread(self.log_error, "route failed", "no localpilot.config.json")
            else:
                runtime_name, router_model = self._active_runtime_model()
                ok, err, fallback = apply_lock(self.project, cfg, runtime_name, router_model or "", False)
                if not ok:
                    decision = {"ok": False, "route": None, "error": err, "task": task}
                    self.call_from_thread(self.log_error, "route blocked", err or "lock blocked")
                else:
                    adapter = build_adapter(cfg, runtime_override=runtime_name)
                    try:
                        decision = route_task(
                            adapter=adapter,
                            task=task,
                            router_model=router_model,
                            **route_kwargs(cfg, self.project, fallback),
                        )
                        decision["task"] = task
                        decision["locked_model"] = f"{runtime_name} / {router_model}"
                        decision["reply"] = "\n".join(decision_steps(decision))
                    finally:
                        adapter.close()
                    decision["pc"] = self._pc_now()
                    if not decision.get("ok"):
                        self.call_from_thread(self.log_error, "route failed", str(decision.get("error") or "unknown"))
        except Exception as exc:  # noqa: BLE001
            decision = {"ok": False, "route": None, "error": str(exc), "task": task}
            self.call_from_thread(self.log_error, "route crashed", str(exc))
        self.busy = False
        self.call_from_thread(self._after_route, decision)

    def _after_route(self, decision: dict[str, Any]) -> None:
        try:
            decision["at"] = datetime.now().strftime("%H:%M:%S")
            self.history.insert(0, decision)
            self.history = self.history[:20]
            append_route_history(self.project, decision)
            queued = self._queued_route
            just = str(decision.get("task") or "")
            if queued and queued != just:
                self.route_inflight = True
                self.pending_task = queued
                self.paint_decision(decision)
                self.fill_runs()
                self.fill_chat(pending=queued)
                self._queued_route = ""
                self.run_worker(self._route_worker, exclusive=True, thread=True, exit_on_error=False)
                return
            self.route_inflight = False
            self.pending_task = ""
            self._queued_route = ""
            self.paint_decision(decision)
            self.fill_runs()
        except Exception as exc:  # noqa: BLE001
            self.log_error("display failed", str(exc))

    def paint_decision(self, decision: dict[str, Any]) -> None:
        self.fill_chat(decision=decision)
        self.fill_route_live()
        route = decision.get("route")
        self._note(
            f"{route or 'fail'}  {decision.get('policy_path') or '?'}  "
            f"{decision.get('latency_ms')}ms"
        )

    def action_warmup(self) -> None:
        if self.busy:
            return
        self.show_page("machine")
        self.query_one("#machine-status", Static).update("warming...")
        self.run_worker(self._warmup_worker, exclusive=True, thread=True, exit_on_error=False)

    def _warmup_worker(self) -> None:
        self.busy = True
        try:
            cfg = self.cfg
            runtime_name, model = self._active_runtime_model()
            ok, err, _ = apply_lock(self.project, cfg, runtime_name, model or "", False)
            if not ok:
                msg = err or "lock blocked warmup"
                self.call_from_thread(self.log_error, "warmup blocked", msg)
            else:
                adapter = build_adapter(cfg, runtime_override=runtime_name)
                try:
                    times = warmup(adapter, model or "", int(cfg.get("local", {}).get("warmup_calls", 1)))
                finally:
                    adapter.close()
                last = times[-1] if times else 0
                cold = bool(times and times[0] > 5000)
                msg = f"{'cold first hit' if cold else 'warm'}  {last:.0f} ms"
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            self.call_from_thread(self.log_error, "warmup crashed", msg)
        self.busy = False
        self.call_from_thread(self._after_warmup, msg)

    def _after_warmup(self, msg: str) -> None:
        self.query_one("#machine-status", Static).update(msg)
        self._note(f"warmup  {msg}")
        self.refresh_snapshot()

    def start_model_picker(self, runtime: str | None) -> None:
        self.picker_active = True
        self.model_filter = runtime if runtime in ("ollama", "llamacpp") else None
        if runtime in ("ollama", "llamacpp"):
            self.lock_runtime = runtime
        self.show_page("models")
        self.fill_models()
        if runtime in ("ollama", "llamacpp"):
            self.query_one("#models-hint", Static).update(
                f"pick a {runtime} model   Enter locks it and opens Route"
            )
        else:
            self.query_one("#models-hint", Static).update(
                "o = Ollama   c = llama.cpp   Enter locks and opens Route"
            )

    def action_focus_ollama(self) -> None:
        if self.start_page not in ("models", "route", "testing"):
            return
        self.lock_runtime = "ollama"
        self.model_filter = "ollama"
        self.show_page("models")
        self.fill_models()
        self.query_one("#models-hint", Static).update("Ollama  ·  pick a model  ·  Enter locks and opens Route")

    def action_c_key(self) -> None:
        if self.start_page == "testing":
            self.action_compat_test()
            return
        if self.start_page not in ("models", "route"):
            return
        self.action_focus_llamacpp()

    def action_focus_llamacpp(self) -> None:
        self.lock_runtime = "llamacpp"
        self.model_filter = "llamacpp"
        self.show_page("models")
        self.fill_models()
        self.query_one("#models-hint", Static).update("llama.cpp  ·  pick a model  ·  Enter locks and opens Route")

    def action_lock_selected(self) -> None:
        if self.start_page != "models":
            self.show_page("models")
        table = self.query_one("#models-table", DataTable)
        if table.row_count == 0:
            return
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            return
        if not row_key or "\t" not in str(row_key):
            return
        runtime, model = str(row_key).split("\t", 1)
        write_lock(self.project, runtime, model)
        self.lock_runtime = runtime
        self.unlock_armed = False
        self.inspect_key = f"{runtime}\t{model}"
        self.refresh_snapshot()
        self._note(f"locked  {runtime} / {model}")
        self.query_one("#models-hint", Static).update(f"switched to {runtime} / {model}")
        self.fill_models()
        self.picker_active = False
        self.show_page("route")
        self.fill_chat()
        self.query_one("#you-log", Static).update("(switched model)")
        self.query_one("#model-result", Static).update("locked\n\nType a task on the left.")

    def action_run_test(self) -> None:
        self._start_test("suite", "running suite  small → medium → large → context → eval")

    def action_compat_test(self) -> None:
        self._start_test("compat", "running compatibility check")

    def action_sweep_models(self) -> None:
        self._start_test("sweep", "testing every local model  fastest ranking after")

    def _start_test(self, kind: str, note: str) -> None:
        if self.busy:
            return
        if kind != "sweep":
            _, model = self._active_runtime_model()
            if not model:
                self.log_error("test blocked", "no model locked — switch one on Models first")
                return
        self.pending_test = kind
        self.show_page("testing")
        self.test_note = note
        self.fill_testing()
        self.query_one("#test-result", Static).update(note)
        self.run_worker(self._test_worker, exclusive=True, thread=True, exit_on_error=False)

    def _route_one(self, adapter: Any, runtime_name: str, model: str, prompt: str, fallback: str | None) -> dict[str, Any]:
        kwargs = route_kwargs(self.cfg, self.project, fallback)
        kwargs["cheap_router"] = None
        return route_task(adapter=adapter, task=prompt, router_model=model, **kwargs)

    def _test_worker(self) -> None:
        self.busy = True
        kind = self.pending_test
        try:
            if kind == "compat":
                body = self._run_compat_locked()
            elif kind == "single":
                body = self._run_named_pack(
                    [{"prompt": COMPAT_PROMPT, "label": "local", "tier": "single"}],
                    "single",
                )
            elif kind == "simple":
                body = self._run_named_pack(SMALL_PACK, "simple")
            elif kind == "hard":
                body = self._run_named_pack(LARGE_PACK, "hard")
            elif kind == "sweep":
                body = self._run_sweep()
            else:
                body = self._run_suite_locked()
            self.call_from_thread(self._after_test, f"{kind} finished", body)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.log_error, f"{kind or 'test'} crashed", str(exc))
            self.call_from_thread(self._after_test, f"{kind} failed: {exc}", str(exc))
        self.busy = False

    def _run_compat_locked(self) -> str:
        runtime_name, model = self._active_runtime_model()
        ok, err, fallback = apply_lock(self.project, self.cfg, runtime_name, model or "", False)
        if not ok:
            raise RuntimeError(err or "lock blocked compatibility")
        adapter = build_adapter(self.cfg, runtime_override=runtime_name)
        try:
            start = time.perf_counter()
            decision = self._route_one(adapter, runtime_name, model, COMPAT_PROMPT, fallback)
            elapsed = (time.perf_counter() - start) * 1000
        finally:
            adapter.close()
        passed = bool(decision.get("ok") and decision.get("route") in ("local", "cloud"))
        status = "PASS" if passed else "FAIL"
        return (
            f"compatibility  {status}\n"
            f"model          {runtime_name} / {model}\n"
            f"route          {decision.get('route')}   {decision.get('policy_path')}\n"
            f"model said     {decision.get('model_said')}\n"
            f"latency        {decision.get('latency_ms') or elapsed:.0f} ms\n"
            f"error          {decision.get('error') or 'none'}"
        )

    def _run_named_pack(self, items: list[dict[str, Any]], title: str) -> str:
        runtime_name, model = self._active_runtime_model()
        ok, err, fallback = apply_lock(self.project, self.cfg, runtime_name, model or "", False)
        if not ok:
            raise RuntimeError(err or f"lock blocked {title}")
        adapter = build_adapter(self.cfg, runtime_override=runtime_name)
        lines = [f"{title}  {runtime_name} / {model}", ""]
        all_rows: list[dict[str, Any]] = []
        try:
            for item in items:
                decision = self._route_one(adapter, runtime_name, model, item["prompt"], fallback)
                row = {
                    **item,
                    "route": decision.get("route"),
                    "ok": decision.get("ok"),
                    "latency_ms": decision.get("latency_ms"),
                    "said": decision.get("model_said"),
                }
                all_rows.append(row)
                mark = "ok" if row.get("ok") and row.get("route") == row.get("label") else "miss"
                lines.append(
                    f"  {mark:4}  want {item.get('label', '?'):5}  got {row.get('route') or 'fail':5}  "
                    f"{row.get('latency_ms')}ms  {item['prompt'][:42]}"
                )
        finally:
            adapter.close()
        summary = score_rows(all_rows)
        lines[1:1] = [
            f"score   {summary['correct']}/{summary['total']}  ({summary['accuracy']:.0%})",
            f"speed   avg {summary['avg_ms']:.0f} ms",
        ]
        return "\n".join(lines)

    def _run_suite_locked(self) -> str:
        runtime_name, model = self._active_runtime_model()
        ok, err, fallback = apply_lock(self.project, self.cfg, runtime_name, model or "", False)
        if not ok:
            raise RuntimeError(err or "lock blocked suite")
        start_pc = self._pc_now()
        adapter = build_adapter(self.cfg, runtime_override=runtime_name)
        lines = [f"suite  {runtime_name} / {model}", ""]
        all_rows: list[dict[str, Any]] = []
        try:
            compat = self._route_one(adapter, runtime_name, model, COMPAT_PROMPT, fallback)
            if not (compat.get("ok") and compat.get("route") in ("local", "cloud")):
                raise RuntimeError(f"compatibility failed: {compat.get('error') or compat.get('model_said')}")
            lines.append(f"compat  PASS  {compat.get('latency_ms')} ms")
            for tier in SUITE_ORDER:
                lines.append(f"\n{tier}")
                for item in SUITE_PACKS[tier]:
                    decision = self._route_one(adapter, runtime_name, model, item["prompt"], fallback)
                    row = {
                        **item,
                        "route": decision.get("route"),
                        "ok": decision.get("ok"),
                        "latency_ms": decision.get("latency_ms"),
                        "said": decision.get("model_said"),
                    }
                    all_rows.append(row)
                    mark = "ok" if row.get("ok") and row.get("route") == row.get("label") else "miss"
                    lines.append(
                        f"  {mark:4}  want {item['label']:5}  got {row.get('route') or 'fail':5}  "
                        f"{row.get('latency_ms')}ms  {item['prompt'][:42]}"
                    )
        finally:
            adapter.close()
        summary = score_rows(all_rows)
        end_pc = self._pc_now()
        lines[1:1] = [
            f"score   {summary['correct']}/{summary['total']}  ({summary['accuracy']:.0%})",
            f"speed   avg {summary['avg_ms']:.0f} ms",
            f"pc      start CPU {start_pc['cpu']:.0f}% GPU {start_pc['gpu']:.0f}%  →  "
            f"end CPU {end_pc['cpu']:.0f}% GPU {end_pc['gpu']:.0f}%",
        ]
        return "\n".join(lines)

    def _candidate_models(self) -> list[tuple[str, str]]:
        snap = collect_snapshot(self.project, self.cfg)
        found: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for name in (snap.get("ollama") or {}).get("models") or []:
            key = ("ollama", str(name))
            if key not in seen:
                found.append(key)
                seen.add(key)
        for name in (snap.get("llamacpp") or {}).get("models") or []:
            key = ("llamacpp", str(name))
            if key not in seen:
                found.append(key)
                seen.add(key)
        return found

    def _run_sweep(self) -> str:
        candidates = self._candidate_models()
        if not candidates:
            raise RuntimeError("no local models found")
        started = time.time()
        board: list[dict[str, Any]] = []
        for runtime_name, model in candidates:
            if time.time() - started > SWEEP_TOTAL_S:
                board.append({"runtime": runtime_name, "model": model, "compat": "skip", "note": "15m cap"})
                continue
            row: dict[str, Any] = {"runtime": runtime_name, "model": model, "compat": "fail", "accuracy": None, "avg_ms": None}
            t0 = time.time()
            try:
                adapter = build_adapter(self.cfg, runtime_override=runtime_name)
                try:
                    compat = self._route_one(adapter, runtime_name, model, COMPAT_PROMPT, None)
                    if not (compat.get("ok") and compat.get("route") in ("local", "cloud")):
                        row["note"] = str(compat.get("error") or "compat failed")
                    elif time.time() - t0 > SWEEP_PER_MODEL_S:
                        row["compat"] = "timeout"
                    else:
                        rows = []
                        for item in SUITE_PACKS["eval"]:
                            if time.time() - t0 > SWEEP_PER_MODEL_S:
                                break
                            decision = self._route_one(adapter, runtime_name, model, item["prompt"], None)
                            rows.append({**item, "route": decision.get("route"), "ok": decision.get("ok"), "latency_ms": decision.get("latency_ms")})
                        summary = score_rows(rows)
                        row.update({"compat": "pass", "accuracy": summary["accuracy"], "avg_ms": summary["avg_ms"], "correct": summary["correct"], "total": summary["total"]})
                finally:
                    adapter.close()
            except Exception as exc:  # noqa: BLE001
                row["note"] = str(exc)
            board.append(row)
            self.leaderboard = rank_board(board)
            self.call_from_thread(self._after_test, f"sweep {len(board)}/{len(candidates)}", self._board_text(self.leaderboard))
        self.leaderboard = rank_board(board)
        try:
            path = self.project / "runs" / "tui-leaderboard.json"
            path.write_text(json.dumps(self.leaderboard, indent=2), encoding="utf-8")
        except Exception:
            pass
        return self._board_text(self.leaderboard)

    def _board_text(self, board: list[dict[str, Any]]) -> str:
        lines = ["leaderboard  fastest compatible first", ""]
        for row in board:
            acc = row.get("accuracy")
            acc_txt = f"{float(acc):.0%}" if isinstance(acc, (int, float)) else "—"
            avg = row.get("avg_ms")
            avg_txt = f"{float(avg):.0f}ms" if isinstance(avg, (int, float)) else "—"
            lines.append(
                f"{row.get('rank', '—'):>3}  {row.get('compat', '?'):8}  {acc_txt:>4}  {avg_txt:>8}  "
                f"{row.get('runtime')} / {short_model(str(row.get('model') or ''))}  {row.get('note') or ''}"
            )
        return "\n".join(lines)

    def _after_test(self, note: str, body: str) -> None:
        self.test_note = note
        self.fill_testing()
        self.query_one("#test-result", Static).update(body)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.ERROR:
            self.busy = False
            err = event.worker.error
            self.log_error("worker failed", str(err or "unknown worker error"))

    def action_unlock(self) -> None:
        self.show_page("models")
        if not self.unlock_armed:
            self.unlock_armed = True
            self.query_one("#models-hint", Static).update("press u again to confirm unlock")
            return
        clear_lock(self.project)
        self.unlock_armed = False
        self.refresh_snapshot()
        self.query_one("#models-hint", Static).update("lock cleared")
        self.fill_models()


def run_app(
    project: str | Path | None = None,
    page: str = "machine",
    route_text: str | None = None,
    auto_warmup: bool = False,
    auto_unlock: bool = False,
) -> int:
    app = LocalPilotApp(
        project=project,
        page=page,
        route_text=route_text,
        auto_warmup=auto_warmup,
        auto_unlock=auto_unlock,
    )
    result = app.run()
    return int(result or 0)
