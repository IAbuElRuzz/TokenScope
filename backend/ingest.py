"""Metadata-only helper for explicitly instrumented API clients; no network interception."""

import hashlib
import json
from datetime import datetime, timezone
from urllib.request import Request
from backend.adapters import normalize_usage
from backend.core.schema import UsageEvent
from backend.core.transport import local_url, local_open


def usage_event(provider, model, session_id, request_id, usage, **metadata):
    normalized = normalize_usage(provider, usage)
    return UsageEvent(
        event_id=hashlib.sha256(f"{provider}:{session_id}:{request_id}".encode()).hexdigest(),
        provider=provider,
        tool="instrumented-api",
        model=model,
        session_id=session_id,
        request_id=request_id,
        timestamp=datetime.now(timezone.utc),
        source="explicit-sdk-metadata",
        billing="api",
        **normalized,
        **metadata,
    )


def publish(event: UsageEvent, token: str, url="http://127.0.0.1:8000"):
    url = local_url(url)
    payload = json.dumps(event.model_dump(mode="json")).encode()
    request = Request(
        url + "/api/events",
        data=payload,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with local_open(request, timeout=10) as response:
        return json.load(response)
