"""Account-local budgets, evaluated from canonical retained requests in UTC periods."""
import json
import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict, model_validator
from backend.storage.history import epoch_us


class Budget(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=80)
    period: Literal['daily', 'monthly']
    scope: Literal['account', 'project', 'model'] = 'account'
    value: str = Field(default='', max_length=256)
    limit_usd: float = Field(gt=0, le=1e12)
    basis: Literal['estimated', 'billed'] = 'estimated'

    @model_validator(mode='after')
    def validate_scope(self):
        if self.scope != 'account' and not self.value.strip():
            raise ValueError('Project/model budgets require an exact scope value')
        if self.scope == 'account' and self.value:
            raise ValueError('Account budgets have no scope value')
        return self


def initialize(store):
    with store.lock, store.db:
        store.db.execute('CREATE TABLE IF NOT EXISTS budgets (id TEXT PRIMARY KEY,payload TEXT NOT NULL)')


def evaluate(store, now=None):
    initialize(store)
    now = now or datetime.now(timezone.utc)
    result = []
    with store.lock:
        for identifier, payload in store.db.execute('SELECT id,payload FROM budgets ORDER BY id').fetchall():
            budget = json.loads(payload)
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if budget['period'] == 'monthly':
                start = start.replace(day=1)
            field = 'cost_usd' if budget['basis'] == 'estimated' else 'billed_cost_usd'
            cost = f"json_extract(payload,'$.{field}')"
            where = "occurred_us>=? AND occurred_us<=? AND json_extract(payload,'$.kind')='usage'"
            args = [epoch_us(start), epoch_us(now)]
            if budget['scope'] != 'account':
                where += f" AND json_extract(payload,'$.{budget['scope']}')=?"
                args.append(budget['value'])
            spent, count, priced = store.db.execute(
                f'SELECT SUM({cost}),COUNT(*),COUNT({cost}) FROM events WHERE '+where, args).fetchone()
            result.append(dict(id=identifier, **budget, spent_usd=spent, unpriced_requests=count-priced,
                               period_start=start.isoformat(), exceeded=spent is not None and spent >= budget['limit_usd']))
    return result


def install_budgets(app, scoped_store, mutation_store):
    @app.get('/api/budgets')
    def budgets(store=Depends(scoped_store)):
        return {'budgets': evaluate(store)}

    @app.post('/api/budgets', status_code=201)
    def create(body: Budget, store=Depends(mutation_store)):
        initialize(store)
        identifier = uuid.uuid4().hex
        with store.lock, store.db:
            if store.db.execute('SELECT COUNT(*) FROM budgets').fetchone()[0] >= 100:
                raise HTTPException(409, 'Maximum 100 budgets per account')
            store.db.execute('INSERT INTO budgets VALUES(?,?)', (identifier, body.model_dump_json()))
        return {'id': identifier}

    @app.delete('/api/budgets/{identifier}')
    def delete(identifier: str, store=Depends(mutation_store)):
        initialize(store)
        with store.lock, store.db:
            count = store.db.execute('DELETE FROM budgets WHERE id=?', (identifier,)).rowcount
        if not count:
            raise HTTPException(404, 'Budget not found')
        return {'deleted': True}
