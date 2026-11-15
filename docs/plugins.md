# Adapter development

Implement `backend.adapters.protocol.TokenUsageAdapter`: discover_sessions, async stream_events, capabilities. The built-in multi-source scanner also exposes poll() and doctor(). Instantiate `create_app(scanner=your_adapter)` to replace the default collector; external entry-point plugin auto-loading is not yet shipped.

Produce metadata dictionaries validated by UsageEvent. Use stable request identity across replays and partial updates; increment revision only for a replacement of that request. Never emit both an absolute cumulative record and its request deltas. Normalize cumulative counters inside the adapter and preserve input/cache and output/reasoning containment. Derive provider total only when the source definition permits. Context must be a separate latest-occupancy observation or labeled estimate.

Use `backend.ingest.usage_event` with final SDK usage objects for opt-in API integration; `publish` only sends to explicit localhost ports and rejects redirects. Source OpenAI-compatible usage can be normalized using provider `openai` when the provider contract actually uses those semantics. No proxy is required or provided.

Add redacted/synthetic fixture tests covering duplicate replay, corrections, malformed and partial records, provider-specific totals, absent telemetry, privacy and recovery. Document official source links, supported numeric versions actually tested, latency limits and auth requirements. Notification extensions implement `NotificationChannel.send`; Slack/Discord/webhooks are interfaces only and require explicit transport configuration.
