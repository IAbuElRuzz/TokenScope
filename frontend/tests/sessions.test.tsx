import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {Session,newestFirst,sessionGroups} from '../src/data';
import {SessionsPanel} from '../src/SessionsPanel';

const session=(id:string,provider:string,time:string,status='idle'):Session=>({session_id:id,provider,tool:'test-agent',model:'test-model',status,last_seen:time,input_tokens:100,output_tokens:20,cache_read_tokens:40,cache_creation_tokens:0,reasoning_tokens:10,total_tokens:120,cost_usd:null});
const old=session('old','openai','2026-11-01T12:00:00Z');
const latest=session('latest','openai','2026-11-15T12:00:00Z');
const other=session('other','anthropic','2026-11-14T12:00:00Z');
describe('session history',()=>{
  it('sorts by actual latest activity without mutating source order',()=>{
    const input=[old,other,latest];
    expect(newestFirst(input).map(s=>s.session_id)).toEqual(['latest','other','old']);
    expect(input[0]).toBe(old);
    expect(newestFirst([latest,session('offset','openai','2026-11-15T13:30:00+02:00')])[0]).toBe(latest);
  });
  it('keeps newest idle session visible and older sessions accessible without losing totals',()=>{
    const groups=sessionGroups([old,other,latest]);
    expect(groups.map(g=>g.provider)).toEqual(['openai','anthropic']);
    expect(groups[0].current).toEqual([latest]);
    expect(groups[0].past).toEqual([old]);
    expect(groups.reduce((n,g)=>n+g.total,0)).toBe(360);
  });
  it('keeps simultaneous active sessions visible, and moves returning history to newest',()=>{
    const active={...old,status:'active'};
    expect(sessionGroups([active,latest])[0].current.map(s=>s.session_id)).toEqual(['latest','old']);
    expect(sessionGroups([latest,{...old,last_seen:'2026-11-16T00:00:00Z'}])[0].current[0].session_id).toBe('old');
  });
  it('renders exact totals, provider groups and expandable past sessions in order',()=>{
    const html=renderToStaticMarkup(<SessionsPanel sessions={[old,other,{...latest,total_tokens:123456}]} onSelect={()=>{}}/>);
    expect(html).toContain('123,456');
    expect(html).toContain('123,576 recorded tokens');
    expect(html).toContain('Past sessions (1)');
    expect(html).toContain('<details');
    expect(html.indexOf('dateTime="2026-11-15')).toBeLessThan(html.indexOf('dateTime="2026-11-01'));
    expect(html).toContain('OpenAI / Codex');
  });
});
