from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class AdapterCapabilities:
    tool: str
    source: str
    metrics: tuple[str, ...]
    supported_versions: str
    limitations: str
    authentication: str = "Local filesystem read permission only"
    recovery: str = "Retry next poll; replay uses stable event identities"


class TokenUsageAdapter(Protocol):
    def discover_sessions(self) -> list[dict]: ...
    def stream_events(self) -> AsyncIterator[dict]: ...
    def capabilities(self) -> AdapterCapabilities: ...
