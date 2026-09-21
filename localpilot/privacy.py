from __future__ import annotations

import re
from typing import Any

DEFAULT_CONTENT_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bsk-[A-Za-z0-9_-]{8,}\b",
    r"\bsk-or-[A-Za-z0-9_-]{8,}\b",
    r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
]

_HIGH_ENTROPY_TOKEN = re.compile(r"\b[A-Za-z0-9/_+=.-]{32,}\b")


def _is_high_entropy_token(text: str) -> bool:
    if len(text) < 32:
        return False
    classes = 0
    if re.search(r"[a-z]", text):
        classes += 1
    if re.search(r"[A-Z]", text):
        classes += 1
    if re.search(r"[0-9]", text):
        classes += 1
    if re.search(r"[/_+=.-]", text):
        classes += 1
    return classes >= 3 and len(set(text)) >= 16


def find_content_matches(task: str, patterns: list[str] | None = None) -> list[str]:
    found: list[str] = []
    for pat in patterns or DEFAULT_CONTENT_PATTERNS:
        try:
            if re.search(pat, task, flags=re.IGNORECASE):
                found.append(pat)
        except re.error:
            continue
    if any(_is_high_entropy_token(m.group(0)) for m in _HIGH_ENTROPY_TOKEN.finditer(task)):
        found.append("high_entropy_token")
    return found


def privacy_audit_fields(
    topic_matches: list[str],
    content_matches: list[str],
    topic_threshold: int,
    content_threshold: int,
) -> dict[str, Any]:
    topic_score = len(topic_matches)
    content_score = len(content_matches)
    return {
        "privacy_topic_score": topic_score,
        "privacy_content_score": content_score,
        "privacy_topic_keywords": topic_matches,
        "privacy_content_patterns": content_matches,
        "privacy_topic_triggered": topic_score >= max(1, int(topic_threshold)),
        "privacy_content_triggered": content_score >= max(1, int(content_threshold)),
    }
