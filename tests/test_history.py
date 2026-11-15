"""Full-history filtering/investigation on synthetic data, including tenant boundaries."""

import json
import sqlite3
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.analytics.prompts import prompt_metadata
from backend.api.app import create_app
from backend.core.schema import UsageEvent
from backend.pricing.engine import Pricing
from backend.server.app import create_server_app
from backend.server.auth import COOKIE
from backend.storage.history import HistoryFilters
from backend.storage.store import Store

STAMP = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def event(index=0, **changes):
    data = dict(
        event_id=f"e{index:04}",
        request_id=f"r{index}",
        session_id="session",
        provider="openai",
        tool="codex",
        model="known",
        timestamp=STAMP + timedelta(seconds=index),
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=50,
        reasoning_tokens=10,
        project="project",
        device_id="device",
    )
    data.update(changes)
    return UsageEvent(**data)


class Empty:
    def poll(self):
        return []

    def doctor(self):
        return []


def test_history_over_thousand_paginates_all_matches_and_full_totals(tmp_path):
    store = Store(tmp_path / "db")
    for index in range(1051):
        store.ingest(event(index))
    first = store.history(page=1, page_size=200)
    assert len(first["items"]) == 200 and first["total_items"] == 1051 and first["total_pages"] == 6
    assert first["items"][0]["event_id"] == "e1050"
    assert first["totals"]["total_tokens"] == 1051 * 120
    assert first["analytics"]["summary"]["requests"] == 1051
    last = store.history(page=6, page_size=200)
    assert len(last["items"]) == 51 and last["items"][-1]["event_id"] == "e0000"
    beyond = store.history(page=7, page_size=200)
    assert beyond["items"] == [] and beyond["total_pages"] == 6 and beyond["total_items"] == 1051
    assert len(store.snapshot()["events"]) == 1000
    assert len(store.filtered_events()) == 1051
    store.close()


def test_time_boundaries_microseconds_offsets_and_deterministic_ties(tmp_path):
    store = Store(tmp_path / "db")
    store.ingest(event(event_id="a", timestamp="2026-11-15T13:00:00+01:00"))
    store.ingest(event(event_id="b", timestamp="2026-11-15T12:00:00Z"))
    store.ingest(event(event_id="micro", timestamp="2026-11-15T12:00:00.000001Z"))
    store.ingest(event(event_id="later", timestamp="2026-11-15T12:00:01Z"))
    filters = HistoryFilters(start=STAMP, end=STAMP + timedelta(microseconds=1))
    result = store.history(filters)
    assert [e["event_id"] for e in result["items"]] == ["b", "a"]
    assert result["totals"]["total_tokens"] == 240
    assert [e["event_id"] for e in store.events()] == ["a", "b", "micro", "later"]
    assert store.history(HistoryFilters(start=STAMP + timedelta(microseconds=1)))["total_items"] == 2
    store.close()


def test_old_ledger_chronological_index_migration_preserves_events(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE events(id TEXT PRIMARY KEY,revision INTEGER,timestamp TEXT,session_id TEXT,payload TEXT)"
    )
    original = event().model_dump(mode="json")
    db.execute(
        "INSERT INTO events VALUES(?,?,?,?,?)",
        (original["event_id"], 0, original["timestamp"], original["session_id"], json.dumps(original)),
    )
    db.commit()
    db.close()
    store = Store(path)
    assert store.history()["items"][0]["event_id"] == original["event_id"]
    store.ingest(event(1))
    assert store.history()["total_items"] == 2
    store.close()
    store = Store(path)
    assert store.history()["items"][0]["event_id"] == "e0001"
    store.close()


def test_all_exact_filters_combine_and_sql_values_are_bound(tmp_path):
    store = Store(tmp_path / "db")
    store.ingest(event())
    for index, field in enumerate(
        ["provider", "model", "tool", "session_id", "project", "device_id", "request_id"], 1
    ):
        store.ingest(event(index, **{field: "other"}))
    filters = HistoryFilters(
        provider="openai",
        model="known",
        tool="codex",
        session_id="session",
        project="project",
        device_id="device",
        request_id="r0",
    )
    result = store.history(filters)
    assert result["total_items"] == 1 and result["totals"]["requests"] == 1
    assert result["options"]["providers"] == ["openai", "other"]  # full own ledger options
    assert store.history(HistoryFilters(provider="openai' OR 1=1 --"))["total_items"] == 0
    assert len(store.filtered_events(filters)) == 1
    store.close()


def test_filtered_accounting_cost_coverage_and_detail_pricing_provenance(tmp_path):
    target = tmp_path / "prices.json"
    target.write_text(
        json.dumps(
            {
                "version": "contract-v1",
                "models": [
                    {
                        "provider": "openai",
                        "model": "known",
                        "effective_from": "2020-01-01",
                        "input": 2,
                        "output": 8,
                        "cache_read": 1,
                        "source": "internal-contract",
                    }
                ],
            }
        )
    )
    store = Store(tmp_path / "db", Pricing(target))
    store.ingest(event())
    store.ingest(event(1, model="unknown"))
    totals = store.history()["totals"]
    assert totals["priced_requests"] == 1 and totals["unpriced_requests"] == 1
    assert totals["known_cost_usd"] == pytest.approx(0.00031)
    detail = store.request_detail("e0000")
    assert detail["accounting"]["input_total_tokens"] == 100
    assert detail["accounting"]["uncached_input_tokens"] == 50
    assert detail["accounting"]["reasoning_tokens"] == 10
    assert detail["pricing"]["source"] == "internal-contract"
    assert detail["pricing"]["version"] == "contract-v1"
    assert detail["pricing"]["estimated_cost_usd"] == pytest.approx(0.00031)
    assert detail["pricing"]["billed_cost_usd"] is None
    assert store.request_detail("not-present") is None
    store.close()


def test_detail_duplicate_identity_and_opt_in_prompt_candidates_are_distinct(tmp_path):
    store = Store(tmp_path / "db")
    metadata = prompt_metadata("private prompt", secret=b"k" * 32, enabled=True)
    store.ingest(event(**metadata))
    store.ingest(event(1, **metadata))
    detail = store.request_detail("e0000")
    assert detail["related"]["repeated_prompt_candidate_count"] == 1
    assert detail["related"]["telemetry_identity_collision_count"] == 0
    assert "private prompt" not in json.dumps(detail)
    store.ingest(event(2, request_id="r0", **metadata))
    detail = store.request_detail("e0000")
    assert detail["related"]["telemetry_identity_collision_count"] == 1
    assert detail["related"]["repeated_prompt_candidates"] == []
    detail = store.request_detail("e0001")
    assert detail["related"]["repeated_prompt_candidates"] == []  # exclude candidate collisions too
    store.close()


def test_detail_candidates_respect_project_model_time_and_missing_request_ids(tmp_path):
    store = Store(tmp_path / "db")
    metadata = prompt_metadata("same", secret=b"k" * 32, enabled=True)
    store.ingest(event(request_id=None, **metadata))
    store.ingest(event(1, request_id=None, **metadata))
    store.ingest(event(2, model="other", **metadata))
    store.ingest(event(3, project="other", **metadata))
    store.ingest(event(1000, **metadata))
    assert store.request_detail("e0000")["related"]["repeated_prompt_candidate_count"] == 1
    store.close()


def test_local_api_auth_filters_export_and_invalid_queries(tmp_path):
    app = create_app(tmp_path, Empty(), token="secret")
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret"}
        for index in range(1051):
            app.state.store.ingest(event(index))
        assert client.get("/api/history").status_code == 401
        assert client.get("/api/history/requests/e0000").status_code == 401
        response = client.get(
            "/api/history",
            headers=headers,
            params={
                "page_size": 10,
                "start": STAMP.isoformat(),
                "end": (STAMP + timedelta(seconds=1001)).isoformat(),
            },
        )
        assert response.status_code == 200
        assert len(response.json()["items"]) == 10 and response.json()["total_items"] == 1001
        export = client.get(
            "/api/export", headers=headers, params={"end": (STAMP + timedelta(seconds=1001)).isoformat()}
        ).json()
        assert len(export["events"]) == 1001
        assert "end" in export["filters"]
        for query in [
            "page=0",
            "page_size=201",
            "start=invalid",
            "start=2026-11-15T12:00:00",
            "start=2026-11-16T00:00:00Z&end=2026-11-15T00:00:00Z",
            "page=9999999999999999999999999",
        ]:
            assert client.get("/api/history?" + query, headers=headers).status_code == 422
        assert client.get("/api/history/requests/missing", headers=headers).status_code == 404


def test_server_history_detail_export_and_options_are_tenant_scoped(tmp_path):
    origin = "https://testserver"
    app = create_server_app(tmp_path, origin)
    alice = app.state.auth.create_user("alice", "long-password-one")
    bob = app.state.auth.create_user("bob", "long-password-two")
    app.state.tenants.ledger(alice["id"]).ingest(event(event_id="alice-only", project="private-a"))
    app.state.tenants.ledger(bob["id"]).ingest(event(event_id="bob-only", project="private-b"))
    with TestClient(app, base_url=origin, headers={"Origin": origin}) as client:
        login = client.post("/api/auth/login", json={"username": "bob", "password": "long-password-two"})
        assert login.status_code == 200
        assert client.cookies.get(COOKIE)
        result = client.get("/api/history").json()
        assert result["total_items"] == 1 and result["items"][0]["event_id"] == "bob-only"
        assert result["options"]["projects"] == ["private-b"]
        assert client.get("/api/history/requests/alice-only").status_code == 404
        assert client.get("/api/history/requests/bob-only").status_code == 200
        assert client.get("/api/history?project=private-a").json()["total_items"] == 0
        assert client.get("/api/export?project=private-a").json()["events"] == []
        assert client.get("/api/export?project=private-b").json()["events"][0]["event_id"] == "bob-only"


def test_partial_migration_backfill_and_exact_retention_boundary(tmp_path):
    path = tmp_path / "partial.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE events(id TEXT PRIMARY KEY,revision INTEGER,timestamp TEXT,session_id TEXT,payload TEXT,occurred_us INTEGER)"
    )
    original = event().model_dump(mode="json")
    db.execute(
        "INSERT INTO events VALUES(?,?,?,?,?,NULL)",
        (original["event_id"], 0, original["timestamp"], original["session_id"], json.dumps(original)),
    )
    db.commit()
    db.close()
    store = Store(path)
    assert store.history(HistoryFilters(start=STAMP))["total_items"] == 1
    assert store.delete(before=STAMP.isoformat(timespec="microseconds")) == 0
    assert store.delete(before=(STAMP + timedelta(microseconds=1)).isoformat()) == 1
    store.close()


def test_filtered_context_analysis_keeps_excluded_reset_and_model_boundaries(tmp_path):
    store = Store(tmp_path / "db")
    for i, n in enumerate([10000, 20000, 30000]):
        store.ingest(event(i, context_tokens=n, context_accuracy="estimated"))
    assert any(
        o["kind"] == "context_growth"
        for o in store.history(HistoryFilters(model="known"))["analytics"]["opportunities"]
    )
    store.ingest(
        event(
            event_id="boundary",
            kind="compaction",
            model="unknown",
            timestamp=STAMP + timedelta(microseconds=1),
        )
    )
    filtered = store.history(HistoryFilters(model="known"))
    assert filtered["total_items"] == 3
    assert filtered["analytics"]["scope"]["events"] == 3
    assert not any(o["kind"] == "context_growth" for o in filtered["analytics"]["opportunities"])
    store.close()
