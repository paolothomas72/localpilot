from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ClassificationResult:
    route: Optional[str]
    confidence: Optional[float]
    latency_ms: float
    ok: bool
    error: Optional[str] = None
    raw: Optional[str] = None


@dataclass
class GenerateResult:
    text: str
    latency_ms: float
    ok: bool
    error: Optional[str] = None


class RuntimeAdapter:
    name: str = "runtime"

    def classify(self, model: str, task: str) -> ClassificationResult:
        raise NotImplementedError

    def generate(self, model: str, prompt: str) -> GenerateResult:
        raise NotImplementedError

