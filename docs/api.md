# Local API

Interactive schema: `http://127.0.0.1:8000/docs`. All `/api` HTTP routes require `Authorization: Bearer LOCAL_TOKEN`; retrieve with `tokenscope token`. Default listener is loopback. Arbitrary browser origins and Host headers are rejected.

- `GET /health`: public liveness/version.
- `GET /api/snapshot`: retained-history session aggregates, UTC-today totals, latest 1,000 events, deterministic alerts.
- `GET /api/analytics`: complete retained-history accounting, per-provider/model/session breakdowns, largest requests, evidence-backed opportunities and coverage. Independent of dashboard session filters. Included as `analytics` in snapshots too.
- `GET /api/events?session_id=...`: full retained event metadata, optionally scoped.
- `POST /api/events`: one canonical request snapshot. Returns `accepted: false` on replay/stale revision/deleted event.
- `GET /api/export`: full retained canonical ledger, including immutable persisted price quotes.
- `GET /api/providers`: source paths, availability and limitations. Does not return credentials.
- `DELETE /api/sessions/{session_id}`: deletes metadata and records replay tombstones.
- `WS /api/ws`: first client message `{"token":"..."}` within five seconds; subsequent server messages are snapshots. Reconnect and reauthenticate to recover missed updates.

See `backend/core/schema.py` and OpenAPI for the canonical contract. Provider, tool, model, session_id, event_id, timezone-aware timestamp required; revision defaults zero. Count categories must be nonnegative. `accounting="delta"` required at ingestion. Optional `billed_cost_usd` is a trusted instrumented source claim; TokenScope does not verify invoices. Do not include request/response bodies or API keys. Unknown properties are discarded.

Optional prompt-derived fields require explicit `prompt_analysis_opt_in=true`: scoped HMAC `prompt_fingerprint` and `prompt_characters`. Unknown raw prompt/body properties are discarded. See [analytics privacy](analytics.md) before enabling.


## Server-mode account API

Explicit server mode replaces standalone bearer authentication with private account cookie sessions. `GET /api/config` identifies the mode. `POST /api/auth/login` accepts username/password from the configured browser Origin and sets an HttpOnly Secure SameSite cookie; `GET /api/auth/me` returns account identity and CSRF token. Logout and all browser mutations require matching Origin and `X-CSRF-Token`. All usage read/export/analytics/WebSocket routes are scoped to that authenticated account.

`GET/POST /api/devices` lists/registers own devices; registration returns a one-time ingest credential. `DELETE /api/devices/{id}` revokes an own device. `POST /api/ingest` accepts `{events:[UsageEvent,...]}` up to 100 items and requires a separate registered device bearer credential. It returns exact native event ID/revision acknowledgements. The server derives account and device identity from that credential and namespaces event/session IDs. A browser cookie cannot ingest; a device token cannot read dashboards.

`GET /api/admin/overview` requires an admin account and returns aggregate account/device/request/token/known-cost totals only. No raw cross-account timeline endpoint is exposed. See [server.md](server.md) for trust boundaries, deployment, retention and enrollment.


## Full history / investigation

`GET /api/history` accepts timezone-aware `start` inclusive / `end` exclusive, exact `provider`, `model`, `tool`, `session_id`, `project`, `device_id`, `request_id`, and `page`/`page_size` (1–200). It returns page items, full matching count/totals/analytics and same-account filter options. `GET /api/history/requests/{event_id}` returns scoped canonical metadata, accounting, persisted pricing provenance and capped related duplicate evidence; absent/other-account events return 404. `GET /api/export` accepts the same filters and exports every match, independent of pagination. All are authenticated according to mode. See [history.md](history.md) for UTC day conversion, consistency, privacy and scale limits.
