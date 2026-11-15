from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class UsageEvent(BaseModel):
    """A request snapshot; higher revisions replace earlier snapshots."""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=256)
    revision: int = Field(default=0, ge=0)
    provider: str = Field(min_length=1, max_length=100)
    tool: str = Field(min_length=1, max_length=100)
    model: str = Field(default="unknown", max_length=200)
    session_id: str = Field(min_length=1, max_length=256)
    request_id: str | None = Field(default=None, max_length=256)
    timestamp: datetime
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_creation_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    input_includes_cache: bool = True
    output_includes_reasoning: bool = True
    accounting: Literal["delta", "cumulative"] = "delta"
    context_tokens: int | None = Field(default=None, ge=0)
    context_window: int | None = Field(default=None, gt=0)
    context_accuracy: Literal["exact", "estimated", "unavailable"] = "unavailable"
    accuracy: Literal["exact", "estimated", "unavailable"] = "exact"
    source: str = Field(default="configured-api", max_length=256)
    kind: Literal["usage", "compaction", "reset", "completed", "activity"] = "usage"
    project: str | None = Field(default=None, max_length=256)
    latency_ms: float | None = Field(default=None, ge=0)
    generation_ms: float | None = Field(default=None, gt=0)
    tool_calls: int = Field(default=0, ge=0)
    billed_cost_usd: float | None = Field(default=None, ge=0)
    billing: Literal["unknown", "subscription", "api", "local"] = "unknown"

    @model_validator(mode="after")
    def normalize(self):
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        self.timestamp = self.timestamp.astimezone(timezone.utc)
        if self.total_tokens is None:
            self.total_tokens = self.input_tokens + self.output_tokens
            if not self.input_includes_cache:
                self.total_tokens += self.cache_read_tokens + self.cache_creation_tokens
            if not self.output_includes_reasoning:
                self.total_tokens += self.reasoning_tokens
        if self.context_tokens is None:
            self.context_accuracy = "unavailable"
        return self
