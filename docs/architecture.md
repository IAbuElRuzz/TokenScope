# Architecture

Python FastAPI serves an authenticated local API, WebSocket and built React dashboard. SQLite WAL is the durable event ledger. React/Vite provides a smaller static deployment than a server-rendered stack; SVG charts avoid extra telemetry or remote resources. PostgreSQL is not implemented.

Pipeline: local log adapters → validated canonical UsageEvent → stable identity/revision deduplication → SQLite transaction → deterministic analytics → bounded WebSocket wakeup queue → fresh dashboard snapshot.

Input/cache and output/reasoning containment flags preserve provider semantics. Provider totals are authoritative when present. Only normalized request deltas enter storage; raw cumulative counters are rejected. Codex source adapter derives deltas against its replayed baseline. Lower stale counters are ignored conservatively. Claude partial snapshots share event identity and increasing replay-order revision. Corrections must use a higher revision to replace earlier request counts, including reductions. Out-of-order independent events are sorted by source timestamp when aggregating; same-timestamp ties sort by ID.

The collector isolates malformed files and retries incomplete JSONL tails. Metadata-only events survive restart; raw log text and credentials do not. Scanner offsets are in memory and restart replays source files, with database deduplication. Deletion tombstones prevent replay resurrection. Symlink files outside configured roots are skipped.

Context occupancy is independently carried per event. Latest-request estimates may omit unseen system tokens or output; a model switch or compaction invalidates old context. Capacity is source-reported or a catalog entry, not a universal constant. No compaction countdown is invented.

Standalone WebSocket clients authenticate in their first frame (not URL); server-mode streams authenticate the account cookie and configured Origin. Each client has a one-marker queue, so a slow client does not accumulate unbounded events. Reconnect sends a fresh snapshot and recovers changes made while disconnected. Snapshot currently materializes retained history for analytics, which limits large-history scalability; charts receive at most 1,000 events. Optimize SQL aggregation/pagination before enterprise scale.


## Explicit central server

`backend.server.app` is a separate mode: no native scanner on the server. Browser account sessions and registered device ingestion use separate hashed opaque secrets in an SQLite control database. Each account owns a physically separate canonical Store ledger, preserving standalone accounting/replay logic while avoiding legacy identifier/tombstone collisions. The server namespaces event/session identities by the authenticated device and ignores payload tenant claims. WebSocket wakeup queues are keyed by authenticated account; admin overview is a deliberate aggregate-only endpoint. Outbound collectors use SQLite pending batches/delivery receipts and HTTPS without redirects. This first mode supports a single process/modest trusted team; PostgreSQL, distributed pub/sub, SSO and horizontal scaling remain future work.


History querying uses a resumable integer UTC-microsecond index, bound exact metadata filters, stable timestamp/event-ID pagination and scoped request detail. List/count/totals are read consistently per response; full matching analytics/export still materialize history. Context recommendations retain same-account unfiltered reset/model boundary checks without adding excluded consumption. See [history.md](history.md).
