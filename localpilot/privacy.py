from __future__ import annotations

import re
from typing import Any

DEFAULT_CONTENT_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bsk-[A-Za-z0-9]{20,}\b",
    r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b",
]


def find_content_matches(task: str, patterns: list[str] | None = None) -> list[str]:
    found: list[str] = []
    for pat in patterns or DEFAULT_CONTENT_PATTERNS:
        try:
            if re.search(pat, task, flags=re.IGNORECASE):
                found.append(pat)
        except re.error:
            continue
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
