"""History endpoints receive a scoped store dependency; never a caller tenant ID."""

from datetime import datetime
from fastapi import Depends, HTTPException, Query
from backend.storage.history import HistoryFilters


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
