from __future__ import annotations

import http.client
import json
import time
from urllib.parse import urlparse

from .runtime_base import ClassificationResult, GenerateResult, RuntimeAdapter

ROUTE_JSON_SCHEMA = {
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


class LlamaCppAdapter(RuntimeAdapter):
    name = "llamacpp"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8090",
        endpoint: str = "/v1/chat/completions",
        api_key: str | None = None,
        timeout_s: int = 120,
        temperature: float = 0.0,
        max_tokens: int = 64,
        top_p: float = 1.0,
        top_k: int = 1,
        min_p: float = 0.0,
        repeat_penalty: float = 1.0,
        seed: int = 7,
    ):
        parsed = urlparse(base_url)
        self._scheme = parsed.scheme or "http"
        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or (443 if self._scheme == "https" else 80)
        self._base_path = parsed.path.rstrip("/")
        self._endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        self._api_key = api_key or ""
        self._timeout_s = timeout_s
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._top_p = top_p
        self._top_k = top_k
        self._min_p = min_p
        self._repeat_penalty = repeat_penalty
        self._seed = seed

    def close(self) -> None:
        return None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def classify(self, model: str, task: str) -> ClassificationResult:
        path = f"{self._base_path}{self._endpoint}" if self._base_path else self._endpoint
        prompt = _build_prompt(task)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a strict JSON classifier."},
                {"role": "user", "content": prompt},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "top_p": self._top_p,
            "top_k": self._top_k,
            "min_p": self._min_p,
            "repeat_penalty": self._repeat_penalty,
            "seed": self._seed,
            "stream": False,
            "response_format": {"type": "json_object", "schema": ROUTE_JSON_SCHEMA},
        }
        t0 = time.perf_counter()
        conn_cls = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(self._host, self._port, timeout=self._timeout_s)
        try:
            conn.request("POST", path, body=json.dumps(payload), headers=self._headers())
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
            content = outer["choices"][0]["message"]["content"]
            if not content:
                return ClassificationResult(
                    route=None,
                    confidence=None,
                    latency_ms=elapsed,
                    ok=False,
                    error="empty_response",
                    raw=raw[:500],
                )
            inner = json.loads(content)
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
        path = f"{self._base_path}{self._endpoint}" if self._base_path else self._endpoint
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 256,
            "stream": False,
        }
        t0 = time.perf_counter()
        conn_cls = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(self._host, self._port, timeout=self._timeout_s)
        try:
            conn.request("POST", path, body=json.dumps(payload), headers=self._headers())
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
            text = str(outer["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:  # noqa: BLE001
            return GenerateResult(text="", latency_ms=elapsed, ok=False, error=f"parse_failed: {exc}")
        if not text:
            return GenerateResult(text="", latency_ms=elapsed, ok=False, error="empty_response")
        return GenerateResult(text=text, latency_ms=elapsed, ok=True)

