"""Metadata-only integrations; credentials and content are never retained."""

from .local import LocalAdapters
from .usage import normalize_usage
from .protocol import AdapterCapabilities, TokenUsageAdapter

__all__ = ["LocalAdapters", "normalize_usage", "AdapterCapabilities", "TokenUsageAdapter"]
