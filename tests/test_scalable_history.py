import json
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.analytics.report import summarize
from backend.api.app import create_app
from backend.core.schema import UsageEvent
from backend.storage.history import HistoryFilters
from backend.storage.scalable import cursor_page, stream_export
from backend.storage.store import Store

STAMP = datetime(2026, 11, 15, tzinfo=timezone.utc)


def event(i, **changes):
    data = dict(event_id=f'e{i:05}', session_id='session', provider='openai', tool='codex',
                timestamp=STAMP + timedelta(seconds=i // 3), model='model', input_tokens=100,
                output_tokens=10, cache_read_tokens=20, reasoning_tokens=5)
    data.update(changes)
    return UsageEvent(**data)


def test_cursor_all_rows_with_ties_and_backdated_arrivals(tmp_path, monkeypatch):
    store = Store(tmp_path / 'db')
    for i in range(1051):
        store.ingest(event(i))
    monkeypatch.setattr(store, 'filtered_events', lambda *a: pytest.fail('materialized history'))
    first = cursor_page(store, HistoryFilters(), page_size=200)
    ids = [e['event_id'] for e in first['items']]
    # New arrivals, even backdated, must not shift or enter this browsing window.
    store.ingest(event(2000, timestamp=STAMP))
    cursor = first['next_cursor']
    while cursor:
        page = cursor_page(store, HistoryFilters(), cursor, 200)
        assert page['total_items'] == 1051
        ids += [e['event_id'] for e in page['items']]
        cursor = page['next_cursor']
    assert len(ids) == len(set(ids)) == 1051
    assert cursor_page(store, HistoryFilters())['total_items'] == 1052
    store.close()


def test_sql_summary_matches_accounting_reference(tmp_path):
    store = Store(tmp_path / 'db')
    store.ingest(event(1))
    store.ingest(event(2, input_includes_cache=False, output_includes_reasoning=False, billed_cost_usd=0.2))
    store.ingest(event(3, input_tokens=1, output_tokens=1))
    store.ingest(event(4, kind='completed'))
    rows = store.events()
    expected = summarize([e for e in rows if e['kind'] == 'usage'])
    assert cursor_page(store, HistoryFilters())['totals'] == expected
    assert cursor_page(store, HistoryFilters(model='absent'))['totals'] == summarize([])
    store.close()


def test_cursor_validation_filters_and_delete_rowid_reuse(tmp_path):
    store = Store(tmp_path / 'db')
    for i in range(4):
        store.ingest(event(i))
    page = cursor_page(store, HistoryFilters(), page_size=1)
    store.delete(session_id='session')
    store.ingest(event(10, timestamp=STAMP))
    assert cursor_page(store, HistoryFilters(), page['next_cursor'])['items'] == []
    with pytest.raises(ValueError):
        cursor_page(store, HistoryFilters(model='different'), page['next_cursor'])
    with pytest.raises(ValueError):
        cursor_page(store, HistoryFilters(), 'garbage')
    store.close()


def test_stream_snapshot_bounded_chunks_and_close(tmp_path, monkeypatch):
    store = Store(tmp_path / 'db')
    for i in range(1051):
        store.ingest(event(i))
    monkeypatch.setattr(store, 'filtered_events', lambda *a: pytest.fail('materialized export'))
    stream = stream_export(store, HistoryFilters())
    prefix = next(stream)
    store.ingest(event(2000))
    chunks = list(stream)
    result = json.loads(prefix + ''.join(chunks))
    assert len(result['events']) == 1051
    assert max(map(len, chunks)) < 5000
    # Cancelling a consumer releases its read transaction.
    stream = stream_export(store, HistoryFilters())
    next(stream)
    stream.close()
    store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    store.close()


class Empty:
    def poll(self):
        return []


def test_cursor_export_auth_and_dates(tmp_path):
    app = create_app(tmp_path, scanner=Empty(), token='secret')
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer secret'}
        assert client.get('/api/history/cursor').status_code == 401
        assert client.get('/api/history/export').status_code == 401
        for i in range(5):
            assert client.post('/api/events', headers=headers, json=event(i).model_dump(mode='json')).status_code == 200
        params = {'start': STAMP.isoformat(), 'end': (STAMP+timedelta(seconds=1)).isoformat()}
        page = client.get('/api/history/cursor', params=params, headers=headers).json()
        exported = client.get('/api/history/export', params=params, headers=headers)
        assert page['total_items'] == len(exported.json()['events']) == 3
        assert 'attachment' in exported.headers['content-disposition']
        assert client.get('/api/history/cursor?cursor=garbage', headers=headers).status_code == 422


def test_server_cursor_and_stream_remain_tenant_scoped(tmp_path):
    from backend.server.app import create_server_app
    origin = 'https://testserver'
    app = create_server_app(tmp_path, origin)
    alice = app.state.auth.create_user('alice', 'test-password-one!')
    bob = app.state.auth.create_user('bob', 'test-password-two!')
    app.state.tenants.ledger(alice['id']).ingest(event(1))
    app.state.tenants.ledger(bob['id']).ingest(event(2, model='private-bob'))
    with TestClient(app, base_url=origin, headers={'Origin': origin}) as client:
        assert client.post('/api/auth/login', json={'username':'alice','password':'test-password-one!'}).status_code == 200
        page = client.get('/api/history/cursor').json()
        exported = client.get('/api/history/export').json()
        assert page['total_items'] == 1
        assert exported['events'][0]['model'] == 'model'
        assert client.get('/api/history/cursor?model=private-bob').json()['total_items'] == 0
        assert client.get('/api/history/requests/e00002').status_code == 404

def test_cursor_rejects_extra_position_fields(tmp_path):
    import base64
    import json
    from backend.storage.scalable import _fingerprint
    store = Store(tmp_path/'bad-cursor.db')
    filters = HistoryFilters()
    value = dict(filter=_fingerprint(filters), watermark=0, position=[0,'id','extra'])
    cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
    with pytest.raises(ValueError):
        cursor_page(store, filters, cursor)
    store.close()
