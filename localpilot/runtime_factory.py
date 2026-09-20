from __future__ import annotations

from typing import Any

from .runtime_base import RuntimeAdapter
from .runtime_llamacpp import LlamaCppAdapter
from .runtime_ollama import OllamaAdapter


def build_adapter(config: dict[str, Any], runtime_override: str | None = None) -> RuntimeAdapter:
    runtime = (runtime_override or config.get("runtime") or "ollama").lower()
    if runtime == "ollama":
        ollama_cfg = config.get("ollama", {})
        return OllamaAdapter(host=ollama_cfg.get("host", "127.0.0.1"), port=int(ollama_cfg.get("port", 11434)))
    if runtime in ("llamacpp", "llama.cpp", "llama_cpp"):
        lc = config.get("llamacpp", {})
        return LlamaCppAdapter(
            base_url=lc.get("base_url", "http://127.0.0.1:8090"),
            endpoint=lc.get("endpoint", "/v1/chat/completions"),
            api_key=lc.get("api_key", ""),
            timeout_s=int(lc.get("timeout_s", 120)),
            temperature=float(lc.get("temperature", 0.0)),
            max_tokens=int(lc.get("max_tokens", 64)),
            top_p=float(lc.get("top_p", 1.0)),
            top_k=int(lc.get("top_k", 1)),
            min_p=float(lc.get("min_p", 0.0)),
            repeat_penalty=float(lc.get("repeat_penalty", 1.0)),
            seed=int(lc.get("seed", 7)),
        )
    raise ValueError(f"Unsupported runtime: {runtime}")


def model_for_runtime(config: dict[str, Any], runtime_name: str, explicit_model: str | None, kind: str) -> str | None:
    if explicit_model:
        return explicit_model
    models = config.get("models", {})
    runtime_key = "llamacpp" if runtime_name in ("llamacpp", "llama.cpp", "llama_cpp") else "ollama"
    key = f"{runtime_key}_{kind}"
    if key in models:
        return models.get(key)
    return models.get(kind)

