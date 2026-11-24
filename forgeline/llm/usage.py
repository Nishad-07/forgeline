"""Equivalent-cost calculation from pricing.yaml (USD per 1M tokens)."""

from __future__ import annotations

from pathlib import Path

import yaml


class PriceTable:
    def __init__(self, models: dict[str, dict[str, float]], providers: dict[str, dict[str, float]]) -> None:
        self.models = models
        self.providers = providers

    @classmethod
    def load(cls, path: Path) -> PriceTable:
        if not path.exists():
            return cls({}, {})
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(data.get("models", {}) or {}, data.get("providers", {}) or {})

    def rate(self, provider: str, model: str) -> dict[str, float]:
        return self.models.get(model) or self.providers.get(provider) or {"input": 0.0, "output": 0.0}

    def cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        rate = self.rate(provider, model)
        return (input_tokens * rate.get("input", 0.0) + output_tokens * rate.get("output", 0.0)) / 1_000_000
