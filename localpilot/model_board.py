from __future__ import annotations

import json
from pathlib import Path
from typing import Any


WRAPPER_MARKS = (
    "mvp64k",
    "t3ctx",
    "syswrap",
    "codebridge",
    "-ft-",
    "finetuned",
    "-test",
    "q8-test",
)


def kind_of(name: str) -> str:
    n = name.lower()
    if "embed" in n:
        return "skip"
    if any(mark in n for mark in ("-vl:", "vl:", "qwen3vl")):
        return "skip"
    if any(mark in n for mark in ("-ft-", "finetuned")):
        return "finetune"
    if any(mark in n for mark in WRAPPER_MARKS):
        return "wrapper"
    return "native"


def display_model(name: str, width: int = 44) -> str:
    text = str(name)
    if len(text) <= width:
        return text
    tail = text.replace("\\", "/").split("/")[-1]
    if len(tail) >= width - 1:
        return "…" + tail[-(width - 1) :]
    keep = width - len(tail) - 1
    return text[:keep] + "…" + tail


def _tally(rows: list[dict[str, Any]], source: str) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for row in rows:
        model = str(row.get("model") or "")
        if not model:
            continue
        bucket = by.setdefault(
            model,
            {"model": model, "hits": 0, "total": 0, "kind": row.get("role") or kind_of(model), "source": source},
        )
        bucket["total"] += 1
        if row.get("match"):
            bucket["hits"] += 1
    return by


def load_gate_scores(project: Path) -> dict[str, dict[str, Any]]:
    """Prefer the 6-prompt native-pairs pack; fill gaps from the earlier sweep."""
    runs = project / "runs"
    scores: dict[str, dict[str, Any]] = {}
    scope = runs / "model-scope.json"
    if scope.exists():
        try:
            payload = json.loads(scope.read_text(encoding="utf-8"))
            sweep = payload.get("sweep") if isinstance(payload, dict) else None
            if isinstance(sweep, list):
                scores.update(_tally(sweep, "sweep"))
        except (OSError, json.JSONDecodeError):
            pass
    pairs = runs / "native-pairs.json"
    if pairs.exists():
        try:
            payload = json.loads(pairs.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                scores.update(_tally(payload, "native-pairs"))
        except (OSError, json.JSONDecodeError):
            pass
    return scores


def score_text(row: dict[str, Any] | None) -> str:
    if not row or not row.get("total"):
        return "—"
    return f"{int(row['hits'])}/{int(row['total'])}"


def best_native(scores: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    natives = [row for row in scores.values() if row.get("kind") == "native" and row.get("total")]
    if not natives:
        return None
    natives.sort(key=lambda row: (row["hits"] / row["total"], row["hits"], -len(row["model"])), reverse=True)
    return natives[0]


def model_pack_rows(project: Path, model: str) -> list[dict[str, Any]]:
    """Past gate-pack rows for one model. Empty for a new user with no runs."""
    if not model:
        return []
    rows: list[dict[str, Any]] = []
    for name in ("native-pairs.json",):
        path = project / "runs" / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, list):
            rows.extend(r for r in payload if str(r.get("model") or "") == model)
    sweep = project / "runs" / "model-scope.json"
    if sweep.exists() and not rows:
        try:
            payload = json.loads(sweep.read_text(encoding="utf-8"))
            pack = payload.get("sweep") if isinstance(payload, dict) else None
            if isinstance(pack, list):
                rows.extend(r for r in pack if str(r.get("model") or "") == model)
        except (OSError, json.JSONDecodeError):
            pass
    return rows


def load_route_history(project: Path, model: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    path = project / "runs" / "route-history.jsonl"
    if not path.exists():
        return []
    found: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-80:]:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if model and str(row.get("model") or "") not in {model, f"ollama / {model}", f"llamacpp / {model}"}:
                locked = str(row.get("model") or "")
                if not locked.endswith(model):
                    continue
            found.append(row)
    except OSError:
        return []
    return list(reversed(found[-limit:]))


def append_route_history(project: Path, decision: dict[str, Any]) -> None:
    path = project / "runs" / "route-history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": decision.get("at"),
        "model": decision.get("locked_model") or decision.get("selected_model"),
        "task": decision.get("task"),
        "route": decision.get("route"),
        "path": decision.get("policy_path"),
        "ms": decision.get("latency_ms"),
        "said": decision.get("model_said"),
    }
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def inspect_block(
    project: Path,
    runtime: str,
    model: str,
    scores: dict[str, dict[str, Any]],
    session_turns: list[dict[str, Any]] | None = None,
) -> str:
    if not model:
        return "SELECTED MODEL\nnone  ·  arrow a row to inspect"
    score = scores.get(model)
    kind = kind_of(model)
    lines = [
        "SELECTED MODEL",
        f"{runtime} / {model}",
        f"kind     {kind}",
        f"source   {runtime}",
        f"gate     {score_text(score)}" + (f"  ({score.get('source')})" if score else "  no pack yet"),
        "",
        "HISTORY / DATA",
    ]
    pack = model_pack_rows(project, model)
    if pack:
        hits = sum(1 for r in pack if r.get("match"))
        lines.append(f"gate pack  {hits}/{len(pack)}")
        for row in pack[:8]:
            mark = "ok" if row.get("match") else "miss"
            want = row.get("want") or row.get("label") or "?"
            got = row.get("got") or row.get("route") or "?"
            ms = row.get("ms")
            ms_txt = f"{float(ms):.0f}ms" if isinstance(ms, (int, float)) else ""
            lines.append(f"  {mark:4}  {row.get('kind') or '':<11} want {want:<5} got {got:<5} {ms_txt}")
    else:
        lines.append("no measured pack for this model yet")
        lines.append("a new user sees this empty until they run Testing or a native pack")

    live = [t for t in (session_turns or []) if str(t.get("locked_model") or "").endswith(model)]
    persisted = load_route_history(project, model)
    past = live or persisted
    lines.append("")
    lines.append("PAST RUNS")
    if not past:
        lines.append("no route turns on this model yet")
    else:
        for turn in past[:8]:
            task = str(turn.get("task") or "")[:42]
            lines.append(
                f"  {turn.get('route') or '?':<5}  {turn.get('path') or turn.get('policy_path') or '?'}  "
                f"{turn.get('ms') or turn.get('latency_ms') or '?'}  {task}"
            )
    return "\n".join(lines)


def lock_hint(scores: dict[str, dict[str, Any]], model: str | None) -> str | None:
    if not scores:
        return None
    best = best_native(scores)
    if not model:
        if best:
            return f"strongest measured native  {best['model']}  {score_text(best)}"
        return None
    mine = scores.get(model)
    if mine and mine["total"] and (mine["hits"] / mine["total"]) < 0.5 and best and best["model"] != model:
        return (
            f"{model} gate {score_text(mine)}. "
            f"stronger native lock  {best['model']}  {score_text(best)}"
        )
    if mine and mine["total"]:
        return f"gate pack  {score_text(mine)}  from {mine.get('source')}"
    if best:
        return f"no pack yet for this lock. strongest native  {best['model']}  {score_text(best)}"
    return None
