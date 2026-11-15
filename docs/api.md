# Local API

Interactive schema: `http://127.0.0.1:8000/docs`. All `/api` HTTP routes require `Authorization: Bearer LOCAL_TOKEN`; retrieve with `tokenscope token`. Default listener is loopback. Arbitrary browser origins and Host headers are rejected.

- `GET /health`: public liveness/version.
- `GET /api/snapshot`: retained-history session aggregates, UTC-today totals, latest 1,000 events, deterministic alerts.
- `GET /api/events?session_id=...`: full retained event metadata, optionally scoped.
- `POST /api/events`: one canonical request snapshot. Returns `accepted: false` on replay/stale revision/deleted event.
- `GET /api/export`: full retained canonical ledger, including immutable persisted price quotes.
- `GET /api/providers`: source paths, availability and limitations. Does not return credentials.
- `DELETE /api/sessions/{session_id}`: deletes metadata and records replay tombstones.
- `WS /api/ws`: first client message `{"token":"..."}` within five seconds; subsequent server messages are snapshots. Reconnect and reauthenticate to recover missed updates.

See `backend/core/schema.py` and OpenAPI for the canonical contract. Provider, tool, model, session_id, event_id, timezone-aware timestamp required; revision defaults zero. Count categories must be nonnegative. `accounting="delta"` required at ingestion. Optional `billed_cost_usd` is a trusted instrumented source claim; TokenScope does not verify invoices. Do not include request/response bodies or API keys. Unknown properties are discarded.
