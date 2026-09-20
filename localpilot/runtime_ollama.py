from __future__ import annotations

import http.client
import json
import time
from typing import Any

from .runtime_base import ClassificationResult, GenerateResult, RuntimeAdapter

ROUTE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["local", "cloud"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["route", "confidence"],
    "additionalProperties": False,
}


def _build_prompt(task: str) -> str:
    return (
        "Classify the developer task for model routing.\n"
        "Return STRICT JSON only with keys: route, confidence.\n"
        'route must be exactly "local" or "cloud".\n'
        "confidence must be a float from 0 to 1.\n"
        "Use this policy:\n"
        "- local: mechanical, deterministic, narrow, low risk.\n"
        "- cloud: architecture/security/high-stakes diagnosis/multi-step reasoning.\n"
        f"TASK: {task}"
    )


class OllamaAdapter(RuntimeAdapter):
    name = "ollama"

    def __init__(self, host: str = "127.0.0.1", port: int = 11434, timeout_s: int = 120):
        self._host = host
        self._port = port
        self._timeout_s = timeout_s

    def close(self) -> None:
        return None

    def classify(self, model: str, task: str) -> ClassificationResult:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": _build_prompt(task),
            "stream": False,
            "format": ROUTE_JSON_SCHEMA,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": 48},
        }
        body = json.dumps(payload)
        t0 = time.perf_counter()
        conn = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout_s)
        try:
            conn.request("POST", "/api/generate", body=body, headers={"Content-Type": "application/json"})
            res = conn.getresponse()
            raw = res.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return ClassificationResult(
                route=None,
                confidence=None,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                ok=False,
                error=f"request_failed: {exc}",
            )
        finally:
            conn.close()

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        if res.status != 200:
            return ClassificationResult(
                route=None,
                confidence=None,
                latency_ms=elapsed,
                ok=False,
                error=f"http_{res.status}",
                raw=raw[:500],
            )

        try:
            outer = json.loads(raw)
            response_text = outer.get("response", "")
            if not response_text:
                return ClassificationResult(
                    route=None,
                    confidence=None,
                    latency_ms=elapsed,
                    ok=False,
                    error="empty_response",
                    raw=raw[:500],
                )
            inner = json.loads(response_text)
        except Exception as exc:  # noqa: BLE001
            return ClassificationResult(
                route=None,
                confidence=None,
                latency_ms=elapsed,
                ok=False,
                error=f"parse_failed: {exc}",
                raw=raw[:500],
            )

        route = inner.get("route")
        confidence = inner.get("confidence")
        if route not in ("local", "cloud"):
            return ClassificationResult(
                route=None,
                confidence=None,
                latency_ms=elapsed,
                ok=False,
                error="invalid_route",
                raw=str(inner)[:500],
            )
        if not isinstance(confidence, (int, float)):
            return ClassificationResult(
                route=None,
                confidence=None,
                latency_ms=elapsed,
                ok=False,
                error="invalid_confidence_type",
                raw=str(inner)[:500],
            )
        if confidence < 0.0 or confidence > 1.0:
            return ClassificationResult(
                route=None,
                confidence=None,
                latency_ms=elapsed,
                ok=False,
                error="invalid_confidence_range",
                raw=str(inner)[:500],
            )
        return ClassificationResult(route=route, confidence=confidence, latency_ms=elapsed, ok=True, raw=str(inner))

    def generate(self, model: str, prompt: str) -> GenerateResult:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.3, "num_predict": 256},
        }
        t0 = time.perf_counter()
        conn = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout_s)
        try:
            conn.request("POST", "/api/generate", body=json.dumps(payload), headers={"Content-Type": "application/json"})
            res = conn.getresponse()
            raw = res.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return GenerateResult(text="", latency_ms=round((time.perf_counter() - t0) * 1000, 2), ok=False, error=f"request_failed: {exc}")
        finally:
            conn.close()
        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        if res.status != 200:
            return GenerateResult(text="", latency_ms=elapsed, ok=False, error=f"http_{res.status}: {raw[:240]}")
        try:
            outer = json.loads(raw)
            text = str(outer.get("response") or "").strip()
        except Exception as exc:  # noqa: BLE001
            return GenerateResult(text="", latency_ms=elapsed, ok=False, error=f"parse_failed: {exc}")
        if not text:
            return GenerateResult(text="", latency_ms=elapsed, ok=False, error="empty_response")
        return GenerateResult(text=text, latency_ms=elapsed, ok=True)

