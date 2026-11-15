import {cpSync,mkdirSync,rmSync} from 'node:fs';
const target=new URL('../../backend/static',import.meta.url);
rmSync(target,{recursive:true,force:true});
mkdirSync(target,{recursive:true});
cpSync(new URL('../dist',import.meta.url),target,{recursive:true});
