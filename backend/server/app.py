"""Explicit opt-in central server. Never scans the server host's tool directories."""

import asyncio
import hashlib
import hmac
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.analytics.report import build_report
from backend.core.schema import UsageEvent
from backend.pricing.engine import Pricing
from backend.server.auth import AuthDB, COOKIE, SESSION_SECONDS, csrf_value, digest
from backend.server.limits import BodyLimitMiddleware
from backend.storage.store import Store
from backend.api.history import history_filters, install_history


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class DeviceName(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")


class Batch(BaseModel):
    events: list[UsageEvent] = Field(min_length=1, max_length=100)


class Tenants:
    """Physical SQLite partitions keep legacy event IDs and deletion scopes isolated."""

    def __init__(self, root):
        self.root = root
        self.stores = {}
        import threading

        self.lock = threading.RLock()

    def ledger(self, user_id):
        if not re.fullmatch(r"u_[a-f0-9]{32}", user_id):
            raise ValueError("Invalid internal account identity")
        with self.lock:
            if user_id not in self.stores:
                folder = self.root / "accounts" / user_id
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.stores[user_id] = Store(folder / "usage.db", Pricing(self.root / "pricing.json"))
                (folder / "usage.db").chmod(0o600)
            return self.stores[user_id]

    def close(self):
        for store in self.stores.values():
            store.close()


def server_origin(value):
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Invalid public origin port") from error
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("TOKENSCOPE_PUBLIC_ORIGIN must be an explicit HTTPS origin")
    if parsed.scheme != "https" and not (
        parsed.hostname in ("localhost", "127.0.0.1", "::1")
        and os.getenv("TOKENSCOPE_ALLOW_INSECURE_HTTP") == "1"
    ):
        raise ValueError(
            "Server requires HTTPS; insecure HTTP is only permitted for explicit loopback development"
        )
    return value.rstrip("/"), parsed


def create_server_app(root: Path, public_origin: str | None = None):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    origin, parsed = server_origin(public_origin or os.environ.get("TOKENSCOPE_PUBLIC_ORIGIN", ""))
    secure = parsed.scheme == "https"
    auth = AuthDB(root / "accounts.db")
    tenants = Tenants(root)
    clients = {}

    async def notify(user_id):
        for queue in tuple(clients.get(user_id, ())):
            if queue.empty():
                queue.put_nowait(True)

    def sweep():
        for user in auth.users():
            tenants.ledger(user["id"]).retain(int(os.getenv("TOKENSCOPE_RETENTION_DAYS", "90")))

    async def retention():
        while True:
            await asyncio.sleep(60)
            # SQLite retention is bounded to this single instance; do not scan host sources.
            sweep()

    @asynccontextmanager
    async def lifespan(app):
        # Deliberately no LocalAdapters import or host filesystem discovery.
        maintenance = asyncio.create_task(retention())
        yield
        maintenance.cancel()
        try:
            await maintenance
        except asyncio.CancelledError:
            pass
        tenants.close()
        auth.close()

    app = FastAPI(title="TokenScope Server", version="0.2.0", lifespan=lifespan)
    app.state.auth = auth
    app.state.tenants = tenants
    app.state.sweep = sweep
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname])
    app.add_middleware(BodyLimitMiddleware)

    @app.middleware("http")
    async def guards(request, call_next):
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Invalid content length"}, status_code=400)
        if size > 2_097_152:
            return JSONResponse({"detail": "Payload too large"}, status_code=413)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
            }
        )
        return response

    def origin_check(request):
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Same-origin browser request required")

    def session(request: Request):
        data = auth.session(request.cookies.get(COOKIE))
        if not data:
            raise HTTPException(401, "Account login required")
        return data

    def mutation(request: Request, data=Depends(session)):
        origin_check(request)
        if not hmac.compare_digest(digest(request.headers.get("x-csrf-token", "")), data["csrf_hash"]):
            raise HTTPException(403, "CSRF token required")
        return data

    def identity(data, token):
        return {
            "user": {"id": data["user_id"], "username": data["username"], "role": data["role"]},
            "csrf_token": csrf_value(token),
        }

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "server", "version": "0.2.0"}

    @app.get("/api/config")
    def config():
        return {"mode": "server"}

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response):
        origin_check(request)
        try:
            result = auth.login(
                body.username, body.password, request.client.host if request.client else "unknown"
            )
        except PermissionError:
            raise HTTPException(429, "Login temporarily rate limited") from None
        if not result:
            raise HTTPException(401, "Invalid username or password")
        token, csrf, user = result
        previous = request.cookies.get(COOKIE)
        if previous:
            auth.logout(previous)
        response.set_cookie(
            COOKIE, token, httponly=True, secure=secure, samesite="strict", max_age=SESSION_SECONDS, path="/"
        )
        return {"user": user, "csrf_token": csrf}

    @app.get("/api/auth/me")
    def me(request: Request, data=Depends(session)):
        return identity(data, request.cookies[COOKIE])

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response, data=Depends(mutation)):
        auth.logout(request.cookies[COOKIE])
        response.delete_cookie(COOKIE, httponly=True, secure=secure, samesite="strict", path="/")
        await notify(data["user_id"])
        return {"ok": True}

    from backend.server.health import install_health

    install_health(app, auth, session)

    @app.get("/api/devices")
    def devices(data=Depends(session)):
        return auth.devices(data["user_id"])

    @app.post("/api/devices", status_code=201)
    def register_device(body: DeviceName, data=Depends(mutation)):
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Device name cannot be blank")
        device, token = auth.create_device(data["user_id"], name)
        return {"device": device, "token": token}

    @app.delete("/api/devices/{device_id}")
    def revoke_device(device_id: str, data=Depends(mutation)):
        if not auth.revoke(data["user_id"], device_id):
            raise HTTPException(404, "Active device not found")
        return {"revoked": True}

    @app.post("/api/ingest")
    async def ingest(body: Batch, request: Request):
        header = request.headers.get("authorization", "")
        device = auth.device(header[7:] if header.startswith("Bearer ") else "")
        if not device:
            raise HTTPException(401, "Active device credential required")
        store = tenants.ledger(device["user_id"])
        acknowledgements = []
        changed = False
        for event in body.events:
            if event.accounting != "delta":
                raise HTTPException(422, "Collectors must normalize cumulative usage to deltas")
        for event in body.events:
            original = event.event_id
            fields = event.model_dump(mode="json")
            fields.update(
                event_id=hashlib.sha256((device["id"] + "\0" + original).encode()).hexdigest(),
                session_id=device["id"] + ":" + hashlib.sha256(event.session_id.encode()).hexdigest(),
                source_session_id=event.session_id,
                device_id=device["id"],
            )
            changed = store.ingest(UsageEvent.model_validate(fields)) or changed
            acknowledgements.append({"event_id": original, "revision": event.revision})
        if changed:
            await notify(device["user_id"])
        return {"acknowledged": acknowledgements}

    @app.get("/api/snapshot")
    def snapshot(data=Depends(session)):
        return tenants.ledger(data["user_id"]).snapshot()

    @app.get("/api/events")
    def events(session_id: str | None = None, data=Depends(session)):
        return tenants.ledger(data["user_id"]).events(session_id)

    @app.get("/api/analytics")
    def analytics(data=Depends(session)):
        return build_report(tenants.ledger(data["user_id"]).events())

    @app.get("/api/export")
    def export(data=Depends(session), filters=Depends(history_filters)):
        return {
            "schema_version": 1,
            "events": tenants.ledger(data["user_id"]).filtered_events(filters),
            "filters": filters.public(),
        }

    def scoped_store(data=Depends(session)):
        return tenants.ledger(data["user_id"])

    install_history(app, scoped_store)
    from backend.alerts.budgets import install_budgets

    def budget_mutation_store(data=Depends(mutation)):
        return tenants.ledger(data["user_id"])

    install_budgets(app, scoped_store, budget_mutation_store)

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str, data=Depends(mutation)):
        deleted = tenants.ledger(data["user_id"]).delete(session_id=session_id)
        await notify(data["user_id"])
        return {"deleted": deleted}

    @app.get("/api/providers")
    def providers(data=Depends(session)):
        return [
            {
                "tool": "registered-collectors",
                "available": True,
                "source": "outbound devices",
                "limitations": "Server host session discovery is disabled.",
            }
        ]

    @app.get("/api/admin/overview")
    def overview(data=Depends(session)):
        if data["role"] != "admin":
            raise HTTPException(403, "Administrator role required")
        rows = []
        for user in auth.users():
            events = tenants.ledger(user["id"]).events()
            usage = [event for event in events if event["kind"] == "usage"]
            devices = auth.devices(user["id"])
            priced = [e["cost_usd"] for e in usage if e.get("cost_usd") is not None]
            rows.append(
                dict(
                    user_id=user["id"],
                    username=user["username"],
                    enabled=bool(user["enabled"]),
                    devices=len(devices),
                    active_devices=sum(d["revoked_at"] is None for d in devices),
                    requests=len(usage),
                    total_tokens=sum(e["total_tokens"] for e in usage),
                    cost_usd=sum(priced) if priced else None,
                    unpriced_requests=len(usage) - len(priced),
                )
            )
        known = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
        return {
            "users": rows,
            "totals": {
                "users": len(rows),
                "requests": sum(r["requests"] for r in rows),
                "total_tokens": sum(r["total_tokens"] for r in rows),
                "cost_usd": sum(known) if known else None,
            },
        }

    @app.websocket("/api/ws")
    async def websocket(ws: WebSocket):
        if ws.headers.get("origin") != origin:
            await ws.close(code=1008)
            return
        token = ws.cookies.get(COOKIE)
        data = auth.session(token)
        if not data:
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=1)
        clients.setdefault(data["user_id"], set()).add(queue)
        try:
            while auth.session(token):
                await ws.send_json(tenants.ledger(data["user_id"]).snapshot())
                try:
                    await asyncio.wait_for(queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    pass
            await ws.close(code=1008)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            clients[data["user_id"]].discard(queue)

    static = Path(__file__).resolve().parents[1] / "static"
    if (static / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(static / "index.html")
    else:

        @app.get("/")
        def index():
            return {"application": "TokenScope Server", "dashboard": "Build frontend with npm run build"}

    return app
