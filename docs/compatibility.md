# Capability research and compatibility

Official documentation and source inspected 2026-11-15 before implementation:

- [Claude Code monitoring](https://code.claude.com/docs/en/monitoring-usage): OTel request logs expose separate uncached input, output, cache read, cache creation, duration and cost. Export batching means provider visibility is not instantaneous. [Claude Code hooks](https://code.claude.com/docs/en/hooks) document transcript paths. Local assistant usage records are an internal source, not a stable API.
- [Codex configuration](https://developers.openai.com/codex/config-advanced): OTel completed-response logs and per-turn token histograms. [Official protocol source](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs) describes TokenUsageInfo and rollout counters. Source counters include cached input within input, reasoning within output.
- [Gemini CLI telemetry](https://geminicli.com/docs/cli/telemetry/): API response token fields include input, output, cached, thoughts and provider total. [Recording service source](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts) documents source-observed local session formats.

| Integration | Implemented source | Accuracy | Context | Setup / limits |
|---|---|---|---|---|
| Claude Code | projects JSONL assistant usage | Exact reported request snapshots | Latest request input estimate; capacity catalog if known | Automatic local discovery; no provider auth; cache categories exclusive |
| Codex CLI | sessions rollout JSONL token_count | Exact reported cumulative deltas | Latest request input estimate; reported maximum | Automatic; repeated snapshots ignored; decreased counters conservatively ignored |
| Gemini CLI | legacy session JSON / message JSONL | Exact recorded usage | Latest request input estimate; unknown capacity unless catalog | Automatic where files exist; unknown patches ignored |
| OpenAI API / compatible | Explicit SDK usage metadata helper | Exact reported counts | Unavailable by default | Instrument existing client; no traffic interception |
| Anthropic API | Explicit metadata normalizer | Exact reported categories | Unavailable by default | Instrument client; cache input exclusive |
| Gemini API | Explicit metadata normalizer | Exact provider total | Unavailable by default | Thoughts distinct from candidate output |
| OpenRouter | OpenAI-compatible explicit usage | Reported usage | Unavailable | Instrument client; no account scraping |
| Ollama | Explicit final usage metadata | Reported eval counts | Unavailable | Local client instrumentation |
| Cursor | None | Unavailable | Unavailable | No supported automatic integration claimed |
| OTLP receiver | Not implemented | Unavailable | Unavailable | Docs explain upstream capabilities, not shipped receiver support |

Numeric CLI release ranges have **not** been certified against installed binaries. Supported formats are pinned by synthetic fixtures and official source field definitions; integrations are experimental and fail closed on unknown schemas. A real simultaneous Claude + Codex run was not performed during development. Two-source synthetic fixture concurrency is tested; this is not a substitute for vendor live validation.

Measurements arrive after usage records are written, typically request completion. Local polling interval is 250 ms. This targets below 500 ms after ingestion, not before provider completion; performance is workload-dependent.

Session active/idle is inferred from latest measured activity (120-second threshold), not an OS-process liveness claim. Completion is explicit only when a canonical completed event arrives. Tool calls, prompt counts, exact occupancy, billed dollars and generation throughput may remain unavailable; zero event tool_calls is not proof of no tools. Prompt text is never persisted, so repeated-prompt detection is intentionally unavailable.
