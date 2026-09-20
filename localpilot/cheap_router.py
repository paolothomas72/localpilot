from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass


TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{1,}")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


HORIZON_PHRASES = (
    "blank repo",
    "from scratch",
    "six months",
    "6 months",
    "into a company",
    "a company",
    "marketing site",
    "global store",
    "checkout and accounts",
    "startup",
    "marketplace",
    "five years",
    "million readers",
    "city scale",
    "three cities",
    "worldwide tour",
    "year-one",
    "whole region",
    "sells experiences",
    "experiences, not",
    "not goods",
)


@dataclass
class CheapRouteDecision:
    route: str
    confidence: float
    latency_ms: float
    overlap: float = 1.0
    horizon: list[str] | None = None
    abstain: bool = False
    abstain_reason: str | None = None


class CheapRouter:
    """
    Tiny multinomial Naive Bayes router trained from labeled prompts.
    No external dependencies; intended as millisecond pre-gate.
    """

    def __init__(self, local_counts: dict[str, int], cloud_counts: dict[str, int], n_local: int, n_cloud: int):
        self.local_counts = local_counts
        self.cloud_counts = cloud_counts
        self.n_local = n_local
        self.n_cloud = n_cloud
        self.vocab = set(local_counts.keys()) | set(cloud_counts.keys())
        self.local_total = sum(local_counts.values())
        self.cloud_total = sum(cloud_counts.values())

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "CheapRouter":
        local_counts: dict[str, int] = {}
        cloud_counts: dict[str, int] = {}
        n_local = 0
        n_cloud = 0
        for r in rows:
            label = str(r.get("label", "")).strip().lower()
            prompt = str(r.get("prompt", ""))
            toks = _tokenize(prompt)
            if label == "local":
                n_local += 1
                for t in toks:
                    local_counts[t] = local_counts.get(t, 0) + 1
            elif label == "cloud":
                n_cloud += 1
                for t in toks:
                    cloud_counts[t] = cloud_counts.get(t, 0) + 1
        return cls(local_counts=local_counts, cloud_counts=cloud_counts, n_local=n_local, n_cloud=n_cloud)

    def predict(self, prompt: str) -> CheapRouteDecision | None:
        if self.n_local == 0 or self.n_cloud == 0:
            return None
        t0 = time.perf_counter()
        toks = _tokenize(prompt)
        if not toks:
            return None
        # Laplace smoothing.
        alpha = 1.0
        vocab_size = max(1, len(self.vocab))
        prior_local = self.n_local / (self.n_local + self.n_cloud)
        prior_cloud = self.n_cloud / (self.n_local + self.n_cloud)
        ll_local = math.log(prior_local + 1e-12)
        ll_cloud = math.log(prior_cloud + 1e-12)
        denom_local = self.local_total + alpha * vocab_size
        denom_cloud = self.cloud_total + alpha * vocab_size
        for tok in toks:
            c_l = self.local_counts.get(tok, 0)
            c_c = self.cloud_counts.get(tok, 0)
            ll_local += math.log((c_l + alpha) / denom_local)
            ll_cloud += math.log((c_c + alpha) / denom_cloud)
        # Softmax over two logits.
        m = max(ll_local, ll_cloud)
        p_local = math.exp(ll_local - m)
        p_cloud = math.exp(ll_cloud - m)
        z = p_local + p_cloud
        p_local /= z
        p_cloud /= z
        if p_local >= p_cloud:
            route = "local"
            conf = p_local
        else:
            route = "cloud"
            conf = p_cloud
        uniq = list(dict.fromkeys(toks))
        seen = [t for t in uniq if (self.local_counts.get(t, 0) + self.cloud_counts.get(t, 0)) >= 2]
        overlap = (len(seen) / len(uniq)) if uniq else 0.0
        lowered = (prompt or "").lower()
        horizon = [p for p in HORIZON_PHRASES if p in lowered]
        abstain = False
        reason = None
        if route == "local" and horizon:
            abstain = True
            reason = f"horizon cues {horizon} on a LOCAL call"
        return CheapRouteDecision(
            route=route,
            confidence=float(conf),
            latency_ms=round((time.perf_counter() - t0) * 1000, 4),
            overlap=round(overlap, 4),
            horizon=horizon,
            abstain=abstain,
            abstain_reason=reason,
        )

