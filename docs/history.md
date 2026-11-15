# Full history and usage-event investigation

The dashboard's **Historical usage investigation** searches the complete retained ledger, independently of overview filters and its latest 1,000-event chart sample. In server mode it operates only within the authenticated account; admins do not gain access to another account's event history. In standalone mode the existing local bearer token is required.

Filters combine exact provider, model, tool, session ID, project, enrolled device ID and provider request ID with a UTC start/end period. Session IDs in server mode are trusted device-scoped identifiers; each event also preserves its native `source_session_id`. Filter choices come from the same account's full ledger, including values outside the current filtered result.

## Time, pagination and totals

`GET /api/history` accepts:

- `start` and `end`: timezone-aware ISO timestamps; start **inclusive**, end **exclusive**. Offsets normalize to UTC. Naive/invalid timestamps and reversed/equal ranges return 422.
- `provider`, `model`, `tool`, `session_id`, `project`, `device_id`, `request_id`: exact string matches (up to 256 characters). Empty fields mean no restriction. SQL values are bound parameters.
- `page`: 1-based; `page_size`: 1–200 (default 50). Results are newest first, using integer UTC microseconds then event ID descending as a deterministic tie-break.

The UI's start/through **days are UTC days**. Selecting a final day sends the next day's midnight as exclusive end, so the entire selected final day is included. API clients may use finer timestamps, including microseconds.

A response includes `items`, `page`, `page_size`, `total_items`, `total_pages`, `filters`, `options`, `totals` and `analytics`. Items include usage **and lifecycle events** such as compaction/reset; only usage records contribute to request totals/analytics. Totals, pricing coverage and recommendations use **every matching event across all pages**, not only the displayed page. Empty pages beyond the final page return an empty list with the real count/totals preserved. Known costs exclude unpriced requests; unknown prices never become zero-cost usage.

Each response holds a consistent local ledger read for list/count/aggregates. Pagination is a fresh query, not a pinned historical snapshot: new arrivals or corrections between page requests may shift page membership. Refresh to see latest data. This release uses page/offset navigation, not cursor-based long-running exports.

Existing ledgers automatically add and backfill a chronological UTC-microsecond index. Startup resumes rows left unfilled by an interrupted migration. Canonical events are preserved; only the query index changes. Retention deletion uses the same exact numeric time semantics.

## Event investigation

Click **Investigate** or call `GET /api/history/requests/{event_id}`. A missing or other-account event returns 404. The response includes:

- Canonical metadata: source, accuracy, device/native session identity, model, context, latency, token categories and consented prompt length where available. No raw prompt or response content.
- Accounting decomposition: normalized total/uncached input, cache categories, normalized output and reasoning subset, validity flag and provider-total residual. Cache/reasoning subsets must not be added to inclusive totals again.
- Persisted pricing evidence: estimated API-equivalent cost separately from supplied billed cost, confidence, version, source and selected historical pricing record. Investigation does not silently reprice history.
- Related telemetry identity collisions: same provider/session/request identity recorded under distinct event IDs; this may indicate overlapping telemetry or ID reuse, not repeated billed work.
- Related repeated-prompt **candidates**: consented matching HMAC fingerprint/scope, same provider/model/project, distinct request identity and within ±15 minutes of the selected event. Identity-collision records are excluded. This centered investigation window differs from global analytics' fixed anchored clusters. Each related category is capped at 20 returned records with a separate complete count.

Matching supplied prompt text does not establish identical complete model context or parameters. Retries, evaluations and intentional repetition can all be valid. Related records are evidence for review, not confirmed waste or guaranteed savings. Uninstrumented requests remain unavailable for prompt repetition analysis. See [analytics privacy](analytics.md).

## Filtered export and privacy

`GET /api/export` accepts the same time/metadata filters and returns **all matches**, independent of history page size. The dashboard's **Export all filtered events** downloads that result, with filter metadata, after account authentication. Exported fingerprints/project/session identifiers are sensitive metadata even without prompt text.

Account changes, logout, authorization expiry and filter/page changes clear stale detail state and cancel pending requests. Generation guards prevent a late parsed response from a previous account/filter selection overwriting the new view. Server-mode credentials remain cookie/CSRF based; standalone credentials remain local bearer tokens.

## Limits and verification

The dashboard now uses cursor pagination, SQL aggregate totals and streamed exports; see [large archive behavior and limits](history-scaling.md). Detailed optimization analysis is an explicit action that still materializes matching history in memory. Context-growth checks consult the same account's unfiltered boundary timeline so filtering out a compaction/model-switch record cannot fabricate growth. Legacy offset endpoints remain available for compatibility; no distributed-load SLA is claimed.

Synthetic tests cover more than 1,000 events, all exact filters, microsecond and timezone-offset boundaries, deterministic ties, empty pages, old/partially migrated ledgers, retention boundary precision, scoped totals/price provenance, duplicate evidence, all-matching exports, invalid query rejection and server account isolation. Frontend tests exercise UTC-day conversion, encoded filters, cancellation/generation guards and rendered evidence. Live vendor, remote proxy and browser-driven E2E remain separate deployment checks.
