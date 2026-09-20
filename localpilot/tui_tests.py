from __future__ import annotations

from typing import Any


SMALL_PACK = [
    {"prompt": "Rename one variable in a single file.", "label": "local", "tier": "small"},
    {"prompt": "Add a docstring to a helper function.", "label": "local", "tier": "small"},
    {"prompt": "Format imports in one Python file.", "label": "local", "tier": "small"},
]
MEDIUM_PACK = [
    {"prompt": "Explain what this 12-line function does, line by line.", "label": "local", "tier": "medium"},
    {"prompt": "Convert this JSON response into a TypeScript interface.", "label": "local", "tier": "medium"},
    {"prompt": "Diagnose intermittent production deadlock.", "label": "cloud", "tier": "medium"},
    {"prompt": "Review auth token logic for security holes.", "label": "cloud", "tier": "medium"},
    {"prompt": "Add type hints to one function without behavior changes.", "label": "local", "tier": "medium"},
]
LARGE_PACK = [
    {"prompt": "Design caching architecture for a read-heavy multi-region service.", "label": "cloud", "tier": "large"},
    {"prompt": "Choose between a modular monolith and split services for audit needs.", "label": "cloud", "tier": "large"},
    {"prompt": "Write a rollback plan for a failed production migration.", "label": "cloud", "tier": "large"},
    {"prompt": "Refactor a 300-line class into smaller units, same behavior.", "label": "cloud", "tier": "large"},
    {"prompt": "Summarize the likely root cause from a long stack trace.", "label": "cloud", "tier": "large"},
    {"prompt": "Reformat this snippet for readability without behavior change.", "label": "local", "tier": "large"},
]
_CONTEXT_PAD = ("def helper(x):\n    return x\n" * 60)
CONTEXT_PACK = [
    {
        "prompt": _CONTEXT_PAD + "\nClassify this task: add a docstring to helper.",
        "label": "local",
        "tier": "context",
    }
]
EVAL_PACK = SMALL_PACK + [
    {"prompt": "Diagnose intermittent production deadlock.", "label": "cloud", "tier": "eval"},
    {"prompt": "Review auth token logic for security holes.", "label": "cloud", "tier": "eval"},
]
COMPAT_PROMPT = "Rename one variable in a single file."

VARIATION_PACK = [
    {"prompt": "Put the helpers in ABC order at the top of the file.", "label": "local", "kind": "easy-new"},
    {"prompt": "The note above foo() has a misspelled word, fix only that.", "label": "local", "kind": "easy-new"},
    {"prompt": "Add one blank line between two functions and nothing else.", "label": "local", "kind": "easy-new"},
    {"prompt": "Delete an unused import in utils.py.", "label": "local", "kind": "easy-new"},
    {"prompt": "Rename tmp to count in one helper.", "label": "local", "kind": "easy-new"},
    {"prompt": "Walk me through turning a blank repo into a company over six months.", "label": "cloud", "kind": "hard-new"},
    {"prompt": "I need a full marketing site with checkout and accounts from scratch.", "label": "cloud", "kind": "hard-new"},
    {"prompt": "Compare three database engines and pick one for a global store.", "label": "cloud", "kind": "hard-new"},
    {"prompt": "Threat-model the OAuth callback before we ship.", "label": "cloud", "kind": "hard-known"},
    {"prompt": "Write a zero-downtime rollback plan for the payments DB.", "label": "cloud", "kind": "hard-known"},
    {"prompt": "Help me pick a career path for the next five years with backups if the first one fails.", "label": "cloud", "kind": "hard-novel"},
    {"prompt": "I want to invent a store that sells experiences, not goods. Where do I start?", "label": "cloud", "kind": "hard-novel"},
    {"prompt": "Plan how a two-person team ships a newspaper to a million readers.", "label": "cloud", "kind": "hard-novel"},
    {"prompt": "What would it take to run a kitchen that serves three cities at once?", "label": "cloud", "kind": "hard-novel"},
    {"prompt": "I need a way for strangers to trade favors at city scale. Sketch the whole thing.", "label": "cloud", "kind": "hard-novel"},
    {"prompt": "How should a band go from one room to a worldwide tour without going broke?", "label": "cloud", "kind": "hard-novel"},
    {"prompt": "Give me a year-one plan to replace a town's paper forms with something people will actually use.", "label": "cloud", "kind": "hard-novel"},
    {"prompt": "A two-person shop wants to become the default way a whole region books anything. Map the first year.", "label": "cloud", "kind": "hard-novel"},
]

SUITE_ORDER = ("small", "medium", "large", "context", "eval")
SUITE_PACKS = {
    "small": SMALL_PACK,
    "medium": MEDIUM_PACK,
    "large": LARGE_PACK,
    "context": CONTEXT_PACK,
    "eval": EVAL_PACK,
}

SWEEP_PER_MODEL_S = 90
SWEEP_TOTAL_S = 900


def score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labeled = [r for r in rows if r.get("label")]
    correct = sum(1 for r in labeled if r.get("ok") and r.get("route") == r.get("label"))
    total = len(labeled) or 1
    lats = [float(r.get("latency_ms") or 0) for r in rows if r.get("ok")]
    avg = sum(lats) / len(lats) if lats else 0.0
    return {
        "correct": correct,
        "total": len(labeled),
        "accuracy": correct / total if labeled else 0.0,
        "avg_ms": avg,
        "ok_count": sum(1 for r in rows if r.get("ok")),
    }


def rank_board(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compatible = [r for r in rows if r.get("compat") == "pass"]
    failed = [r for r in rows if r.get("compat") != "pass"]
    compatible.sort(key=lambda r: (float(r.get("avg_ms") or 1e9), -float(r.get("accuracy") or 0)))
    failed.sort(key=lambda r: str(r.get("model") or ""))
    ranked = []
    for i, row in enumerate(compatible, start=1):
        ranked.append({**row, "rank": i})
    for row in failed:
        ranked.append({**row, "rank": "—"})
    return ranked
