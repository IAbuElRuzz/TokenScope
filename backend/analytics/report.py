"""Deterministic opportunities from measured metadata, never inferred prompt content.

Token totals remain source totals. Diagnostic categories explain overlap rather than
adding cached/reasoning subsets to inclusive provider counts a second time.
"""

from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from statistics import median


def _stamp(event):
    return datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))


def token_parts(event):
    reads = event.get("cache_read_tokens", 0)
    writes = event.get("cache_creation_tokens", 0)
    reasoning = event.get("reasoning_tokens", 0)
    input_total = event.get("input_tokens", 0)
    output_total = event.get("output_tokens", 0)
    if not event.get("input_includes_cache", True):
        input_total += reads + writes
    if not event.get("output_includes_reasoning", True):
        output_total += reasoning
    valid = reads + writes <= input_total and reasoning <= output_total
    return {
        "input_total_tokens": input_total,
        "uncached_input_tokens": max(0, input_total - reads - writes),
        "cache_read_tokens": reads,
        "cache_creation_tokens": writes,
        "output_total_tokens": output_total,
        "reasoning_tokens": reasoning,
        "decomposition_valid": valid,
    }


def summarize(events):
    result = dict(
        requests=len(events),
        total_tokens=0,
        input_total_tokens=0,
        uncached_input_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        output_total_tokens=0,
        reasoning_tokens=0,
        known_cost_usd=None,
        billed_cost_usd=None,
        priced_requests=0,
        unpriced_requests=0,
        invalid_decomposition_requests=0,
        provider_total_residual_tokens=0,
    )
    for event in events:
        parts = token_parts(event)
        for key, value in parts.items():
            if key != "decomposition_valid":
                result[key] += value
        result["invalid_decomposition_requests"] += not parts["decomposition_valid"]
        result["total_tokens"] += event.get("total_tokens", 0) or 0
        # Preserve provider categories not represented in normalized fields (e.g. tool prompt usage).
        result["provider_total_residual_tokens"] += (
            (event.get("total_tokens", 0) or 0) - parts["input_total_tokens"] - parts["output_total_tokens"]
        )
        for source, destination in [("cost_usd", "known_cost_usd"), ("billed_cost_usd", "billed_cost_usd")]:
            if event.get(source) is not None:
                result[destination] = (result[destination] or 0) + event[source]
        result["priced_requests"] += event.get("cost_usd") is not None
    result["unpriced_requests"] = len(events) - result["priced_requests"]
    result["cache_read_ratio"] = (
        result["cache_read_tokens"] / result["input_total_tokens"]
        if result["input_total_tokens"] and not result["invalid_decomposition_requests"]
        else None
    )
    result["reasoning_output_ratio"] = (
        result["reasoning_tokens"] / result["output_total_tokens"]
        if result["output_total_tokens"] and not result["invalid_decomposition_requests"]
        else None
    )
    return result


def _request_key(event):
    return event["provider"], event["session_id"], event.get("request_id") or event["event_id"]


def build_report(events):
    """Aggregate the complete retained canonical ledger in O(n log n) time.

    Callers must supply revision-deduplicated events (Store.events does). Repeated
    prompt candidates never alter consumption or claim guaranteed savings.
    """
    requests = sorted(
        (e for e in events if e.get("kind") == "usage"), key=lambda e: (_stamp(e), e["event_id"])
    )
    summary = summarize(requests)
    breakdowns = {}
    for plural, field in [("providers", "provider"), ("models", "model"), ("sessions", "session_id")]:
        groups = defaultdict(list)
        for event in requests:
            # A model ID need not be globally unique between providers.
            key = f"{event['provider']}/{event['model']}" if field == "model" else event[field]
            groups[key].append(event)
        breakdowns[plural] = sorted(
            (dict(key=key, **summarize(group)) for key, group in groups.items()),
            key=lambda row: (-row["total_tokens"], row["key"]),
        )
    opportunities = []

    def opportunity(kind, title, evidence, recommendation, limitations, related, confidence="medium"):
        identifiers = sorted(e["event_id"] for e in related)
        digest = sha256((kind + ":".join(identifiers)).encode()).hexdigest()[:20]
        opportunities.append(
            dict(
                id=f"{kind}:{digest}",
                kind=kind,
                title=title,
                confidence=confidence,
                evidence=evidence,
                recommendation=recommendation,
                limitations=limitations,
                related_event_ids=identifiers[:20],
            )
        )

    identities = defaultdict(list)
    for event in requests:
        if event.get("request_id"):
            identities[_request_key(event)].append(event)
    duplicate_ids = set()
    for same_request in identities.values():
        if len(same_request) > 1:
            duplicate_ids.update(e["event_id"] for e in same_request)
            opportunity(
                "telemetry_identity_collision",
                "Multiple events identify the same request",
                {
                    "events": len(same_request),
                    "request_id": same_request[0]["request_id"],
                    "recorded_tokens": sum(e["total_tokens"] for e in same_request),
                },
                "Check adapter identity and revisions before interpreting this as repeated model work. Use one stable event ID per request.",
                [
                    "These records may be duplicate telemetry or request-ID reuse; they do not prove multiple provider calls.",
                    "Ledger totals are unchanged; repair the source before treating them as billed usage.",
                ],
                same_request,
            )

    fingerprints = defaultdict(list)
    for event in requests:
        if (
            event.get("prompt_analysis_opt_in")
            and event.get("prompt_fingerprint")
            and event.get("prompt_fingerprint_scope")
            and event["event_id"] not in duplicate_ids
        ):
            key = (
                event["provider"],
                event["model"],
                event.get("project"),
                event["prompt_fingerprint_scope"],
                event["prompt_fingerprint"],
            )
            fingerprints[key].append(event)
    for group in fingerprints.values():
        # Fixed 15-minute windows anchored to the first request avoid chaining a whole day together.
        clusters = []
        for event in group:
            if not clusters or (_stamp(event) - _stamp(clusters[-1][0])).total_seconds() > 900:
                clusters.append([])
            clusters[-1].append(event)
        for cluster in clusters:
            if len({_request_key(e) for e in cluster}) < 2:
                continue
            repeated = cluster[1:]
            priced = [e["cost_usd"] for e in repeated if e.get("cost_usd") is not None]
            opportunity(
                "repeated_prompt",
                "Identical prompt fingerprints across distinct requests",
                {
                    "requests": len(cluster),
                    "repeat_requests": len(repeated),
                    "repeat_associated_tokens": sum(e["total_tokens"] for e in repeated),
                    "repeat_known_cost_usd": sum(priced) if priced else None,
                    "repeat_priced_requests": len(priced),
                    "window_seconds": 900,
                },
                "Review whether these calls intentionally repeat work. If results can safely be reused, consider request coalescing, idempotency or an application result cache.",
                [
                    "Matching fingerprints establish identical supplied prompt text, not identical full model context or parameters.",
                    "Retries, evaluations and changing context can justify repetition. Associated usage is an upper bound, not recoverable savings.",
                    "Only explicitly opted-in prompt metadata is analyzed; uninstrumented requests are not assessed.",
                ],
                cluster,
            )

    # Context comparisons stop at an explicit boundary, model switch or observed shrink.
    context_observations = defaultdict(list)
    current_model = {}
    for event in sorted(events, key=lambda e: (_stamp(e), e["event_id"])):
        sid = event["session_id"]
        model = (event["provider"], event["model"])
        if event.get("kind") in ("compaction", "reset") or current_model.get(sid, model) != model:
            previous_model = current_model.get(sid)
            if previous_model is not None:
                context_observations.pop((sid, *previous_model), None)
        current_model[sid] = model
        if (
            event.get("kind") == "usage"
            and event.get("context_tokens") is not None
            and event.get("context_accuracy") != "unavailable"
        ):
            key = (sid, *model)
            previous = context_observations[key]
            if previous and event["context_tokens"] < previous[-1]["context_tokens"]:
                previous.clear()
            previous.append(event)
    session_models = defaultdict(list)
    for event in requests:
        if event["event_id"] in duplicate_ids:
            continue
        session_models[(event["session_id"], event["provider"], event["model"])].append(event)
    for own in session_models.values():
        totals = summarize(own)
        if len(own) >= 3 and totals["input_total_tokens"] >= 10000 and totals["cache_read_tokens"] == 0:
            opportunity(
                "cache_review",
                "High input usage with no reported cache reads",
                {
                    "session_id": own[0]["session_id"],
                    "requests": len(own),
                    "input_total_tokens": totals["input_total_tokens"],
                    "cache_read_tokens": 0,
                },
                "Check whether this model supports caching, whether the stable prefix qualifies, and whether cache metadata is available before changing prompt layout.",
                [
                    "Zero reported cache reads does not distinguish cache misses from unavailable cache telemetry.",
                    "Input volume alone does not establish repeated prompt content or guaranteed savings.",
                ],
                own,
                "low",
            )
        if len(own) >= 6:
            previous_outputs = [token_parts(e)["output_total_tokens"] for e in own[:-1]]
            baseline = median(previous_outputs)
            current = token_parts(own[-1])["output_total_tokens"]
            if baseline > 0 and current >= baseline * 3 and current >= 1000:
                opportunity(
                    "output_spike",
                    "Latest output is unusually large for this session and model",
                    {
                        "session_id": own[0]["session_id"],
                        "latest_output_tokens": current,
                        "previous_median_output_tokens": baseline,
                        "ratio": current / baseline,
                        "baseline_requests": len(previous_outputs),
                    },
                    "Review whether a concise output format or a task-appropriate output limit would meet the same requirements.",
                    [
                        "Tasks may differ within a session; a larger response is not inherently inefficient.",
                        "Reasoning tokens are included only where provider accounting defines them as output.",
                    ],
                    [own[-1]],
                )
        observed = context_observations.get((own[0]["session_id"], own[0]["provider"], own[0]["model"]), [])
        if len(observed) >= 3:
            first, last = observed[0], observed[-1]
            growth = last["context_tokens"] - first["context_tokens"]
            if (
                first["context_tokens"] > 0
                and growth >= 10000
                and last["context_tokens"] >= first["context_tokens"] * 2
            ):
                opportunity(
                    "context_growth",
                    "Reported request context grew substantially",
                    {
                        "session_id": last["session_id"],
                        "first_context_tokens": first["context_tokens"],
                        "latest_context_tokens": last["context_tokens"],
                        "growth_tokens": growth,
                        "context_accuracy": last.get("context_accuracy", "estimated"),
                    },
                    "Review whether stale tool output, history or background material can be summarized while preserving task requirements.",
                    [
                        "Context estimates are request observations, not cumulative usage or exact live occupancy.",
                        "Growth can be required by the task; this finding does not identify which content caused it.",
                    ],
                    [first, last],
                )
        lengths = [
            e for e in own if e.get("prompt_analysis_opt_in") and e.get("prompt_characters") is not None
        ]
        large = [e for e in lengths if e["prompt_characters"] >= 8000]
        if large:
            opportunity(
                "prompt_length",
                "Large explicitly instrumented prompt text",
                {
                    "requests": len(large),
                    "maximum_prompt_characters": max(e["prompt_characters"] for e in large),
                    "instrumented_requests": len(lengths),
                },
                "Review long boilerplate and examples for task relevance. Measure the result on representative tasks before shortening instructions.",
                [
                    "Character length is not token length, semantic redundancy or proof of waste.",
                    "Only the supplied prompt text is measured; system messages, tools and other context may be omitted.",
                ],
                large,
                "low",
            )

    largest = []
    for event in sorted(requests, key=lambda e: (-e["total_tokens"], e["event_id"]))[:20]:
        largest.append(
            {
                **{
                    key: event.get(key)
                    for key in (
                        "event_id",
                        "session_id",
                        "provider",
                        "model",
                        "timestamp",
                        "total_tokens",
                        "cost_usd",
                        "prompt_characters",
                    )
                },
                **{
                    key: value
                    for key, value in token_parts(event).items()
                    if key in ("input_total_tokens", "output_total_tokens")
                },
            }
        )
    return {
        "scope": {
            "events": len(events),
            "usage_requests": len(requests),
            "period_start": requests[0]["timestamp"] if requests else None,
            "period_end": requests[-1]["timestamp"] if requests else None,
        },
        "summary": summary,
        "breakdowns": breakdowns,
        "largest_requests": largest,
        "opportunities": opportunities[:100],
        "opportunities_total": len(opportunities),
        "coverage": {
            "total_requests": len(requests),
            "priced_requests": summary["priced_requests"],
            "unpriced_requests": summary["unpriced_requests"],
            "billed_requests": sum(e.get("billed_cost_usd") is not None for e in requests),
            "prompt_fingerprint_requests": sum(
                bool(e.get("prompt_analysis_opt_in") and e.get("prompt_fingerprint")) for e in requests
            ),
            "prompt_length_requests": sum(
                e.get("prompt_analysis_opt_in", False) and e.get("prompt_characters") is not None
                for e in requests
            ),
            "telemetry_identity_collision_events": len(duplicate_ids),
        },
        "limits": [
            "Full retained usage ledger; independent of the dashboard session/date filters and recent 1,000-event chart sample.",
            "Estimated API-equivalent costs exclude unpriced requests and are separate from authoritative billed costs.",
            "Cache and reasoning counts are subsets where provider semantics specify; provider total residuals can represent additional categories.",
            "Recommendations are deterministic review opportunities, not confirmed waste, equivalent-quality model comparisons or guaranteed savings.",
            "Prompt metadata is absent by default. No raw prompts, provider requests or AI explanations are generated by this analysis.",
            "At most 100 opportunities, 20 related event IDs per opportunity and 20 largest requests are included.",
        ],
    }
