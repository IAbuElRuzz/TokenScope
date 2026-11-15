# Detailed usage and optimization analysis

The dashboard's **Detailed token analysis** and authenticated `GET /api/analytics` use the full retained canonical ledger, not the recent 1,000-event chart sample. The same report is included under `snapshot.analytics` in HTTP and WebSocket snapshots. Its period and global scope are explicit: dashboard session/date filters do not filter this report.

## Accounting and evidence

Breakdowns compare providers, provider-qualified models, and sessions. They show requests, normalized input/output, provider-accounted totals, estimated API-equivalent cost and priced-request coverage. Cache reads/writes form part of normalized input; reasoning forms part of normalized output. Do not add these subsets to totals again. `provider_total_residual_tokens` preserves any difference between the provider total and normalized input/output (for example, additional provider categories). Inconsistent subsets are flagged via `invalid_decomposition_requests`; ratios become unavailable rather than implying valid proportions.

The largest 20 requests expose measured input/output, exact event IDs, timestamps and any explicitly provided prompt length. Unknown costs remain null; a real zero rate remains zero. Partial known costs exclude unpriced requests and are separate from supplied billed cost. These are workload comparisons, not equivalent-quality model benchmarks or automatic savings estimates.

## Deterministic review opportunities

Each opportunity has evidence, a confidence level, related event IDs, a recommendation and limitations. There are no extra LLM calls, AI-generated causes or automatic prompt changes.

| Opportunity | Required evidence | What it does not prove |
|---|---|---|
| Telemetry identity collision | More than one ledger event shares provider/session/request identity | Multiple provider calls or actual waste. Check identity/revision handling first. Totals are not silently rewritten. |
| Repeated prompt candidate | Explicitly opted-in matching scoped fingerprints, provider, model and project; distinct request identities within a 15-minute window | Equal complete request context/parameters, useless retries, or guaranteed savings. |
| Cache review | At least 3 requests, at least 10,000 normalized input tokens and no reported cache reads in a session/model | Cache support, actual miss rate or repeated content; absent telemetry can look identical. |
| Output spike | At least 5 prior requests in the same session/model; latest output at least 1,000 tokens and 3× prior median | Waste or equivalent task requirements. |
| Context growth | At least 3 context observations; at least 10,000 growth and 2× initial size | Exact occupancy, the cause of growth or obsolete content. Comparisons stop at compaction, reset, model switch or observed shrink. |
| Prompt length review | Explicit opt-in length of at least 8,000 characters | Token length, semantic redundancy or unnecessary instructions. |

Repeated-candidate evidence reports tokens and known cost **associated with later requests**, not confirmed avoidable work. It is only a candidate upper bound: evaluations, intentional repeats, retries or changed full context may all be valid. Identity-collision records are excluded from repeat/workload opportunity detection, while retained ledger consumption remains visible for source repair. Hash/window rules are deterministic and not statistically calibrated confidence probabilities.

Reports include at most 100 opportunities, 20 event IDs per opportunity and 20 largest requests. Aggregate consumption and coverage still span all retained events. Replay, revisions, retention and deletion affect the report through the canonical ledger, so telemetry replay alone creates no repeated-request finding.

## Optional private prompt metadata

Native session discovery never reads prompt content for this analysis. Default events carry no fingerprints or lengths; the UI explicitly reports unavailable duplicate analysis. An instrumented client may opt in to text-derived metadata:

```python
from backend.analytics.prompts import prompt_metadata
from backend.ingest import usage_event, publish

# Load >=32 random bytes from your application's protected local secret store.
# Reuse the key for the comparison period; do not place it in events/source control.
metadata = prompt_metadata(prompt_text, secret=private_fingerprint_key, enabled=True)
event = usage_event(
    "openai", response.model, session_id, response.id,
    response.usage.model_dump(), **metadata,
)
publish(event, local_tokenscope_token)
```

`enabled` defaults to false. The helper hashes the exact supplied UTF-8 text in memory with HMAC-SHA256; whitespace changes produce a different fingerprint. It returns only the digest, a key-derived comparison scope, Unicode character count and explicit consent flag. It never returns or persists raw text. The caller decides which text to measure; it need not include system context, tools or conversation history, so prompt equality is not complete-request equality. No provider keys or API traffic are intercepted.

Keep a stable private random key per installation; rotating it creates a new scope and prevents cross-key grouping. A minimum length check cannot verify key entropy; use a cryptographically random key. Do not reuse a provider API key as a fingerprint secret. API ingestion rejects fingerprint/length metadata without `prompt_analysis_opt_in=true`, and rejects unscoped or malformed digests.

Fingerprints **are sensitive metadata**: they reveal equality/frequency even without raw text; anyone with the key can test guesses. Scope IDs intentionally disclose shared-key comparability. Digests and lengths inherit the local authenticated API, exports, retention and deletion policies, and are never sent to an LLM. Do not enable them unless this disclosure fits your local privacy requirements. Uninstrumented requests are not assessed for prompt repetition.

## Implementation limits

Analytics use O(n log n) sorting and linear grouping over retained history; legacy per-session alerts now index usage once rather than repeatedly scanning every event for each session. Storage still loads the full retained ledger. This change does not add SQL pagination, task-quality measurement, semantic prompt similarity or an automated prompt rewriter. Prompt recommendations require manual task-specific evaluation.
