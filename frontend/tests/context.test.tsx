import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {ContextGauge} from '../src/ContextGauge';
import {Session} from '../src/data';
const base={context_tokens:710,context_window:1000,context_accuracy:'estimated'} as Session;
describe('ContextGauge component',()=>{it('labels estimates and clamps bar width',()=>{const html=renderToStaticMarkup(<ContextGauge session={base}/>);expect(html).toContain('71%');expect(html).toContain('estimated');expect(html).toContain('width:71%');});it('renders unavailable rather than zero occupancy when absent',()=>{expect(renderToStaticMarkup(<ContextGauge session={{...base,context_tokens:null}}/>)).toContain('Unavailable');});it('caps overflow while preserving actual utilization',()=>{const html=renderToStaticMarkup(<ContextGauge session={{...base,context_tokens:1200}}/>);expect(html).toContain('120%');expect(html).toContain('width:100%');});});
