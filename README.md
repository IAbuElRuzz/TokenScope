# TokenScope

Local-first token observability for developers running multiple coding agents. Automatically discovers Claude Code, Codex and Gemini CLI session logs, persists usage metadata, and streams a dashboard over authenticated WebSockets. No cloud account, provider credentials, proxy, or prompt storage required.

**Release status: 0.1.0 / experimental local-log integrations.** This is a functioning application with fixture-tested adapters, not a claim that every requested production feature is finished. Internal CLI log formats are not stable vendor APIs. See [compatibility](docs/compatibility.md) and [validation](docs/validation.md) before relying on totals for billing.

## Quick start

Requires Python 3.11+, Node 20.19+ or 22+, npm. On macOS/Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm install
npm run build
tokenscope start
```

On Windows PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
npm install
npm run build
tokenscope start
```

Open **http://127.0.0.1:8000**. In another activated terminal run `tokenscope token` and paste the generated local access token into the dashboard. The token is stored in your browser tab's session storage. Never publish it. Start Claude Code and Codex normally: supported usage appears when their session files report completed requests. Monitoring an existing history may take longer on first launch. Empty states mean no measured events; production never seeds example usage.

```sh
tokenscope doctor
tokenscope status
tokenscope sessions
tokenscope live
tokenscope costs --today
tokenscope export --format json > usage.json
tokenscope export --format csv > usage.csv
tokenscope delete 'codex:SESSION_ID'
```

Data lives under `~/.tokenscope` (override `TOKENSCOPE_DATA_DIR`). Logs are read-only. Retention defaults to 90 days, swept every minute; deleted event identifiers remain as tombstones to prevent source-log replay restoring deleted metadata. Never delete only the database if you expect deletion to survive replay. Full removal: stop TokenScope and remove its data directory; starting again reimports retained source logs.

## What is measured

- Input/output, cache reads/writes, reasoning, reported totals, model, session, project basename, request timeline.
- Multiple sessions, safe log replay, revision replacement, duplicate suppression, idle status, compaction where recorded.
- Current context is **estimated from the latest request input**, never lifetime token sums. Unknown capacity stays unavailable. Codex provides a source-reported maximum; other maxima may come from editable model metadata.
- Costs are **estimated API-equivalent**, not subscription bills. Actual billed USD is only accepted from explicitly instrumented authoritative sources. Missing prices remain unavailable, including unsupported cache TTLs.
- Deterministic alerts cover context thresholds, model changes, token spikes, long session histories, no reported cache reads, session and rolling daily/weekly/30-day budgets. Desktop browser notifications require your click and permission.

Dashboard charts use up to the latest 1,000 events; session totals use retained history. Date filtering selects sessions by last activity, not billing-period event aggregation. Export provides the full retained event ledger. There is no live generation rate unless a source supplies generation timing.

## Configuration

| Variable | Default / meaning |
|---|---|
| `TOKENSCOPE_DATA_DIR` | `~/.tokenscope` |
| `TOKENSCOPE_TOKEN` | Optional explicit local API token; otherwise generated with restricted permissions |
| `TOKENSCOPE_CLAUDE_ROOT` | `~/.claude/projects` |
| `TOKENSCOPE_CODEX_ROOT` | `$CODEX_HOME/sessions` or `~/.codex/sessions` |
| `TOKENSCOPE_GEMINI_ROOT` | `~/.gemini/tmp` |
| `TOKENSCOPE_RETENTION_DAYS` | `90` |
| `TOKENSCOPE_SESSION_BUDGET` | `10` USD estimated API-equivalent |
| `TOKENSCOPE_DAILY_BUDGET` | `0` disables; rolling 24 hours |
| `TOKENSCOPE_WEEKLY_BUDGET` | `0` disables; rolling 7 days |
| `TOKENSCOPE_MONTHLY_BUDGET` | `0` disables; rolling 30 days |

Optional `~/.tokenscope/pricing.json` replaces the bundled effective-dated catalog. See [pricing](docs/pricing.md). Catalog changes apply to new ingestions; existing estimates remain persisted. No automatic external metadata fetching occurs.

## Docker

```sh
docker compose up --build -d
docker compose exec tokenscope tokenscope token
```

The compose file binds loopback only, drops capabilities, uses an unprivileged user, and mounts only session directories read-only. Create source folders first, or remove unavailable mounts. Linux host file ownership must allow UID 10001 read access; prefer local installation if logs are private to your UID. Never mount credential directories or entire home directories.

## Development and verification

```sh
python -m pip install -e '.[dev]'
pytest -q
ruff check backend cli tests benchmarks
npm install
npm test
npm run build
python -m benchmarks.ingestion
```

`npm run dev` runs the dashboard at localhost:5173 with an API/WebSocket proxy to port 8000; run `tokenscope start` separately. Production builds copy the dashboard into `backend/static`, included in wheels built after the frontend build. The release workflow builds both.

Reproduce a screenshot: launch with your own supported sessions, sign into the local dashboard, select desired filters, and capture your browser. Review session/project metadata before sharing. No misleading screenshots with synthetic production data are included.

## Documentation

[Architecture](docs/architecture.md) · [Compatibility and research](docs/compatibility.md) · [API](docs/api.md) · [Plugins](docs/plugins.md) · [Troubleshooting](docs/troubleshooting.md) · [Validation and remaining work](docs/validation.md) · [Security](SECURITY.md)

## Publishing

Target repository: https://github.com/IAbuElRuzz/TokenScope

If publishing manually from this workspace after reviewing files:

```sh
git init
git add .
git commit -m "Build TokenScope local-first observatory"
git branch -M main
git remote add origin https://github.com/IAbuElRuzz/TokenScope.git
gh auth login
git push -u origin main
```

MIT licensed. Contributions to documented formats, adapters, accounting and reliability are welcome.
