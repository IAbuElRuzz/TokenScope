import {describe,it,expect} from 'vitest';
import {emptyHistoryFilters,historyCursorQuery} from '../src/history';
describe('cursor history queries',()=>{
 it('retains exact filters and encodes opaque cursors without offsets',()=>{
  const query=new URLSearchParams(historyCursorQuery({...emptyHistoryFilters,model:'model/a',end:'2026-11-15'},'opaque=+/'));
  expect(query.get('cursor')).toBe('opaque=+/');expect(query.get('model')).toBe('model/a');
  expect(query.get('end')).toBe('2026-11-16T00:00:00.000Z');expect(query.has('page')).toBe(false);
 });
 it('starts a new browsing window without a cursor',()=>{expect(historyCursorQuery(emptyHistoryFilters)).toBe('');});
});
