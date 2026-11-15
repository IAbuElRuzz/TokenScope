"""Validated full-history query semantics shared by local and tenant-scoped stores."""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FIELDS = ("provider", "model", "tool", "session_id", "project", "device_id", "request_id")


def epoch_us(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("History dates must include timezone")
    delta = value.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


@dataclass(frozen=True)
class HistoryFilters:
    start: datetime | None = None
    end: datetime | None = None
    provider: str | None = None
    model: str | None = None
    tool: str | None = None
    session_id: str | None = None
    project: str | None = None
    device_id: str | None = None
    request_id: str | None = None

    def __post_init__(self):
        for value in (self.start, self.end):
            if value is not None:
                epoch_us(value)
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("History start must precede exclusive end")
        for field in FIELDS:
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or len(value) > 256):
                raise ValueError("History filter must be at most 256 characters")

    def sql(self):
        clauses = []
        args = []
        if self.start is not None:
            clauses.append("occurred_us>=?")
            args.append(epoch_us(self.start))
        if self.end is not None:
            clauses.append("occurred_us<?")
            args.append(epoch_us(self.end))
        for field in FIELDS:
            value = getattr(self, field)
            if value:
                # Field is a constant allowlist; values always remain bound SQL parameters.
                expression = "session_id" if field == "session_id" else f"json_extract(payload,'$.{field}')"
                clauses.append(expression + "=?")
                args.append(value)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), args

    def public(self):
        result = {key: value for key, value in asdict(self).items() if value is not None and value != ""}
        for key in ("start", "end"):
            if key in result:
                result[key] = result[key].astimezone(timezone.utc).isoformat()
        return result
