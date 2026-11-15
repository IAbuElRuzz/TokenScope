from datetime import datetime, timezone
from fastapi.testclient import TestClient
from backend.server.app import create_server_app
from backend.core.schema import UsageEvent
from backend.storage.store import Store
from backend.alerts.budgets import evaluate, initialize
import json


def test_budgets_scope_period_missing_cost_and_revisions(tmp_path):
    store = Store(tmp_path/'usage.db')
    initialize(store)
    now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
    for i, stamp, project, billed in [('a','2026-11-15T10:00:00Z','work',4),('b','2026-11-14T10:00:00Z','work',9),('c','2026-11-15T11:00:00Z','other',8),('d','2026-11-15T11:00:00Z','work',None)]:
        store.ingest(UsageEvent(event_id=i, session_id=i, provider='test',tool='test',model='test',timestamp=stamp,project=project,billed_cost_usd=billed))
    budget=dict(name='Work',scope='project',value='work',period='daily',basis='billed',limit_usd=4)
    with store.db:
        store.db.execute('INSERT INTO budgets VALUES(?,?)',('test',json.dumps(budget)))
    row=evaluate(store,now)[0]
    assert row['spent_usd']==4 and row['unpriced_requests']==1 and row['exceeded']
    store.ingest(UsageEvent(event_id='a',revision=1,session_id='a',provider='test',tool='test',model='test',timestamp='2026-11-15T10:00:00Z',project='work',billed_cost_usd=2))
    assert evaluate(store,now)[0]['spent_usd']==2
    assert not evaluate(store,now)[0]['exceeded']
    store.close()


def test_budget_api_csrf_and_tenant_isolation(tmp_path):
    app=create_server_app(tmp_path,'https://testserver')
    auth=app.state.auth
    auth.create_user('alice','very-long-password')
    auth.create_user('bobby','very-long-password')
    a,csrf,_=auth.login('alice','very-long-password','a')
    b,bc,_=auth.login('bobby','very-long-password','b')
    with TestClient(app,base_url='https://testserver') as client:
        headers={'Cookie':'tokenscope_session='+a,'Origin':'https://testserver','X-CSRF-Token':csrf}
        body=dict(name='Daily',period='daily',scope='account',value='',limit_usd=10,basis='estimated')
        assert client.post('/api/budgets',json=body).status_code==401
        result=client.post('/api/budgets',headers=headers,json=body)
        assert result.status_code==201
        identifier=result.json()['id']
        assert client.post('/api/budgets',headers={**headers,'X-CSRF-Token':'wrong'},json=body).status_code==403
        other={'Cookie':'tokenscope_session='+b,'Origin':'https://testserver','X-CSRF-Token':bc}
        assert client.get('/api/budgets',headers=other).json()=={'budgets':[]}
        assert client.delete('/api/budgets/'+identifier,headers=other).status_code==404
        assert client.delete('/api/budgets/'+identifier,headers=headers).status_code==200

def test_monthly_budget_utc_window_and_unknown_price(tmp_path):
    store=Store(tmp_path/'usage.db')
    initialize(store)
    now=datetime(2026,11,15,12,tzinfo=timezone.utc)
    for identifier,stamp,billed in [('old','2026-10-31T23:59:59Z',99),('start','2026-11-01T00:00:00Z',3),('future','2026-11-16T00:00:00Z',99)]:
        store.ingest(UsageEvent(event_id=identifier,session_id='s',provider='unknown',tool='test',model='m',timestamp=stamp,billed_cost_usd=billed))
    with store.db:
        for basis in ('estimated','billed'):
            value=dict(name=basis,scope='model',value='m',period='monthly',basis=basis,limit_usd=3)
            store.db.execute('INSERT INTO budgets VALUES(?,?)',(basis,json.dumps(value)))
    rows={row['basis']:row for row in evaluate(store,now)}
    assert rows['billed']['spent_usd']==3 and rows['billed']['exceeded']
    assert rows['estimated']['spent_usd'] is None
    assert rows['estimated']['unpriced_requests']==1 and not rows['estimated']['exceeded']
    store.close()
