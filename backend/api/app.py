import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from backend.core.config import api_token, data_directory
from backend.core.schema import UsageEvent
from backend.pricing.engine import Pricing
from backend.storage.store import Store
from backend.api.history import history_filters, install_history

log = logging.getLogger("tokenscope")


def create_app(data_dir: Path | None = None, scanner=None, token: str | None = None):
    root = data_dir or data_directory()
    if os.getenv("TOKENSCOPE_MODE", "standalone") == "server":
        from backend.server.app import create_server_app

        return create_server_app(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret = token or api_token(root)
    store = Store(root / "usage.db", Pricing(root / "pricing.json"))
    (root / "usage.db").chmod(0o600)
    clients = set()
    version = 0

    async def broadcast():
        nonlocal version
        version += 1
        # Slow clients only retain a wake-up marker; reconnect always fetches a fresh snapshot.
        for queue in tuple(clients):
            if queue.empty():
                queue.put_nowait(version)

    async def collector():
        from backend.adapters import LocalAdapters

        adapter = scanner or LocalAdapters()
        ticks = 0
        while True:
            try:
                changed = False
                records = await asyncio.to_thread(adapter.poll)
                for raw in records:
                    try:
                        changed = store.ingest(UsageEvent.model_validate(raw)) or changed
                    except (ValueError, TypeError):
                        log.warning("Skipped invalid usage metadata; run tokenscope doctor")
                ticks += 1
                if ticks % 240 == 0:
                    store.retain(int(os.getenv("TOKENSCOPE_RETENTION_DAYS", "90")))
                if changed or ticks % 60 == 0:
                    await broadcast()
            except Exception:
                # Deliberately do not log source payloads, paths, or credential-bearing exception text.
                log.warning("Local telemetry scan failed; retrying automatically")
            await asyncio.sleep(0.25)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(collector())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        store.close()

    app = FastAPI(title="TokenScope", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    def authorized(request: Request):
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth, "Bearer " + secret):
            raise HTTPException(401, "Bearer token required")
        origin = request.headers.get("origin")
        if origin and origin not in (
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ):
            raise HTTPException(403, "Origin rejected")

    @app.middleware("http")
    async def guard(request, call_next):
        try:
            size = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return __import__("fastapi").responses.JSONResponse(
                {"detail": "Invalid content length"}, status_code=400
            )
        if size > 1_048_576:
            return __import__("fastapi").responses.JSONResponse(
                {"detail": "Payload too large"}, status_code=413
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/config")
    def config():
        return {"mode": "standalone"}

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/snapshot", dependencies=[Depends(authorized)])
    def snapshot():
        return store.snapshot()

    @app.get("/api/analytics", dependencies=[Depends(authorized)])
    def analytics():
        from backend.analytics.report import build_report

        return build_report(store.events())

    @app.get("/api/events", dependencies=[Depends(authorized)])
    def events(session_id: str | None = None):
        return store.events(session_id)

    @app.get("/api/export", dependencies=[Depends(authorized)])
    def export(filters=Depends(history_filters)):
        return {"schema_version": 1, "events": store.filtered_events(filters), "filters": filters.public()}

    def scoped_store(_=Depends(authorized)):
        return store

    install_history(app, scoped_store)

    @app.post("/api/events", dependencies=[Depends(authorized)])
    async def ingest(event: UsageEvent):
        try:
            accepted = store.ingest(event)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if accepted:
            await broadcast()
        return {"accepted": accepted}

    @app.delete("/api/sessions/{session_id}", dependencies=[Depends(authorized)])
    async def delete_session(session_id: str):
        deleted = store.delete(session_id=session_id)
        await broadcast()
        return {"deleted": deleted}

    @app.get("/api/collector-health", dependencies=[Depends(authorized)])
    def collector_health():
        return {"devices": []}

    @app.get("/api/providers", dependencies=[Depends(authorized)])
    def providers():
        from backend.adapters import LocalAdapters

        return (scanner or LocalAdapters()).doctor()

    @app.websocket("/api/ws")
    async def websocket(ws: WebSocket):
        origin = ws.headers.get("origin")
        if origin and origin not in (
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ):
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=1)
        try:
            credentials = await asyncio.wait_for(ws.receive_json(), timeout=5)
            if not isinstance(credentials, dict) or not hmac.compare_digest(
                str(credentials.get("token", "")), secret
            ):
                await ws.close(code=1008)
                return
            clients.add(queue)
            while True:
                await ws.send_json(store.snapshot())
                try:
                    await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError, ValueError):
            pass
        finally:
            clients.discard(queue)

    static = Path(__file__).resolve().parents[1] / "static"
    if static.exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(static / "index.html")
    else:

        @app.get("/")
        def index():
            return {
                "application": "TokenScope",
                "dashboard": "Run npm install && npm run build, then restart.",
                "api": "/docs",
            }

    return app
