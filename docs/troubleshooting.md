# Troubleshooting

No sessions: run `tokenscope doctor`, use the documented root overrides, confirm the tool records usage, and wait for request completion. Sources unavailable due to filesystem permissions are not proof of zero usage. Old or unknown log formats may be ignored; submit a metadata-only fixture with secrets and text removed.

Dashboard missing: `npm install && npm run build`, then restart Python. Source installs need a compiled dashboard; release wheels include it. Dashboard unauthorized: run `tokenscope token`, paste token, use localhost:8000. Never put tokens in URL query strings. Stale dashboard: reconnect; verify API health and original tab token.

Cost unavailable: exact model ID/effective date absent, unknown cache-write pricing or custom catalog missing a category. This is intentional. Do not assign zero price to unknown models. Context unavailable: no source occupancy or known capacity; catalog capacity alone does not imply current usage.

Docker file permission errors: use local installation or mount only explicitly readable log directories; do not chmod your credential files for TokenScope. Container socket/build failures are deployment environment issues, not evidence of application correctness.

Large historical archive: first replay reads all configured files; source JSON legacy files are bounded at 64 MiB; individual JSONL lines at 8 MiB. Use narrower roots and retention. Snapshot analytics materialize history; benchmark before long-term high-volume deployment.
