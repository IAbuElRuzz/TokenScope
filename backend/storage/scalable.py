"""Bounded history retrieval and SQLite aggregate queries for retained archives."""
import base64
import hashlib
import json
import sqlite3
from pathlib import Path


def _fingerprint(filters):
    return hashlib.sha256(json.dumps(filters.public(), sort_keys=True).encode()).hexdigest()


def _prepare(store):
    with store.lock, store.db:
        if getattr(store, '_history_indexes_ready', False):
            return
        columns = {r[1] for r in store.db.execute('PRAGMA table_info(events)')}
        if 'history_seq' not in columns:
            store.db.execute('ALTER TABLE events ADD COLUMN history_seq INTEGER')
        store.db.execute('UPDATE events SET history_seq=rowid WHERE history_seq IS NULL')
        store.db.execute('CREATE TABLE IF NOT EXISTS history_sequence (singleton INTEGER PRIMARY KEY CHECK(singleton=1), value INTEGER NOT NULL)')
        store.db.execute('INSERT OR IGNORE INTO history_sequence SELECT 1,COALESCE(MAX(history_seq),0) FROM events')
        store.db.executescript("""
            CREATE TRIGGER IF NOT EXISTS history_arrival AFTER INSERT ON events BEGIN
                UPDATE history_sequence SET value=value+1 WHERE singleton=1;
                UPDATE events SET history_seq=(SELECT value FROM history_sequence WHERE singleton=1) WHERE id=NEW.id;
            END;
            CREATE TRIGGER IF NOT EXISTS history_revision AFTER UPDATE OF payload ON events BEGIN
                UPDATE history_sequence SET value=value+1 WHERE singleton=1;
                UPDATE events SET history_seq=(SELECT value FROM history_sequence WHERE singleton=1) WHERE id=NEW.id;
            END;
        """)
        for field in ('provider', 'model', 'project', 'device_id', 'request_id', 'tool'):
            store.db.execute(f"CREATE INDEX IF NOT EXISTS history_{field} ON events(json_extract(payload,'$.{field}'),occurred_us DESC,id DESC)")
        store._history_indexes_ready = True


def _connection(store):
    _prepare(store)
    with store.lock:
        path = store.db.execute('PRAGMA database_list').fetchone()[2]
    if not path:
        raise ValueError('History streaming requires a persistent ledger')
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=False)
    db.execute('BEGIN')
    return db


def _where(filters, watermark, position=None):
    where, args = filters.sql()
    where += (' AND ' if where else ' WHERE ') + 'history_seq<=?'
    args.append(watermark)
    if position is not None:
        where += ' AND (occurred_us,id)<(?,?)'
        args.extend(position)
    return where, args


def sql_summary(db, where, args):
    """Match analytics.summarize accounting without materializing event payloads."""
    def field(name, fallback='0'):
        return f"COALESCE(json_extract(payload,'$.{name}'),{fallback})"
    reads, writes, reasoning = [field(k) for k in ('cache_read_tokens', 'cache_creation_tokens', 'reasoning_tokens')]
    inputs = f"({field('input_tokens')} + CASE WHEN {field('input_includes_cache','1')}=0 THEN {reads}+{writes} ELSE 0 END)"
    outputs = f"({field('output_tokens')} + CASE WHEN {field('output_includes_reasoning','1')}=0 THEN {reasoning} ELSE 0 END)"
    invalid = f'({reads}+{writes}>{inputs} OR {reasoning}>{outputs})'
    sums = {'total_tokens':field('total_tokens'), 'input_total_tokens':inputs,
            'uncached_input_tokens':f'max(0,{inputs}-{reads}-{writes})', 'cache_read_tokens':reads,
            'cache_creation_tokens':writes, 'output_total_tokens':outputs, 'reasoning_tokens':reasoning,
            'invalid_decomposition_requests':invalid,
            'provider_total_residual_tokens':f"{field('total_tokens')}-{inputs}-{outputs}"}
    expressions = ['COUNT(*) AS requests'] + [f'COALESCE(SUM({v}),0) AS {k}' for k,v in sums.items()]
    expressions += ["SUM(json_extract(payload,'$.cost_usd')) AS known_cost_usd",
                    "SUM(json_extract(payload,'$.billed_cost_usd')) AS billed_cost_usd",
                    "COUNT(json_extract(payload,'$.cost_usd')) AS priced_requests"]
    usage_where = where + (' AND ' if where else ' WHERE ') + "json_extract(payload,'$.kind')='usage'"
    cursor = db.execute('SELECT '+','.join(expressions)+' FROM events'+usage_where, args)
    result = dict(zip([c[0] for c in cursor.description], cursor.fetchone()))
    result['unpriced_requests'] = result['requests']-result['priced_requests']
    result['cache_read_ratio'] = result['cache_read_tokens']/result['input_total_tokens'] if result['input_total_tokens'] and not result['invalid_decomposition_requests'] else None
    result['reasoning_output_ratio'] = result['reasoning_tokens']/result['output_total_tokens'] if result['output_total_tokens'] and not result['invalid_decomposition_requests'] else None
    return result


def cursor_page(store, filters, cursor=None, page_size=50):
    if not 1 <= page_size <= 200:
        raise ValueError('Invalid page size')
    state = None
    if cursor:
        try:
            if len(cursor)>2048:
                raise ValueError()
            state=json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if (not isinstance(state.get('position'), list) or len(state['position']) != 2
                or state['filter'] != _fingerprint(filters) or type(state['watermark']) is not int
                or not 0 <= state['watermark'] <= 2**63-1 or type(state['position'][0]) is not int
                or not -(2**63) <= state['position'][0] <= 2**63-1
                or not isinstance(state['position'][1],str) or len(state['position'][1])>256):
                raise ValueError()
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise ValueError('Invalid cursor or changed filters; restart history') from None
    db=_connection(store)
    try:
        watermark = state['watermark'] if state else db.execute('SELECT value FROM history_sequence WHERE singleton=1').fetchone()[0]
        where,args=_where(filters,watermark)
        total=db.execute('SELECT COUNT(*) FROM events'+where,args).fetchone()[0]
        totals=sql_summary(db,where,args)
        page_where,page_args=_where(filters,watermark,state['position'] if state else None)
        rows=db.execute('SELECT occurred_us,id,payload FROM events'+page_where+' ORDER BY occurred_us DESC,id DESC LIMIT ?',[*page_args,page_size+1]).fetchall()
        next_cursor=None
        if len(rows)>page_size:
            last=rows[page_size-1]
            next_cursor=base64.urlsafe_b64encode(json.dumps({'filter':_fingerprint(filters),'watermark':watermark,'position':[last[0],last[1]]}).encode()).decode()
        return {'items':[json.loads(r[2]) for r in rows[:page_size]],'total_items':total,
                'totals':totals,'next_cursor':next_cursor,'page_size':page_size,'filters':filters.public()}
    finally:
        db.close()


def stream_export(store, filters):
    """One consistent read snapshot; close on completion or consumer cancellation."""
    db=_connection(store)
    try:
        where,args=filters.sql()
        cursor=db.execute('SELECT payload FROM events'+where+' ORDER BY occurred_us,id',args)
        yield '{"schema_version":1,"filters":'+json.dumps(filters.public())+',"events":['
        first=True
        while True:
            rows=cursor.fetchmany(256)
            if not rows:
                break
            for row in rows:
                yield ('' if first else ',')+row[0]
                first=False
        yield ']}'
    finally:
        db.close()
