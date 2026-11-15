# Central multi-user server

TokenScope can run as a **self-hosted single-instance server for a trusted team**. Every account has private usage, sessions, analytics, exports and deletion scope. Administrators have an explicit aggregate account/device usage overview; they do not receive another user's request timeline through that endpoint. Standalone local monitoring remains the default.

This is a first server milestone, not a public multi-tenant SaaS claim. Use one application process with SQLite and per-account ledger partitions on durable local disk. PostgreSQL, horizontal scaling, SSO, self-registration, password recovery emails and audited enterprise access control are not implemented. Admins provision accounts locally. Operators with filesystem/backups access can read every partition; application isolation does not protect against the server owner.

A **TokenScope account** identifies the owner of enrolled collectors. It is not a verified Anthropic/OpenAI/Google billing identity. Two provider logins on one OS account are not automatically distinguished. TokenScope never scrapes provider login credentials, API keys or private account endpoints.

## Server setup

Install/build as documented in README. Use a private data directory and a dedicated OS identity. Before exposing the application, configure a TLS reverse proxy for one explicit origin:

```sh
export TOKENSCOPE_DATA_DIR=/your/private/tokenscope-server
export TOKENSCOPE_PUBLIC_ORIGIN=https://tokens.example.com
# Passwords are prompted twice by getpass, never supplied in argv.
tokenscope users create admin --admin
tokenscope users create alice
tokenscope users create bob
tokenscope server --host 127.0.0.1 --port 8000
```

Proxy that origin to localhost:8000, preserving Host and WebSocket upgrades. Configure proxy connection/body/rate limits, TLS certificates and trusted forwarded headers yourself. The application rejects other Host/Origin values, caps actual request bodies at 2 MiB including chunked transfers, and never follows outbound collector redirects. Configure access logs to exclude sensitive request bodies/headers. Do not expose a plaintext remote listener or run multiple workers against the in-process WebSocket registry.

For `backend.api.server:app` / container deployments set `TOKENSCOPE_MODE=server` as well. `tokenscope server` selects server mode directly. **Server mode never instantiates the local CLI scanner**, even if the host has Claude/Codex/Gemini logs. No logs are mounted by the central compose file:

```sh
export TOKENSCOPE_PUBLIC_ORIGIN=https://tokens.example.com
docker compose -f docker-compose.server.yml up --build -d
docker compose -f docker-compose.server.yml exec tokenscope tokenscope users create admin --admin
```

For local development only, explicitly set `TOKENSCOPE_PUBLIC_ORIGIN=http://127.0.0.1:8000` and `TOKENSCOPE_ALLOW_INSECURE_HTTP=1`. Insecure HTTP is rejected for non-loopback hosts. Never use this exception remotely.

Account operations on the server:

```sh
tokenscope users list
tokenscope users disable alice
tokenscope users enable alice
tokenscope users password alice
```

Disabling or changing a password invalidates browser sessions and revokes enrolled devices. Enabling a disabled account does not restore those credentials; enroll new ones. Username is 3–64 letters/numbers/dot/dash/underscore, normalized lowercase. Passwords require 12–1024 characters, salted PBKDF2-HMAC-SHA256 with 600,000 iterations. Login uses bounded in-memory IP/account throttling; use reverse-proxy rate limiting too because restart clears the throttle. Password verification runs in FastAPI's worker threadpool.

## Browser login and device enrollment

At the configured HTTPS origin log in using the provisioned username/password. The browser stores an opaque 12-hour session in an HttpOnly Secure SameSite=Strict cookie. Only its SHA-256 hash persists in the control database. A separate CSRF token is derived from the opaque session secret; mutations require matching Origin and `X-CSRF-Token`. CSRF token hashes, not raw session credentials, persist. Logout invalidates the server session and wakes its streams; disable/expiry are checked on the next stream update or five-second heartbeat.

In **Server account**, register a device (e.g. “Alice laptop”). A `tsd_...` credential is shown **once**. Copy it into a private file on that computer; do not paste it into source code, shell arguments, command history or chat. The server stores only a token hash. Device credentials can ingest usage only; they cannot sign into dashboards, read/export usage or administer accounts. Browser cookies cannot authenticate collector ingestion.

Device registration/revocation and administrator overview require account authentication; mutation routes additionally require CSRF. The UI polls device activity and the role-gated admin aggregate every five seconds. Device timestamps are server-generated. Revocation blocks the next ingest attempt but does not erase previously accepted history.

## Outbound collectors on developer computers

Install TokenScope on each computer and preserve local source permissions. Save the enrolled token in a user-only file (POSIX `chmod 600`; Windows restrict inherited ACLs yourself). The collector reads only its usual configured source roots, extracts existing usage metadata and **explicitly sends it to the configured server**. It does not send prompt/response text or provider keys. Project basenames, model/session identifiers, costs and optional consented fingerprints are still personal metadata: review this scope before enrollment.

```sh
tokenscope collect --server https://tokens.example.com --device-token-file /private/path/device-token
# Scheduled execution: one scan followed by draining all pending batches.
tokenscope collect --server https://tokens.example.com --device-token-file /private/path/device-token --once
```

Only HTTPS origins are accepted remotely; credentials embedded in URLs, URL paths/query strings and redirects are rejected. TLS certificate verification uses the normal Python trust configuration. An explicit `--allow-insecure-loopback` permits HTTP only for local smoke tests. Device credentials are sent only to `/api/ingest`, never through the standalone localhost bearer-token helper.

The SQLite spool lives under the computer's `TOKENSCOPE_DATA_DIR/collectors/<opaque-enrollment-hash>/spool.db`. Pending metadata survives crashes/network failures. Batches contain at most 100 events; exact acknowledged event/revision pairs become delivery receipts with the payload removed. Receipts prevent re-transmission after source-log replay on restart; the server independently deduplicates and honors deletion tombstones. Unknown or invalid records are skipped with counts, never logged bodies. Network errors retry with bounded exponential backoff; 401/403 stops the collector so revoked credentials are not retried forever. `--once` exits nonzero on delivery failure and leaves metadata queued.

When 100,000 pending records are queued, new scans pause until delivery catches up. One initial log replay can exceed that soft threshold; constrain source roots/monitor disk usage for very large histories. Delivery receipts persist, retaining opaque event IDs/revisions; stopping and deleting a collector spool clears receipts, after which the server still protects against replay. Do not delete the spool during an outage unless you intend to discard queued metadata. Re-enrolling into a *different* device creates a new identity scope and may reimport the same logs as new device history; reuse one enrollment per source root and revoke old sources deliberately.

## Isolation and authoritative identity

Accounts and device IDs are randomly generated internal identifiers. Each account has its own SQLite usage ledger and tombstones under `accounts/<internal-user-id>/usage.db`; auth/devices/session hashes live in `accounts.db`. Device credentials map to an account in the server database. Payload user/device fields cannot choose another account. The server namespaces event identity and session identity by the authenticated device, preserving `source_session_id` separately for investigation. Two users or devices with identical local IDs cannot overwrite or delete each other's records.

HTTP queries, exports, analytics and WebSocket subscriptions resolve identity from the browser session; there is no caller-supplied tenant selector. Notifications only wake that account's streams. Administrators see aggregate users/devices/request/token/cost/coverage totals through `/api/admin/overview`; raw timelines remain private to each account in the application. Admin local CLI and operator filesystem access remain privileged.

A device can submit fabricated usage to its own account: this is observability metadata, not a billing attestation service. Device-scoped identity prevents collision; it does not verify provider invoices or source integrity.

## Retention, deployment and validation limits

Default 90-day retention sweeps every minute across all persisted accounts, including inactive/unopened ledgers. Source replay cannot restore deleted events because per-account tombstones persist. Back up the entire private data directory consistently (control DB, ledger DBs, WAL files, tombstones); restore auth and usage together. Password reset/revocation affects future ingestion only. Full account deletion/retention of audit logs and cryptographic backup management require an operator policy; there is no self-service account deletion endpoint yet.

The control plane and usage stores are SQLite, not PostgreSQL. Single-process pub/sub, synchronous ledger operations and full-history snapshot analytics are appropriate for modest teams only; no 500 ms distributed-load SLA is claimed. TLS/proxy setup, Windows ACLs, actual remote-network delivery, live vendor sessions and browser-driven E2E remain deployment validation responsibilities. TestClient integration tests exercise account/device collision isolation, cookie/CSRF/roles, revocation, replay/restart/retention, per-account WebSockets and collector retry/spooling with synthetic metadata.
