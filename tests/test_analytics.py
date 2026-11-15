"""Synthetic metadata only: these tests never read user prompts or session logs."""

import json
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.analytics.prompts import prompt_metadata
from backend.analytics.report import build_report, token_parts
from backend.api.app import create_app
from backend.core.schema import UsageEvent
from backend.storage.store import Store

STAMP = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def record(index=0, **overrides):
    fields = dict(
        event_id=f"e{index}",
        request_id=f"r{index}",
        provider="openai",
        tool="test",
        model="model",
        session_id="session",
        timestamp=STAMP + timedelta(seconds=index),
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=50,
        reasoning_tokens=10,
    )
    fields.update(overrides)
    return UsageEvent(**fields).model_dump(mode="json")


def kinds(report):
    return {item["kind"] for item in report["opportunities"]}


def test_token_decomposition_preserves_provider_overlaps():
    report = build_report(
        [
            record(),
            record(
                1,
                provider="anthropic",
                input_includes_cache=False,
                cache_creation_tokens=30,
                output_includes_reasoning=False,
            ),
        ]
    )
    summary = report["summary"]
    assert summary["total_tokens"] == 330  # 120 inclusive + (100+50+30+20+10)
    assert summary["input_total_tokens"] == 280
    assert summary["uncached_input_tokens"] == 150
    assert summary["cache_read_tokens"] == 100
    assert summary["cache_creation_tokens"] == 30
    assert summary["output_total_tokens"] == 50
    assert summary["reasoning_tokens"] == 20  # subset of output_total; do not add again
    assert summary["cache_read_ratio"] == pytest.approx(100 / 280)
    assert summary["provider_total_residual_tokens"] == 0
    assert len(report["breakdowns"]["models"]) == 2  # same ID, different providers


def test_provider_total_residual_and_invalid_categories_are_visible():
    event = record(total_tokens=140)
    report = build_report([event])
    assert report["summary"]["total_tokens"] == 140
    assert report["summary"]["provider_total_residual_tokens"] == 20
    report = build_report([record(cache_read_tokens=200)])
    assert report["summary"]["invalid_decomposition_requests"] == 1
    assert report["summary"]["cache_read_ratio"] is None
    assert token_parts(record(reasoning_tokens=100))["decomposition_valid"] is False


def test_pricing_coverage_never_turns_unknown_into_free():
    first = record()
    first["cost_usd"] = 0.2
    second = record(1, billed_cost_usd=0.1)
    second["cost_usd"] = None
    report = build_report([first, second])
    assert report["summary"]["known_cost_usd"] == 0.2
    assert report["summary"]["billed_cost_usd"] == 0.1
    assert report["coverage"]["priced_requests"] == 1
    assert report["coverage"]["unpriced_requests"] == 1
    assert report["coverage"]["billed_requests"] == 1
    assert build_report([record()])["summary"]["known_cost_usd"] is None
    first["cost_usd"] = 0
    assert build_report([first])["summary"]["known_cost_usd"] == 0


def test_no_prompt_metadata_by_default_or_raw_prompt_persistence():
    assert prompt_metadata("private text", secret=b"k" * 32) == {}
    assert "repeated_prompt" not in kinds(build_report([record(), record(1)]))
    with pytest.raises(ValueError):
        prompt_metadata("private", secret=b"short", enabled=True)
    metadata = prompt_metadata("TOP SECRET", secret=b"k" * 32, enabled=True)
    assert metadata["prompt_characters"] == 10
    assert "TOP SECRET" not in json.dumps(metadata)
    event = record(**metadata)
    assert "TOP SECRET" not in json.dumps(event)
    assert build_report([event])["coverage"]["prompt_fingerprint_requests"] == 1


def test_metadata_requires_explicit_opt_in_and_scope():
    with pytest.raises(ValueError):
        record(prompt_characters=100)
    with pytest.raises(ValueError):
        record(prompt_analysis_opt_in=True, prompt_fingerprint="a" * 64)
    with pytest.raises(ValueError):
        record(
            prompt_analysis_opt_in=True, prompt_fingerprint="plain prompt", prompt_fingerprint_scope="scope"
        )
    assert record(prompt_analysis_opt_in=True, prompt_characters=0)["prompt_characters"] == 0


def test_fingerprint_scope_rotation_exact_text_and_secret():
    a = prompt_metadata("same prompt", secret=b"a" * 32, enabled=True)
    assert a == prompt_metadata("same prompt", secret=b"a" * 32, enabled=True)
    assert (
        a["prompt_fingerprint"]
        != prompt_metadata("same prompt ", secret=b"a" * 32, enabled=True)["prompt_fingerprint"]
    )
    changed = prompt_metadata("same prompt", secret=b"b" * 32, enabled=True)
    assert a["prompt_fingerprint_scope"] != changed["prompt_fingerprint_scope"]
    assert "repeated_prompt" not in kinds(build_report([record(**a), record(1, **changed)]))


def test_repeated_distinct_requests_evidence_is_not_guaranteed_savings():
    metadata = prompt_metadata("same prompt", secret=b"a" * 32, enabled=True)
    first = record(**metadata)
    first["cost_usd"] = 0.1
    second = record(1, **metadata)
    second["cost_usd"] = None
    third = record(2, **metadata)
    third["cost_usd"] = 0.3
    report = build_report([third, first, second])
    finding = next(item for item in report["opportunities"] if item["kind"] == "repeated_prompt")
    assert finding["evidence"]["requests"] == 3
    assert finding["evidence"]["repeat_associated_tokens"] == 240
    assert finding["evidence"]["repeat_known_cost_usd"] == 0.3
    assert finding["evidence"]["repeat_priced_requests"] == 1
    assert any("not recoverable savings" in value for value in finding["limitations"])
    assert report["summary"]["total_tokens"] == 360
    assert report == build_report([second, third, first])


def test_repetition_does_not_cross_time_model_project_or_scope_boundaries():
    metadata = prompt_metadata("same prompt", secret=b"a" * 32, enabled=True)
    events = [
        record(**metadata),
        record(1, model="other", **metadata),
        record(2, project="other", **metadata),
        record(1000, **metadata),
    ]
    assert "repeated_prompt" not in kinds(build_report(events))


def test_telemetry_identity_collision_is_separate_from_repeated_work():
    metadata = prompt_metadata("same prompt", secret=b"a" * 32, enabled=True)
    report = build_report([record(**metadata), record(1, request_id="r0", **metadata)])
    assert kinds(report) == {"telemetry_identity_collision"}
    assert report["coverage"]["telemetry_identity_collision_events"] == 2
    assert report["summary"]["total_tokens"] == 240  # never silently rewrite ledger


def test_output_baseline_and_prompt_length_opportunities():
    metadata = prompt_metadata("a" * 8000, secret=b"a" * 32, enabled=True)
    events = [record(i, output_tokens=100) for i in range(5)] + [record(5, output_tokens=1500, **metadata)]
    report = build_report(events)
    assert {"output_spike", "prompt_length"} <= kinds(report)
    spike = next(item for item in report["opportunities"] if item["kind"] == "output_spike")
    assert spike["evidence"]["ratio"] == 15
    assert "output_spike" not in kinds(build_report(events[:5]))
    assert "prompt_length" not in kinds(
        build_report([record(prompt_analysis_opt_in=True, prompt_characters=7999)])
    )


def test_context_growth_respects_compaction_model_switch_and_shrink():
    events = [
        record(i, context_tokens=n, context_accuracy="estimated") for i, n in enumerate([10000, 20000, 30000])
    ]
    assert "context_growth" in kinds(build_report(events))
    boundary = record(
        1, kind="compaction", timestamp=STAMP + timedelta(seconds=0.5), input_tokens=0, output_tokens=0
    )
    assert "context_growth" not in kinds(build_report(events + [boundary]))
    switch = record(1, model="different", timestamp=STAMP + timedelta(seconds=0.5))
    assert "context_growth" not in kinds(build_report(events + [switch]))
    shrunk = record(3, context_tokens=5000, context_accuracy="estimated")
    assert "context_growth" not in kinds(build_report(events + [shrunk]))


def test_zero_cache_reads_is_low_confidence_not_miss_rate():
    report = build_report([record(i, input_tokens=4000, cache_read_tokens=0) for i in range(3)])
    cache = next(item for item in report["opportunities"] if item["kind"] == "cache_review")
    assert cache["confidence"] == "low"
    assert any("unavailable cache telemetry" in text for text in cache["limitations"])


def test_report_ignores_lifecycle_for_token_consumption_and_handles_empty():
    report = build_report([record(kind="compaction")])
    assert report["summary"]["requests"] == 0
    assert report["summary"]["total_tokens"] == 0
    assert report["scope"]["events"] == 1
    assert report["scope"]["period_start"] is None
    assert report["opportunities"] == []
    assert build_report([])["summary"]["cache_read_ratio"] is None


def test_revision_replay_restart_deletion_and_full_history_report(tmp_path):
    path = tmp_path / "db"
    store = Store(path)
    for i in range(1005):
        store.ingest(UsageEvent.model_validate(record(i)))
    assert not store.ingest(UsageEvent.model_validate(record()))
    store.ingest(UsageEvent.model_validate(record(revision=1, input_tokens=200)))
    snapshot = store.snapshot()
    assert len(snapshot["events"]) == 1000
    assert snapshot["analytics"]["summary"]["requests"] == 1005
    assert snapshot["analytics"]["summary"]["total_tokens"] == 1005 * 120 + 100
    store.close()
    store = Store(path)
    assert store.snapshot()["analytics"]["summary"] == snapshot["analytics"]["summary"]
    store.delete(session_id="session")
    assert store.snapshot()["analytics"]["summary"]["requests"] == 0
    store.close()


class EmptyScanner:
    def poll(self):
        return []

    def doctor(self):
        return []


def test_analytics_endpoint_authenticated_and_snapshot_consistent(tmp_path):
    with TestClient(create_app(tmp_path, EmptyScanner(), token="secret")) as client:
        headers = {"Authorization": "Bearer secret"}
        assert client.get("/api/analytics").status_code == 401
        assert client.post("/api/events", headers=headers, json=record()).status_code == 200
        report = client.get("/api/analytics", headers=headers).json()
        assert report == client.get("/api/snapshot", headers=headers).json()["analytics"]
        assert report["coverage"]["prompt_fingerprint_requests"] == 0
        assert report["largest_requests"][0]["event_id"] == "e0"
