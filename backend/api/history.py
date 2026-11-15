"""History endpoints receive a scoped store dependency; never a caller tenant ID."""

from datetime import datetime
from fastapi import Depends, HTTPException, Query
from backend.storage.history import HistoryFilters
from backend.storage.scalable import cursor_page, stream_export
from fastapi.responses import StreamingResponse


def history_filters(
    start: datetime | None = None,
    end: datetime | None = None,
    provider: str | None = Query(None, max_length=256),
    model: str | None = Query(None, max_length=256),
    tool: str | None = Query(None, max_length=256),
    session_id: str | None = Query(None, max_length=256),
    project: str | None = Query(None, max_length=256),
    device_id: str | None = Query(None, max_length=256),
    request_id: str | None = Query(None, max_length=256),
):
    try:
        return HistoryFilters(start, end, provider, model, tool, session_id, project, device_id, request_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


def install_history(app, scoped_store):
    @app.get("/api/history/cursor")
    def cursor_history(filters=Depends(history_filters), cursor: str | None = Query(None, max_length=2048),
                       page_size: int = Query(50, ge=1, le=200), store=Depends(scoped_store)):
        try:
            return cursor_page(store, filters, cursor, page_size)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/api/history/export")
    def streaming_export(filters=Depends(history_filters), store=Depends(scoped_store)):
        return StreamingResponse(stream_export(store, filters), media_type="application/json",
                                 headers={"Content-Disposition": 'attachment; filename="tokenscope-history.json"'})

    @app.get("/api/history/analysis")
    def full_analysis(filters=Depends(history_filters), store=Depends(scoped_store)):
        return store.history(filters, 1, 1)["analytics"]

    @app.get("/api/history")
    def history(
        filters=Depends(history_filters),
        page: int = Query(1, ge=1, le=1_000_000_000),
        page_size: int = Query(50, ge=1, le=200),
        store=Depends(scoped_store),
    ):
        return store.history(filters, page, page_size)

    @app.get("/api/history/requests/{event_id}")
    def detail(event_id: str, store=Depends(scoped_store)):
        result = store.request_detail(event_id)
        if result is None:
            raise HTTPException(404, "Usage event not found")
        return result
