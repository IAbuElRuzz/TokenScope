import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {CollectorHealthTable,CollectorHealth,healthTime} from '../src/CollectorHealthPanel';
const device:CollectorHealth={id:'d',name:'Laptop',received_at:1800000000,last_successful_sync:null,pending_events:null,invalid_records:0,collector_version:null,protocol_version:1,sources:[{tool:'codex',available:true,readable:true,malformed_records:2},{tool:'claude',available:false}],status:'stale'};
describe('collector health evidence',()=>{
 it('preserves unknown vs zero diagnostics, sources and stale status',()=>{const html=renderToStaticMarkup(<CollectorHealthTable devices={[device]}/>);expect(html).toContain('Unavailable pending');expect(html).toContain('0 invalid records');expect(html).toContain('codex · available · readable · 2 malformed');expect(html).toContain('claude · unavailable');expect(html).toContain('stale');expect(html).toContain('Protocol 1');});
 it('converts Unix heartbeat seconds including epoch and never invents successful sync',()=>{expect(healthTime(0)).toBe(new Date(0).toLocaleString());expect(healthTime(1800000000)).toBe(new Date(1800000000000).toLocaleString());expect(healthTime(null)).toBe('Unavailable');});
 it('explains standalone empty health without showing fabricated collectors',()=>{const html=renderToStaticMarkup(<CollectorHealthTable devices={[]}/>);expect(html).toContain('Standalone monitoring does not require an enrolled collector');expect(html).not.toContain('Laptop');});
});
