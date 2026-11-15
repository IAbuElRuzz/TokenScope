# Pricing and context metadata

The bundled catalog is a deliberately limited verified snapshot, not a complete live price service. Sources: [OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing), [Claude model capacities](https://platform.claude.com/docs/en/models/overview), inspected 2026-11-15. OpenAI default rates are omitted because context-tier selection requires further validation; configure explicit contract tiers locally. No regional/fast/long-context assumptions are applied. Anthropic cache writes remain unpriced because TTL is not normalized. Unknown/historical models stay unavailable.

Replace `~/.tokenscope/pricing.json`, then restart. JSON shape:

```json
{"version":"enterprise-2026-11","models":[{"provider":"custom","model":"exact-model-id","effective_from":"2026-11-01T00:00:00+00:00","effective_until":null,"input":1.0,"output":4.0,"cache_read":0.1,"cache_write":1.25,"context_window":128000,"source":"internal-contract","minimum_input_tokens":0,"maximum_input_tokens":200000}]}
```

Rates are USD per million tokens. Effective dates use timezone-aware ISO UTC strings. Multiple rows support effective dates and input-size tiers. Specify every billable category used: a missing positive-category rate makes the quote unavailable. Cache reads/writes are subtracted from inclusive input before applying uncached rates; reasoning is added to output only where it is exclusive. Catalog context capacity is not evidence of actual occupancy or compaction threshold.

Persisted events store the quote, version, source and selected pricing row; restarts do not reprice history. Request revisions recompute using their original selected pricing row. Subscription usage always displays API-equivalent estimates, not direct billing. Billed USD is a separate optional field. Currency conversion, taxes, discounts, cache TTL tier selection, automated catalog updates and complete model coverage remain deployment responsibilities.
