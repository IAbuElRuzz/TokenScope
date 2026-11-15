# Validation and release limitations

Actual checks performed in the development sandbox (2026-11-15):

- Backend pytest: accounting subsets, revision/replay/restart, context independent from totals, pricing arithmetic/history, concurrent sessions, API authentication, Host/Origin rejection, WebSocket reconnect, deletion/tombstones, malformed inputs, local transport credential protection, deterministic alerts, retention, eight local-adapter fixtures, two-source automatic collector integration and local WebSocket delivery assertion below 500 ms.
- Frontend: strict TypeScript and Vite production build; six Vitest tests (data accounting/filtering and context gauge server-rendered component tests).
- Ruff configured lint rules passed.
- Built `tokenscope-0.1.0-py3-none-any.whl` using cached setuptools; inspected included dashboard HTML/JS/CSS, pricing catalog, API modules and CLI entry point. Wheel build succeeded; clean online dependency installation was not validated.
- `docker compose config --quiet` passed. Container build/run could not be tested: Docker socket permission denied in the sandbox. A running daemon does not change that restriction.
- Synthetic ingestion benchmark: 2,000 events, approximately 1,919 events/second, 257 ms full snapshot, 8.5 MB traced peak Python memory. These are one-machine test numbers, not a production SLA. No browser-render or provider-completion latency claim follows from them.

The sandbox could not install Python packages over network (DNS blocked). Tests used an existing Python 3.14 environment with FastAPI/pytest and cached httpx copied read-only into a temporary import path. Normal documented installation remains CI's responsibility. Frontend cached dependencies installed and compiled successfully. Dependency audit could not access registry; cached Vite/Vitest development versions require security review/upgrade before a production release. GitHub CI has not been observed running yet.

Reproduction in this sandbox:

```sh
PYTHONPATH=/private/tmp/tokenscope-testdeps:. /Users/i/Desktop/Eth_Bots/Hyperliquid/Bot/bot-be/venv/bin/python -m pytest -q
ASDF_PYTHON_VERSION=3.14.1 ruff check backend cli tests benchmarks
npm test
npm run build
```

## Remaining release work

This release does **not** meet the entire production definition in the original project brief. Do not label these features complete:

- Live vendor validation with simultaneous actual Claude Code/Codex/Gemini binaries; numerical supported release ranges and platform-specific log fixtures.
- Browser-driven end-to-end tests and actual screenshot visual verification; current frontend tests cover components and data, not browser interaction.
- Full date-range event aggregation, SQL pagination/large-history optimization, dedicated multi-session comparison workflow.
- Automatic Cursor support, OTLP receiver, optional proxy, PostgreSQL backend, dynamically downloaded catalog, comprehensive current/historical model pricing and context metadata.
- Automated entry-point plugin loading; adapter injection and explicit SDK metadata instrumentation work.
- Complete prompt/tool counters and generation timing from native logs, confirmed session termination, exact context occupancy; these fields may be unavailable.
- Repeated-prompt analysis is unavailable by design without opt-in content-derived fingerprints. Optional AI explanations and outbound notification transports are not implemented.
- Complete billing tier/cache TTL pricing, invoice verification and subscription allowance mapping.
- Dependency security audit, signed packages, observed CI/release workflow execution, full cross-platform installer/runtime verification and container runtime verification.

Internal source-log corrections arriving after truncation may have lower replay revision order and remain conservatively ignored; ingest authoritative higher-revision canonical corrections for such cases. Codex counter decreases are ignored to avoid double-counting stale snapshots; true undocumented counter resets can therefore undercount until a verified reset format is supported.
