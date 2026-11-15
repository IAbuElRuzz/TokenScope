export type AppMode='server'|'standalone';
export type ServerUser={id:string;username:string;role:string};
export type ServerIdentity={user:ServerUser;csrf_token:string};
export type ApiRequest=(path:string,init?:RequestInit)=>Promise<Response>;
export function apiRequest(mode:AppMode,token:string,csrf:string):ApiRequest {
  return (path,init={})=>{
    const headers=new Headers(init.headers);
    if(mode==='standalone'&&token)headers.set('Authorization',`Bearer ${token}`);
    if(mode==='server'&&!['GET','HEAD','OPTIONS'].includes((init.method??'GET').toUpperCase()))headers.set('X-CSRF-Token',csrf);
    return fetch(path,{...init,headers,credentials:'same-origin'});
  };
}
export async function responseJson<T>(response:Response):Promise<T>{
  if(!response.ok)throw Error(response.status===401?'Your session expired. Sign in again.':response.status===403?'This action is not permitted.':`Request failed (${response.status}).`);
  return response.json();
}
