from fastapi.testclient import TestClient
from backend.server.app import create_server_app


def test_health_is_scoped_revocable_and_rejects_paths(tmp_path):
    app = create_server_app(tmp_path, 'https://testserver')
    auth = app.state.auth
    a = auth.create_user('alice', 'very-long-password')
    auth.create_user('bobby', 'very-long-password')
    da, token = auth.create_device(a['id'], 'Laptop')
    ca, _, _ = auth.login('alice', 'very-long-password', 'a')
    cb, _, _ = auth.login('bobby', 'very-long-password', 'b')
    with TestClient(app, base_url='https://testserver') as client:
        body = dict(collector_version='0.1.0', pending_events=8, invalid_records=2, sources=[])
        assert client.post('/api/collector-heartbeat', json=body).status_code == 401
        headers = {'Authorization': 'Bearer '+token}
        assert client.post('/api/collector-heartbeat', headers=headers, json={**body, 'path':'secret'}).status_code == 422
        assert client.post('/api/collector-heartbeat', headers=headers, json=body).status_code == 200
        def get(cookie):
            return client.get('/api/collector-health', headers={'Cookie':'tokenscope_session='+cookie}).json()['devices']
        assert get(cb) == []
        assert get(ca)[0]['pending_events'] == 8
        assert get(ca)[0]['status'] == 'degraded'
        with auth.db:
            auth.db.execute('UPDATE collector_health SET received_at=0')
        assert get(ca)[0]['status'] == 'stale'
        auth.revoke(a['id'], da['id'])
        assert client.post('/api/collector-heartbeat', headers=headers, json=body).status_code == 401
        assert get(ca)[0]['status'] == 'revoked'



def test_collector_health_omits_paths_and_persists_sync(tmp_path):
    import json
    from io import BytesIO
    from backend.server.collector import Collector, Spool
    class Scanner:
        def doctor(self):
            return [dict(tool='codex',available=True,readable=True,source='/private/secret',malformed_records=2)]
    class Opener:
        def open(self, request, timeout):
            self.body=json.loads(request.data)
            return BytesIO(b'{"accepted":true}')
    path=tmp_path/'spool.db'
    spool=Spool(path)
    spool.save_state('last_successful_sync',123)
    spool.save_state('invalid_records',4)
    spool.close()
    spool=Spool(path)
    collector=Collector('https://testserver','tsd_test',spool,Scanner())
    collector.opener=Opener()
    collector.heartbeat(force=True)
    body=collector.opener.body
    assert body['last_successful_sync']==123 and body['invalid_records']==4
    assert '/private/secret' not in json.dumps(body)
    assert body['sources'][0]['malformed_records']==2
    spool.close()
