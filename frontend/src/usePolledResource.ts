import {useEffect,useState} from 'react';
import {ApiRequest,responseJson} from './api';
/** One request at a time; cancellation and a live flag block stale account results. */
export function usePolledResource<T>(request:ApiRequest,path:string,interval:number){
 const [data,setData]=useState<T|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[revision,setRevision]=useState(0);
 useEffect(()=>{let live=true,inFlight=false;const controller=new AbortController();setData(null);setError('');setLoading(true);
  async function update(){if(inFlight||!live)return;inFlight=true;try{const next=await responseJson<T>(await request(path,{signal:controller.signal}));if(live){setData(next);setError('');}}catch(e){if(live&&!(e instanceof DOMException&&e.name==='AbortError'))setError(e instanceof Error?e.message:'Unable to refresh data.');}finally{inFlight=false;if(live)setLoading(false);}}
  void update();const timer=setInterval(()=>{void update();},interval);return()=>{live=false;clearInterval(timer);controller.abort();};
 },[request,path,interval,revision]);
 return {data,error,loading,refresh:()=>setRevision(n=>n+1)};
}
