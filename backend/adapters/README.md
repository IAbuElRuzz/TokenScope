# Local source contracts

Research checked 2026-11-15 against primary sources:

- [Claude Code monitoring](https://code.claude.com/docs/en/monitoring-usage) documents exact API request input/output/cache categories. Input excludes cache reads and writes. [Hooks](https://code.claude.com/docs/en/hooks) document local transcript paths. The assistant JSONL format is an internal, capability-detected format, not a stable public API.
- [Codex protocol](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs) defines `TokenUsageInfo`, cumulative `total_token_usage`, `last_token_usage`, and `model_context_window`. Cached input and reasoning output are subsets. Token-count snapshots can repeat; only counter changes create events. Synthetic full-context markers are ignored. Decreased or stale counters are ignored without changing the baseline; arbitrary vendor counter corrections cannot be reconstructed exactly from this format.
- [Gemini chat recording service](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts) and [types](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingTypes.ts) define per-message `TokensSummary`. Legacy JSON and append-only JSONL are supported. Content patches and rewinds do not erase historical token expenditure. Token-bearing replacement messages update the original event.
- [Gemini telemetry](https://geminicli.com/docs/cli/telemetry/) is an alternative documented source but this package does not implement an OTLP receiver.

No numeric tool version range is claimed: compatibility means the tested record shapes above. Unknown fields are ignored and unknown shapes produce no measurements. `doctor` reports roots and malformed records. Future tool versions can change formats; fixture validation is required before claiming support.

Only model, token counts, timestamps, session/request identifiers and project basename/hash leave the parser. Credentials are never read. Prompt/response text is parsed transiently with JSON but never retained or logged. Symlinked session files and files resolving outside configured roots are skipped. JSONL records above 8 MiB and JSON files above 64 MiB are skipped defensively. Offsets are memory-only; restart replay relies on stable event identities and revisions in persistent storage.

Visibility is post-response, not live generation token counts. Poll interval is 200 ms after local availability. Context is explicitly estimated from the latest prompt size, never lifetime consumption. Provider model metadata supplies a window only where available; Claude/Gemini local records do not establish one. Session termination cannot be inferred from a completed response; inactivity is handled by the backend.

The API normalizer accepts reported usage metadata from OpenAI-compatible, Anthropic, Gemini, and Ollama responses. It does not discover arbitrary network traffic or fetch account billing. OpenRouter usage can use the OpenAI-compatible contract. Cursor has no native local adapter in this release.
