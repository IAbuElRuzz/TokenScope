"""Central server integration tests use synthetic usage and accounts, never host logs."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from io import BytesIO
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from backend.server.app import create_server_app, server_origin
from backend.server.auth import AuthDB, COOKIE, password_hash, password_matches, digest
from backend.server.collector import (
    Collector,
    CollectorAuthorizationError,
    Spool,
    collector_origin,
    read_device_token,
)
from backend.server.limits import BodyLimitMiddleware

ORIGIN = "https://testserver"
PASSWORD = "testing-password-one!"


def event(**changes):
    data = dict(
        event_id="same-event",
        session_id="same-session",
        request_id="same-request",
        timestamp=datetime.now(timezone.utc).isoformat(),
        provider="openai",
        tool="codex",
        model="test",
        input_tokens=100,
        output_tokens=20,
    )
    data.update(changes)
    return data


@pytest.fixture
def central(tmp_path):
    app = create_server_app(tmp_path, ORIGIN)
    alice = app.state.auth.create_user("alice", PASSWORD, "admin")
    bob = app.state.auth.create_user("bob", PASSWORD)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield app, client, alice, bob, tmp_path


def login(client, name):
    client.cookies.clear()  # Separate browser sessions for isolation tests.
    response = client.post("/api/auth/login", json={"username": name, "password": PASSWORD})
    assert response.status_code == 200
    return {
        "Cookie": f"{COOKIE}={response.cookies[COOKIE]}",
        "Origin": ORIGIN,
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def enroll(client, headers, name="laptop"):
    response = client.post("/api/devices", headers=headers, json={"name": name})
    assert response.status_code == 201
    return response.json()


def send(client, device, records=None):
    return client.post(
        "/api/ingest",
        headers={"Authorization": "Bearer " + device["token"]},
        json={"events": records or [event()]},
    )


def test_password_hashing_and_authentication_secrets(tmp_path):
    encoded = password_hash(PASSWORD)
    assert PASSWORD not in encoded
    assert password_matches(PASSWORD, encoded)
    assert not password_matches("wrong", encoded)
    assert password_hash(PASSWORD) != encoded
    with pytest.raises(ValueError):
        password_hash("short")
    auth = AuthDB(tmp_path / "auth.db")
    user = auth.create_user("alice", PASSWORD)
    token, csrf, _ = auth.login("alice", PASSWORD, "client")
    device, secret = auth.create_device(user["id"], "machine")
    auth.db.commit()
    assert auth.db.execute("SELECT token_hash FROM browser_sessions").fetchone()[0] == digest(token)
    assert auth.db.execute("SELECT token_hash FROM devices").fetchone()[0] == digest(secret)
    assert secret not in str([tuple(row) for row in auth.db.execute("SELECT * FROM devices")])
    assert auth.session(token)["user_id"] == user["id"]
    assert auth.session(csrf) is None
    auth.close()


def test_login_cookie_csrf_origin_and_role_boundaries(central):
    app, client, alice, bob, root = central
    assert client.get("/api/snapshot").status_code == 401
    assert client.get("/api/config").json() == {"mode": "server"}
    assert (
        client.post(
            "/api/auth/login",
            headers={"Origin": "https://evil.example"},
            json={"username": "alice", "password": PASSWORD},
        ).status_code
        == 403
    )
    assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    response = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    headers = login(client, "bob")
    assert (
        client.post(
            "/api/devices", headers={"Cookie": headers["Cookie"]}, json={"name": "machine"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/devices", headers={**headers, "Origin": "https://evil.example"}, json={"name": "machine"}
        ).status_code
        == 403
    )
    assert client.get("/api/admin/overview", headers=headers).status_code == 403
    assert client.post("/api/devices", headers=headers, json={"name": "   "}).status_code == 422
    assert client.get("/api/auth/me", headers=headers).json()["user"]["id"] == bob["id"]
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/snapshot", headers=headers).status_code == 401


def test_two_users_identical_ids_isolate_every_read_delete_analyze_and_export(central):
    app, client, alice, bob, root = central
    a = login(client, "alice")
    b = login(client, "bob")
    da = enroll(client, a)
    db = enroll(client, b)
    assert send(client, da, [event(device_id=db["device"]["id"], user_id=bob["id"])]).status_code == 200
    assert send(client, db, [event(input_tokens=7)]).status_code == 200
    sa = client.get("/api/snapshot", headers=a).json()
    sb = client.get("/api/snapshot", headers=b).json()
    assert sa["sessions"][0]["total_tokens"] == 120
    assert sb["sessions"][0]["total_tokens"] == 27
    assert sa["sessions"][0]["session_id"] != sb["sessions"][0]["session_id"]
    assert sa["sessions"][0]["device_id"] == da["device"]["id"]
    assert sa["sessions"][0]["source_session_id"] == "same-session"
    sid = sa["sessions"][0]["session_id"]
    assert client.get("/api/events", headers=b, params={"session_id": sid}).json() == []
    assert client.delete("/api/sessions/" + sid, headers=b).json() == {"deleted": 0}
    assert client.get("/api/analytics", headers=b).json()["summary"]["total_tokens"] == 27
    assert client.get("/api/export", headers=b).json()["events"][0]["device_id"] == db["device"]["id"]
    assert client.delete("/api/sessions/" + sid, headers=a).json()["deleted"] == 1
    assert send(client, da).status_code == 200
    assert client.get("/api/export", headers=a).json()["events"] == []  # tenant tombstones survive replay
    assert client.get("/api/export", headers=b).json()["events"]
    overview = client.get("/api/admin/overview", headers=a).json()
    assert overview["totals"]["total_tokens"] == 27
    assert "session_id" not in json.dumps(overview) and "model" not in json.dumps(overview)
    assert all("token" not in device for device in client.get("/api/devices", headers=a).json())


def test_two_devices_same_user_namespace_and_revocation(central):
    app, client, alice, bob, root = central
    a = login(client, "alice")
    b = login(client, "bob")
    first = enroll(client, a, "laptop")
    second = enroll(client, a, "desktop")
    assert send(client, first).json()["acknowledged"] == [{"event_id": "same-event", "revision": 0}]
    assert send(client, second).status_code == 200
    assert send(client, first).status_code == 200
    snapshot = client.get("/api/snapshot", headers=a).json()
    assert len(snapshot["sessions"]) == 2
    assert sum(s["total_tokens"] for s in snapshot["sessions"]) == 240
    assert (
        client.get(
            "/api/snapshot", headers={"Authorization": "Bearer " + first["token"], "Cookie": ""}
        ).status_code
        == 401
    )
    assert client.post("/api/ingest", headers=a, json={"events": [event()]}).status_code == 401
    path = "/api/devices/" + first["device"]["id"]
    assert client.delete(path, headers=b).status_code == 404
    assert client.delete(path, headers=a).status_code == 200
    assert send(client, first).status_code == 401
    assert send(client, second).status_code == 200


def test_websocket_tenant_delivery_and_logout_invalidation(central):
    import time

    app, client, alice, bob, root = central
    a = login(client, "alice")
    b = login(client, "bob")
    da = enroll(client, a)
    db = enroll(client, b)
    with (
        client.websocket_connect("/api/ws", headers=a) as wa,
        client.websocket_connect("/api/ws", headers=b) as wb,
    ):
        assert wa.receive_json()["sessions"] == []
        assert wb.receive_json()["sessions"] == []
        assert send(client, da).status_code == 200
        assert wa.receive_json()["sessions"][0]["total_tokens"] == 120
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(wb.receive_json)
            time.sleep(0.05)
            assert not pending.done()  # Alice's ingestion did not wake Bob's stream.
            assert send(client, db, [event(input_tokens=1)]).status_code == 200
            assert pending.result(timeout=2)["sessions"][0]["total_tokens"] == 21
        assert client.post("/api/auth/logout", headers=a).status_code == 200
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            wa.receive_json()


def test_account_disable_password_reset_and_throttle(central):
    app, client, alice, bob, root = central
    b = login(client, "bob")
    db = enroll(client, b)
    app.state.auth.set_enabled("bob", False)
    assert client.get("/api/snapshot", headers=b).status_code == 401
    assert send(client, db).status_code == 401
    app.state.auth.set_enabled("bob", True)
    b = login(client, "bob")
    db = enroll(client, b)
    app.state.auth.set_password("bob", "replacement-password-two!")
    assert client.get("/api/snapshot", headers=b).status_code == 401
    assert send(client, db).status_code == 401
    statuses = [
        client.post("/api/auth/login", json={"username": "unknown", "password": "bad"}).status_code
        for _ in range(11)
    ]
    assert statuses[-1] == 429


def test_restart_persists_identity_devices_replay_and_retention(tmp_path):
    app = create_server_app(tmp_path, ORIGIN)
    app.state.auth.create_user("alice", PASSWORD)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        headers = login(client, "alice")
        device = enroll(client, headers)
        assert send(client, device).status_code == 200
    app = create_server_app(tmp_path, ORIGIN)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        assert send(client, device).status_code == 200
        headers = login(client, "alice")
        assert client.get("/api/snapshot", headers=headers).json()["analytics"]["summary"]["requests"] == 1
        old = event(event_id="old", timestamp=(datetime.now(timezone.utc) - timedelta(days=100)).isoformat())
        send(client, device, [old])
        app.state.tenants.close()
        app.state.tenants.stores.clear()  # unopened persisted partition
        app.state.sweep()
        assert client.get("/api/export", headers=headers).json()["events"][0]["request_id"] == "same-request"
        assert len(client.get("/api/export", headers=headers).json()["events"]) == 1
        assert send(client, device, [old]).status_code == 200
        assert len(client.get("/api/export", headers=headers).json()["events"]) == 1


def test_server_never_instantiates_native_scanner(tmp_path, monkeypatch):
    import backend.adapters

    def forbidden(*args, **kwargs):
        raise AssertionError("Server must never scan home")

    monkeypatch.setattr(backend.adapters, "LocalAdapters", forbidden)
    monkeypatch.setenv("TOKENSCOPE_MODE", "server")
    monkeypatch.setenv("TOKENSCOPE_PUBLIC_ORIGIN", ORIGIN)
    from backend.api.app import create_app

    with TestClient(create_app(tmp_path), base_url=ORIGIN) as client:
        assert client.get("/health").json()["mode"] == "server"


def test_server_and_collector_url_validation(monkeypatch):
    for url in [
        "http://remote.example",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com?secret=x",
        "https://localhost:bad",
    ]:
        with pytest.raises(ValueError):
            server_origin(url)
        with pytest.raises(ValueError):
            collector_origin(url)
    assert collector_origin("https://example.com/") == "https://example.com"
    assert collector_origin("http://localhost:8000", True) == "http://localhost:8000"
    monkeypatch.setenv("TOKENSCOPE_ALLOW_INSECURE_HTTP", "1")
    assert server_origin("http://127.0.0.1:8000")[0] == "http://127.0.0.1:8000"


def test_received_body_limit_without_content_length():
    called = []
    sent = []

    async def inner(scope, receive, send):
        called.append(True)

    messages = iter(
        [
            {"type": "http.request", "body": b"a" * 6, "more_body": True},
            {"type": "http.request", "body": b"b" * 6, "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    asyncio.run(BodyLimitMiddleware(inner, maximum=10)({"type": "http"}, receive, send))
    assert not called and sent[0]["status"] == 413


class Scanner:
    def __init__(self, records):
        self.records = records

    def poll(self):
        return self.records


class FakeResponse(BytesIO):
    pass


class FakeOpener:
    def __init__(self, error=None):
        self.requests = []
        self.error = error

    def open(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        if request.full_url.endswith("/api/collector-heartbeat"):
            return FakeResponse(b'{"accepted":true}')
        records = json.loads(request.data)["events"]
        return FakeResponse(
            json.dumps(
                {"acknowledged": [{"event_id": e["event_id"], "revision": e["revision"]} for e in records]}
            ).encode()
        )


def test_collector_durable_spool_replay_batches_and_malformed_records(tmp_path, capsys):
    path = tmp_path / "spool.db"
    records = [event(event_id=f"e{i}") for i in range(205)] + [event(event_id="bad", timestamp="invalid")]
    spool = Spool(path)
    collector = Collector(ORIGIN, "tsd_" + "a" * 32, spool, Scanner(records))
    opener = FakeOpener()
    collector.opener = opener
    assert collector.run(once=True)
    assert len([r for r in opener.requests if r.full_url.endswith("/api/ingest")]) == 3 and spool.pending() == 0
    assert "1 invalid metadata" in capsys.readouterr().out
    assert "invalid" not in str(list(spool.db.execute("SELECT payload FROM deliveries")))
    spool.close()
    spool = Spool(path)
    collector = Collector(ORIGIN, "tsd_" + "a" * 32, spool, Scanner(records[:-1]))
    collector.opener = opener
    assert collector.run(once=True)
    assert len([r for r in opener.requests if r.full_url.endswith("/api/ingest")]) == 3  # restart replay uses delivered receipts
    assert all(row[0] is None for row in spool.db.execute("SELECT payload FROM deliveries"))
    spool.close()


def test_collector_auth_failure_stops_with_spool_and_redirects_not_followed(tmp_path, capsys):
    from backend.core.transport import NoRedirect

    spool = Spool(tmp_path / "spool.db")
    collector = Collector(ORIGIN, "tsd_" + "a" * 32, spool, Scanner([event()]))
    collector.opener = FakeOpener(HTTPError(ORIGIN, 401, "DO NOT PRINT SECRET", {}, None))
    with pytest.raises(CollectorAuthorizationError):
        collector.run(once=True)
    assert spool.pending() == 1
    assert "SECRET" not in capsys.readouterr().out
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example") is None
    collector.opener = FakeOpener(HTTPError(ORIGIN, 503, "secret response", {}, None))
    assert collector.run(once=True) is False
    assert spool.pending() == 1
    assert "secret response" not in capsys.readouterr().out
    spool.close()


def test_device_token_file_privacy(tmp_path):
    target = tmp_path / "device-token"
    target.write_text("tsd_" + "a" * 32)
    target.chmod(0o600)
    assert read_device_token(target) == "tsd_" + "a" * 32
    if os.name == "posix":
        target.chmod(0o644)
        with pytest.raises(ValueError):
            read_device_token(target)
    target.chmod(0o600)
    target.write_text("browser-or-provider-key")
    with pytest.raises(ValueError):
        read_device_token(target)


def test_session_expiry_and_revocation_persist(tmp_path):
    import time

    path = tmp_path / "accounts.db"
    auth = AuthDB(path)
    user = auth.create_user("alice", PASSWORD)
    cookie, csrf, _ = auth.login("alice", PASSWORD, "client")
    device, token = auth.create_device(user["id"], "test")
    auth.revoke(user["id"], device["id"])
    with auth.db:
        auth.db.execute("UPDATE browser_sessions SET expires_at=?", (time.time() - 1,))
    auth.close()
    auth = AuthDB(path)
    assert auth.device(token) is None
    assert auth.session(cookie) is None
    auth.close()


def test_batch_validation_precedes_any_ingestion(central):
    app, client, alice, bob, root = central
    headers = login(client, "alice")
    device = enroll(client, headers)
    assert (
        send(
            client, device, [event(event_id="valid"), event(event_id="bad", accounting="cumulative")]
        ).status_code
        == 422
    )
    assert client.get("/api/export", headers=headers).json()["events"] == []
    assert send(client, device, [event(event_id=str(i)) for i in range(101)]).status_code == 422
    assert client.get("/api/export", headers=headers).json()["events"] == []
