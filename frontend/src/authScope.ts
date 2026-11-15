import type {ApiRequest} from './api';
const cancelled=()=>new DOMException('Account changed or request cancelled.','AbortError');
/** Invalidates async results immediately, before React effect cleanup runs. */
export class AuthScope {
  private generation=0;
  private controllers=new Set<AbortController>();
  capture(){const generation=this.generation;return ()=>generation===this.generation;}
  invalidate(){this.generation++;for(const controller of this.controllers)controller.abort();this.controllers.clear();}
  wrap(base:ApiRequest):ApiRequest{const authorized=this.capture();return async(path,init={})=>{
    if(!authorized())throw cancelled();const current=this.capture(),controller=new AbortController();this.controllers.add(controller);
    const abort=()=>controller.abort();init.signal?.addEventListener('abort',abort,{once:true});if(init.signal?.aborted)abort();
    const release=()=>{this.controllers.delete(controller);init.signal?.removeEventListener('abort',abort);};
    try{const response=await base(path,{...init,signal:controller.signal});if(!current()||controller.signal.aborted)throw cancelled();
      if(!response.ok||response.status===204){release();return response;}
      // Keep the body cancellable and reject stale parsed values as well as stale headers.
      return new Proxy(response,{get(target,key){if(['json','text','blob','arrayBuffer','formData'].includes(String(key)))return async()=>{try{const consume=Reflect.get(target,key,target) as ()=>Promise<unknown>;const value=await consume.call(target);if(!current()||controller.signal.aborted)throw cancelled();return value;}finally{release();}};const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;}});
    }catch(error){release();throw error;}
  };}
}
