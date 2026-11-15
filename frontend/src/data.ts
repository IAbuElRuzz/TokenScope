export type Session={session_id:string;provider:string;tool:string;model:string;project?:string;status:string;input_tokens:number;output_tokens:number;cache_read_tokens:number;cache_creation_tokens:number;reasoning_tokens:number;total_tokens:number;cost_usd:number|null;context_tokens?:number|null;context_window?:number|null;context_accuracy?:string;last_seen:string};
export type UsageEvent={event_id:string;session_id:string;timestamp:string;provider:string;model:string;tool?:string;tokens?:Record<string,number>;input_tokens?:number;output_tokens?:number;context_tokens?:number|null;total_tokens?:number;cost_usd?:number|null;context?:{used?:number;window?:number};[key:string]:unknown};
export type Alert={id?:string;message:string;severity?:string};
export type Snapshot={sessions:Session[];events:UsageEvent[];totals:Record<string,number>;alerts:Alert[]};
export const empty:Snapshot={sessions:[],events:[],totals:{},alerts:[]};
export const count=(n:number|undefined|null)=>n==null?'—':new Intl.NumberFormat('en',{notation:n>=10000?'compact':'standard',maximumFractionDigits:1}).format(n);
export const money=(n:number|undefined|null)=>n==null?'Unavailable':new Intl.NumberFormat('en',{style:'currency',currency:'USD',maximumFractionDigits:4}).format(n);
export function contextPercent(s:Session):number|null{return s.context_tokens!=null&&s.context_window&&s.context_window>0?100*s.context_tokens/s.context_window:null;}
export function newestFirst(sessions:Session[]):Session[]{
  const time=(value:string)=>Number.isFinite(Date.parse(value))?Date.parse(value):0;
  return [...sessions].sort((a,b)=>time(b.last_seen)-time(a.last_seen)||a.session_id.localeCompare(b.session_id));
}
export function selectSessions(sessions:Session[],filters:Record<string,string>){return newestFirst(sessions.filter(s=>Object.entries(filters).every(([k,v])=>!v||(k==='date'?s.last_seen.slice(0,10)>=v:String(s[k as keyof Session]??'')===v))));}
export const exactTokens=(n:number)=>new Intl.NumberFormat('en',{maximumFractionDigits:0}).format(n);
export function sessionGroups(sessions:Session[]){
  const groups=new Map<string,Session[]>();
  for(const session of newestFirst(sessions)){
    const group=groups.get(session.provider)??[];
    group.push(session);groups.set(session.provider,group);
  }
  return [...groups].map(([provider,items])=>({provider,
    total:items.reduce((sum,s)=>sum+s.total_tokens,0),
    current:items.filter((s,i)=>i===0||s.status==='active'),
    past:items.filter((s,i)=>i>0&&s.status!=='active'),
  }));
}
export function eventTotal(e:UsageEvent):number {return Number(e.total_tokens??e.tokens?.total_tokens??e.tokens?.total??0);}
export function bucketEvents(events:UsageEvent[]){const buckets=new Map<string,{input:number;output:number;total:number;cost:number}>();for(const e of events){const key=e.timestamp?.slice(0,16);if(!key)continue;const b=buckets.get(key)??{input:0,output:0,total:0,cost:0};b.input+=e.input_tokens??e.tokens?.input_tokens??e.tokens?.input??0;b.output+=e.output_tokens??e.tokens?.output_tokens??e.tokens?.output??0;b.total+=eventTotal(e);b.cost+=Number(e.cost_usd??0);buckets.set(key,b);}return [...buckets].sort(([a],[b])=>a.localeCompare(b)).map(([time,data])=>({time,...data}));}
