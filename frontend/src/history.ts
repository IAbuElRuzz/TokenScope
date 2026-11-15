import type {AnalyticsReport,AnalyticsSummary} from './AnalyticsPanel';
import type {UsageEvent} from './data';
export type HistoryFilters={provider:string;model:string;tool:string;session_id:string;project:string;device_id:string;request_id:string;start:string;end:string};
export const emptyHistoryFilters:HistoryFilters={provider:'',model:'',tool:'',session_id:'',project:'',device_id:'',request_id:'',start:'',end:''};
/** Date inputs are UTC calendar days; the visible final day is inclusive. */
export function historyQuery(filters:HistoryFilters,page?:number,pageSize=50){
 const query=new URLSearchParams();
 for(const [key,value] of Object.entries(filters)){
  if(!value)continue;
  if(key==='start'||key==='end'){
   const day=new Date(`${value}T00:00:00.000Z`);
   if(!Number.isFinite(day.getTime())||day.toISOString().slice(0,10)!==value)throw Error('Choose valid UTC calendar dates.');
   if(key==='end')day.setUTCDate(day.getUTCDate()+1);
   query.set(key,day.toISOString());
  }else query.set(key,value);
 }
 if(query.has('start')&&query.has('end')&&query.get('start')!>=query.get('end')!)throw Error('End date must be on or after start date.');
 if(page!=null){query.set('page',String(page));query.set('page_size',String(pageSize));}
 return query.toString();
}
export type HistoryPage={items:UsageEvent[];page:number;page_size:number;total_items:number;total_pages:number;totals:AnalyticsSummary;analytics:AnalyticsReport;filters:Record<string,unknown>;options:{providers:string[];models:string[];tools:string[];sessions:string[];projects:string[];devices:string[]}};
export type RelatedRequest={event_id:string;timestamp:string;[key:string]:unknown};
export type RequestDetail={event:UsageEvent;accounting:{input_total_tokens:number;uncached_input_tokens:number;cache_read_tokens:number;cache_creation_tokens:number;output_total_tokens:number;reasoning_tokens:number;decomposition_valid:boolean;provider_total_residual_tokens:number};pricing:{estimated_cost_usd:number|null;billed_cost_usd:number|null;confidence:string;source:string|null;version:string|null;record:Record<string,unknown>|null};related:{telemetry_identity_collisions:RelatedRequest[];repeated_prompt_candidates:RelatedRequest[];telemetry_identity_collision_count?:number;repeated_prompt_candidate_count?:number};limits:string[]};
/** A completed old request must never replace a newer filter/detail selection. */
export class LatestRequest {
 private serial=0;private controller:AbortController|null=null;
 begin(){this.controller?.abort();const controller=new AbortController(),serial=++this.serial;this.controller=controller;return {signal:controller.signal,current:()=>this.serial===serial&&!controller.signal.aborted};}
 cancel(){this.serial++;this.controller?.abort();this.controller=null;}
}
