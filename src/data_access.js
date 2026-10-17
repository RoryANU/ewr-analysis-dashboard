/* The only transport boundary. Offline builds never probe or call a server. */
const DashboardData=(()=>{
 const data=JSON.parse(document.getElementById('offline-data').textContent);
 if(data.version!==1)throw Error('Unsupported embedded data version: '+data.version);
 const capabilities=Object.freeze({canUpload:false,hasFlows:Object.values(data.scenarios).some(s=>Object.keys(s.flows).length)});
 let worker,seq=0;const pending=new Map();
 function numeric(kind,q,signal){
  if(signal?.aborted)return Promise.reject(new DOMException('Aborted','AbortError'));
  if(!worker){const url=URL.createObjectURL(new Blob([JSON.parse(document.getElementById('offline-worker').textContent)],{type:'text/javascript'}));
   try{worker=new Worker(url);}finally{URL.revokeObjectURL(url);}
   worker.onmessage=({data:m})=>{const p=pending.get(m.id);if(!p)return;pending.delete(m.id);p.cleanup();m.error?p.reject(Error(m.error)):p.resolve(m.result);};
   worker.onerror=e=>{for(const p of pending.values()){p.cleanup();p.reject(Error(e.message||'Offline computation failed'));}pending.clear();worker.terminate();worker=null;};
  }
  return new Promise((resolve,reject)=>{const id=++seq,abort=()=>{pending.delete(id);reject(new DOMException('Aborted','AbortError'));},cleanup=()=>signal?.removeEventListener('abort',abort);signal?.addEventListener('abort',abort,{once:true});pending.set(id,{resolve,reject,cleanup});
   const selected={scenarios:{},regimes:{[q.gauge]:data.regimes[q.gauge]}};
   for(const k of ['base','comp'])if(q[k])selected.scenarios[q[k]]={flows:{[q.gauge]:data.scenarios[q[k]]?.flows[q.gauge]}};
   worker.postMessage({id,kind,q,data:selected});
  });
 }
 async function json(url,{signal}={}){
  if(signal?.aborted)throw new DOMException('Aborted','AbortError');
  const u=new URL(url,'https://offline.invalid'),q=Object.fromEntries(u.searchParams),kind=u.pathname.split('/').pop();
  if(kind==='ewrs')return data.inventories[q.gauge]||{ewrs:[],inventory:[]};
  if(['spells','regimes','curves','heatmap'].includes(kind))return numeric(kind,q,signal);
  if(kind==='ewryears'||kind==='interevents'){
   const sid=q.base||q.scn,sc=data.scenarios[sid],key=JSON.stringify([q.gauge,q.ewr,q.pu||'']);
   const lookup=s=>{const dict=s?.[kind==='ewryears'?'years':'interevents'];let value=dict?.[key];if(!value&&!q.pu){const first=Object.keys(dict||{}).find(k=>{const a=JSON.parse(k);return a[0]===q.gauge&&a[1]===q.ewr;});value=dict?.[first];}if(!value)throw Error('This EWR diagnostic was not embedded for the selected scenario');return structuredClone(value);};
   const out=lookup(sc);if(kind==='ewryears'&&q.comp){out.scenarios.comp=lookup(data.scenarios[q.comp]).scenarios.base;OfflineNumerics.pair(out,true);}return out;
  }
  throw Error('Scenarios are prepared centrally; this file cannot upload or run Python.');
 }
 async function request(url,options={}){const value=await json(url,options);return {ok:true,status:200,json:async()=>value};}
 return {capabilities,json,request,metadata:data.metadata};
})();
