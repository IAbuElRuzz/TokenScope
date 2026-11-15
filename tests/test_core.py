import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.core.schema import UsageEvent
from backend.pricing.engine import Pricing
from backend.storage.store import Store


def event(**changes):
    data = dict(
        event_id="e",
        provider="openai",
        tool="codex",
        model="test",
        session_id="s",
        timestamp=datetime.now(timezone.utc),
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=50,
        reasoning_tokens=10,
    )
    data.update(changes)
    return UsageEvent(**data)


def test_accounting():
    assert event().total_tokens == 120
    assert event(input_includes_cache=False).total_tokens == 170
    assert event(output_includes_reasoning=False).total_tokens == 130
    with pytest.raises(ValueError):
        event(timestamp=datetime.now())
    with pytest.raises(ValueError):
        event(input_tokens=-1)


def test_replay_revisions_out_of_order_and_restart(tmp_path):
    path = tmp_path / "usage.db"
    store = Store(path)
    assert store.ingest(event(revision=1))
    assert not store.ingest(event(revision=1))
    assert store.ingest(event(revision=2, output_tokens=40))
    assert not store.ingest(event(revision=1))
    store.close()
    store = Store(path)
    assert not store.ingest(event(revision=2, output_tokens=40))
    assert store.snapshot()["sessions"][0]["total_tokens"] == 140
    store.delete(session_id="s")
    assert not store.ingest(event(revision=3))
    assert not store.events()
    store.close()


def test_context_latest_not_sum(tmp_path):
    store = Store(tmp_path / "db")
    store.ingest(event(context_tokens=80, context_window=100, context_accuracy="estimated"))
    store.ingest(
        event(
            event_id="next",
            input_tokens=1000,
            context_tokens=30,
            context_window=100,
            context_accuracy="estimated",
        )
    )
    session = store.snapshot()["sessions"][0]
    assert session["input_tokens"] == 1100
    assert session["context_percent"] == 30
    store.close()


def test_price_subsets_and_history(tmp_path):
    catalog = {
        "version": "test",
        "models": [
            {
                "provider": "openai",
                "model": "test",
                "effective_from": "2020-01-01",
                "input": 2,
                "output": 8,
                "cache_read": 1,
                "cache_write": 3,
                "source": "custom",
            }
        ],
    }
    target = tmp_path / "pricing.json"
    target.write_text(json.dumps(catalog))
    store = Store(tmp_path / "db", Pricing(target))
    store.ingest(event())
    assert store.events()[0]["cost_usd"] == pytest.approx(0.00031)
    catalog["models"][0]["input"] = 100
    target.write_text(json.dumps(catalog))
    store.pricing = Pricing(target)
    assert store.events()[0]["cost_usd"] == pytest.approx(0.00031)
    store.close()


def test_concurrent_sessions(tmp_path):
    store = Store(tmp_path / "db")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: store.ingest(event(event_id=str(i), session_id=str(i % 2))), range(100)))
    snapshot = store.snapshot()
    assert len(snapshot["sessions"]) == 2
    assert sum(s["total_tokens"] for s in snapshot["sessions"]) == 12000
    store.close()


class EmptyScanner:
    def poll(self):
        return []

    def doctor(self):
        return []


def test_api_auth_websocket_reconnect_and_deletion(tmp_path):
    app = create_app(tmp_path, EmptyScanner(), token="test-secret")
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test-secret"}
        assert client.get("/api/snapshot").status_code == 401
        assert (
            client.get("/api/snapshot", headers={**headers, "Origin": "https://evil.example"}).status_code
            == 403
        )
        assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json({"token": "test-secret"})
            assert ws.receive_json()["sessions"] == []
            response = client.post("/api/events", headers=headers, json=event().model_dump(mode="json"))
            assert response.json()["accepted"]
            assert ws.receive_json()["sessions"][0]["total_tokens"] == 120
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json({"token": "test-secret"})
            assert ws.receive_json()["sessions"][0]["total_tokens"] == 120
        assert client.get("/api/export", headers=headers).json()["events"][0]["event_id"] == "e"
        assert client.delete("/api/sessions/s", headers=headers).json()["deleted"] == 1
        assert not client.post("/api/events", headers=headers, json=event().model_dump(mode="json")).json()[
            "accepted"
        ]


def test_model_change_invalidates_context_and_bad_auth(tmp_path):
    store = Store(tmp_path / "db")
    store.ingest(event(context_tokens=80, context_window=100, context_accuracy="estimated"))
    store.ingest(event(event_id="new", model="different"))
    assert store.snapshot()["sessions"][0]["context_tokens"] is None
    assert store.snapshot()["sessions"][0]["context_window"] is None
    store.close()
    with TestClient(create_app(tmp_path / "api", EmptyScanner(), token="secret")) as client:
        assert client.get("/health", headers={"content-length": "bad"}).status_code == 400
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json([])
            with pytest.raises(Exception):
                ws.receive_json()


def test_local_transport_rejects_userinfo_and_redirects():
    from backend.core.transport import local_url, NoRedirect

    assert local_url("http://localhost:8000") == "http://localhost:8000"
    for url in [
        "http://localhost:123@evil.example",
        "http://evil.example:8000",
        "https://localhost:8000",
        "http://localhost:8000/?x=y",
    ]:
        with pytest.raises(ValueError):
            local_url(url)
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "http://evil.example") is None


def test_alerts_and_retention(tmp_path, monkeypatch):
    from datetime import timedelta

    monkeypatch.setenv("TOKENSCOPE_SESSION_BUDGET", "0")
    store = Store(tmp_path / "db")
    store.ingest(event(context_tokens=96, context_window=100, context_accuracy="estimated"))
    assert any(a["id"].startswith("context:") for a in store.snapshot()["alerts"])
    old = event(event_id="old", timestamp=datetime.now(timezone.utc) - timedelta(days=100))
    store.ingest(old)
    assert store.retain(90) == 1
    assert not store.ingest(old)
    store.close()


def test_provider_unknown_and_absent_usage():
    from backend.adapters import normalize_usage

    with pytest.raises(ValueError):
        normalize_usage("unknown", {"input_tokens": 10})
    with pytest.raises(ValueError):
        normalize_usage("openai", {})


def test_two_source_automatic_ingestion_and_restart(tmp_path):
    import time
    from backend.adapters import LocalAdapters

    roots = [tmp_path / name for name in ("claude", "codex", "gemini")]
    for root in roots:
        root.mkdir()
    stamp = datetime.now(timezone.utc).isoformat()
    claude = {
        "type": "assistant",
        "sessionId": "c",
        "timestamp": stamp,
        "message": {"id": "c1", "model": "test", "usage": {"input_tokens": 10, "output_tokens": 5}},
    }
    codex = [
        {"type": "session_meta", "payload": {"id": "o"}},
        {
            "type": "event_msg",
            "timestamp": stamp,
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}},
            },
        },
    ]
    (roots[0] / "session.jsonl").write_text(json.dumps(claude) + "\n")
    (roots[1] / "rollout.jsonl").write_text("".join(json.dumps(e) + "\n" for e in codex))
    for restart in range(2):
        app = create_app(tmp_path / "data", LocalAdapters(*roots), token="secret")
        with TestClient(app) as client:
            with client.websocket_connect("/api/ws") as ws:
                ws.send_json({"token": "secret"})
                snap = ws.receive_json()
                if len(snap["sessions"]) < 2:
                    snap = ws.receive_json()
                assert len(snap["sessions"]) == 2
                assert sorted(s["total_tokens"] for s in snap["sessions"]) == [15, 30]
                assert sum(s["total_tokens"] for s in snap["sessions"]) == 45
                if restart == 0:
                    started = time.perf_counter()
                    newer = event(event_id="latency")
                    client.post(
                        "/api/events",
                        headers={"Authorization": "Bearer secret"},
                        json=newer.model_dump(mode="json"),
                    )
                    update = ws.receive_json()
                    assert any(e["event_id"] == "latency" for e in update["events"])
                    assert time.perf_counter() - started < 0.5
                    client.delete("/api/sessions/s", headers={"Authorization": "Bearer secret"})


def test_snapshot_sessions_newest_first_preserves_full_history(tmp_path):
    store = Store(tmp_path/'db')
    store.ingest(event(event_id='old',session_id='past',timestamp=datetime(2026,1,1,tzinfo=timezone.utc)))
    store.ingest(event(event_id='new',session_id='latest',timestamp=datetime(2026,1,3,tzinfo=timezone.utc)))
    store.ingest(event(event_id='middle',session_id='middle',timestamp=datetime(2026,1,2,tzinfo=timezone.utc)))
    sessions = store.snapshot()['sessions']
    assert [s['session_id'] for s in sessions] == ['latest','middle','past']
    assert sum(s['total_tokens'] for s in sessions) == 360
    store.ingest(event(event_id='return',session_id='past',timestamp=datetime(2026,1,4,tzinfo=timezone.utc)))
    sessions = store.snapshot()['sessions']
    assert sessions[0]['session_id'] == 'past'
    assert sessions[0]['total_tokens'] == 240
    store.close()
