"""Authenticated collector heartbeats contain diagnostics, never filesystem paths."""
import json
import time
from typing import Literal
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict


class SourceHealth(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tool: str = Field(max_length=100)
    available: bool
    readable: bool = True
    malformed_records: int = Field(default=0, ge=0)


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    protocol_version: Literal[1] = 1
    collector_version: str = Field(max_length=50)
    pending_events: int = Field(ge=0)
    invalid_records: int = Field(ge=0)
    last_successful_sync: float | None = Field(default=None, ge=0)
    sources: list[SourceHealth] = Field(default_factory=list, max_length=20)


def install_health(app, auth, session):
    with auth.lock, auth.db:
        auth.db.execute('CREATE TABLE IF NOT EXISTS collector_health (device_id TEXT PRIMARY KEY, received_at REAL NOT NULL, payload TEXT NOT NULL)')

    @app.post('/api/collector-heartbeat')
    def heartbeat(body: Heartbeat, request: Request):
        header = request.headers.get('authorization', '')
        device = auth.device(header[7:] if header.startswith('Bearer ') else '')
        if not device:
            raise HTTPException(401, 'Active device credential required')
        now = time.time()
        if body.last_successful_sync is not None and body.last_successful_sync > now + 300:
            raise HTTPException(422, 'Collector clock is ahead of server')
        with auth.lock, auth.db:
            auth.db.execute('INSERT OR REPLACE INTO collector_health VALUES(?,?,?)',
                            (device['id'], now, body.model_dump_json()))
        return {'accepted': True}

    @app.get('/api/collector-health')
    def health(data=Depends(session)):
        now = time.time()
        result = []
        with auth.lock:
            for device in auth.devices(data['user_id']):
                row = auth.db.execute('SELECT received_at,payload FROM collector_health WHERE device_id=?', (device['id'],)).fetchone()
                payload = json.loads(row['payload']) if row else {}
                received = row['received_at'] if row else None
                status = 'unknown'
                if row:
                    status = 'stale' if now - received > 90 else 'healthy'
                    if status == 'healthy' and (payload.get('pending_events', 0) or payload.get('invalid_records', 0) or any(not s['available'] or not s.get('readable', True) or s.get('malformed_records', 0) for s in payload.get('sources', []))):
                        status = 'degraded'
                if device['revoked_at'] is not None:
                    status = 'revoked'
                result.append({**device, **payload, 'received_at': received, 'status': status})
        return {'devices': result}
