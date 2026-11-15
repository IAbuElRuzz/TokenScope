import json
from decimal import Decimal
from pathlib import Path


class Pricing:
    """User-maintained effective-dated rates; missing prices remain unavailable."""

    def __init__(self, path: Path | None = None):
        self.catalog = json.loads(
            (path if path and path.exists() else Path(__file__).with_name("catalog.json")).read_text()
        )

    def quote(self, event):
        candidates = [
            row
            for row in self.catalog["models"]
            if row["provider"] == event.provider
            and row["model"] == event.model
            and row["effective_from"] <= event.timestamp.isoformat()
            and event.input_tokens >= row.get("minimum_input_tokens", 0)
            and (row.get("maximum_input_tokens") is None or event.input_tokens <= row["maximum_input_tokens"])
            and (not row.get("effective_until") or event.timestamp.isoformat() < row["effective_until"])
        ]
        if not candidates:
            return {
                "cost_usd": None,
                "pricing_version": self.catalog["version"],
                "pricing_source": None,
                "cost_confidence": "unavailable",
            }
        row = max(candidates, key=lambda r: r["effective_from"])
        uncached = event.input_tokens
        if event.input_includes_cache:
            uncached = max(0, uncached - event.cache_read_tokens - event.cache_creation_tokens)
        output = event.output_tokens + (0 if event.output_includes_reasoning else event.reasoning_tokens)
        counts = [uncached, output, event.cache_read_tokens, event.cache_creation_tokens]
        rates = [row.get(k) for k in ("input", "output", "cache_read", "cache_write")]
        if any(count and rate is None for count, rate in zip(counts, rates)):
            return {
                "cost_usd": None,
                "pricing_version": self.catalog["version"],
                "pricing_source": row.get("source"),
                "cost_confidence": "unavailable",
            }
        cost = sum(Decimal(count) * Decimal(str(rate or 0)) for count, rate in zip(counts, rates)) / Decimal(
            1_000_000
        )
        return {
            "cost_usd": float(cost),
            "pricing_version": self.catalog["version"],
            "pricing_source": row.get("source"),
            "pricing_record": row,
            "cost_confidence": "estimated",
        }

    def context_window(self, provider, model):
        rows = [r for r in self.catalog["models"] if r["provider"] == provider and r["model"] == model]
        return max(rows, key=lambda r: r["effective_from"]).get("context_window") if rows else None
