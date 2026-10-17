/* Pure daily-series diagnostics. UTC day integers avoid DST and preserve missing days.
 * Shared by the browser worker and the Python/Node parity suite. */
(function(root){
'use strict';
const DAY=86400000, round=(x,n=3)=>{if(!Number.isFinite(x))return null;const k=10**n,v=x*k,l=Math.floor(v),f=v-l;return (Math.abs(f-.5)<Number.EPSILON*Math.max(1,Math.abs(v))*4?(l%2===0?l:l+1):Math.round(v))/k;};
const date=d=>new Date(d*DAY), iso=d=>date(d).toISOString().slice(0,10);
const wy=d=>date(d).getUTCFullYear()-(date(d).getUTCMonth()<6?1:0);
const daysIn=w=>(Date.UTC(w+1,6,1)-Date.UTC(w,6,1))/DAY;
const month=d=>date(d).getUTCMonth()+1;
const window=(d,s,e)=>s<=e?month(d)>=s&&month(d)<=e:month(d)>=s||month(d)<=e;
const range=(n,f)=>Array.from({length:n},(_,i)=>f(i));
const sort=a=>a.sort((a,b)=>a-b);
const pct=(a,p)=>{const i=(a.length-1)*p/100,l=Math.floor(i);return a[l]+(a[Math.ceil(i)]-a[l])*(i-l);};
const exceed=(a,x)=>{let l=0,h=a.length;while(l<h){const m=(l+h)>>>1;if(a[m]<x)l=m+1;else h=m;}return a.length?100*(a.length-l)/a.length:NaN;};
const nice=x=>{if(!(x>0))return 100;const k=10**Math.max(Math.floor(Math.log10(x))-1,0);return Math.ceil(x/k)*k;};
function decode(s){
  if(s.values)return {start:s.start,values:Float64Array.from(s.values,v=>v==null?NaN:v)};
  const raw=atob(s.data), bytes=Uint8Array.from(raw,c=>c.charCodeAt(0)), view=new DataView(bytes.buffer);
  const values=new Float64Array(s.count);let prev=0;
  for(let i=0;i<s.count;i++){const d=view.getFloat64(i*8,true);if(Number.isNaN(d))values[i]=NaN;else{prev=Object.hasOwn(s.overrides||{},i)?s.overrides[i]:prev+d;values[i]=prev/s.scale;}}
  return {start:s.start,values};
}
function runs(s,hit){
 const v=s.values, n=v.length, years={wy:[],days:[],longest:[],n:[],complete:[]}, out=[],groups=new Map();
 for(let i=0;i<n;i++){const w=wy(s.start+i);if(!groups.has(w))groups.set(w,[]);groups.get(w).push(i);}
 for(let i=0;i<n;i++){if(!hit[i]||!Number.isFinite(v[i]))continue;let j=i;while(j+1<n&&hit[j+1]&&Number.isFinite(v[j+1]))j++;
 out.push({start:iso(s.start+i),end:iso(s.start+j),days:j-i+1,censored:i===0||j===n-1||!Number.isFinite(v[i-1])||!Number.isFinite(v[j+1]),wy:wy(s.start+i)});i=j;}
 for(const [w,idx] of groups){const complete=idx.length===daysIn(w)&&idx.every(i=>Number.isFinite(v[i]));let best=0,cur=0,total=0;
 for(const i of idx){const yes=hit[i]&&Number.isFinite(v[i]);cur=yes?cur+1:0;best=Math.max(best,cur);total+=yes?1:0;}
 years.wy.push(w);years.complete.push(+complete);years.days.push(complete?total:null);years.longest.push(complete?best:null);years.n.push(complete?out.filter(r=>r.wy===w).length:null);}
 return {years,runs:out};
}
function pair(out,achievement=false){
 if(!out.scenarios.comp)return out;
 const b=out.scenarios.base.years,c=out.scenarios.comp.years,common=b.wy.filter((w,i)=>b.complete[i]&&c.complete[c.wy.indexOf(w)]);
 out.paired={wy:common};if(achievement){const pb=common.map(w=>b.achieved[b.wy.indexOf(w)]),pc=common.map(w=>c.achieved[c.wy.indexOf(w)]),sum=a=>a.reduce((s,x)=>s+x,0),n=common.length;
 Object.assign(out.paired,{n,base:sum(pb),comp:sum(pc),gained:pb.filter((x,i)=>!x&&pc[i]).length,lost:pb.filter((x,i)=>x&&!pc[i]).length,pct_base:n?round(100*sum(pb)/n):null,pct_comp:n?round(100*sum(pc)/n):null});}return out;
}
function calculate(kind,q,data){
 const keys=['base','comp'].filter(k=>q[k]), series={};
 for(const k of keys){const raw=data.scenarios[q[k]]?.flows[q.gauge];if(!raw)throw Error('No embedded daily flows for this scenario and gauge');series[k]=decode(raw);}
 if(kind==='spells'||kind==='regimes'){
 const out={gauge:q.gauge,scenarios:{}};
 if(kind==='spells')out.thr=+q.thr;else out.regimes=data.regimes[q.gauge]||[];
 for(const k of keys){const s=series[k];if(kind==='spells'){const r=runs(s,Array.from(s.values,v=>v<+q.thr));r.years.below=r.years.days;delete r.years.days;out.scenarios[k]={years:r.years,longest_runs:r.runs.sort((a,b)=>b.days-a.days).slice(0,10),n_runs:r.runs.length,missing_days:Array.from(s.values).filter(v=>!Number.isFinite(v)).length};}
 else {const per=out.regimes.map(c=>runs(s,Array.from(s.values,v=>v>=c.thr)).years);out.scenarios[k]={regimes:per,years:per[0]||{wy:[],complete:[]}};}}
 return pair(out);
 }
 const start=Math.max(...keys.map(k=>series[k].start)),end=Math.min(...keys.map(k=>series[k].start+series[k].values.length-1));
 const dates=range(Math.max(0,end-start+1),i=>start+i),val=(k,d)=>series[k].values[d-series[k].start];
 if(!dates.length)throw Error('the scenarios share no dates at this gauge');
 if(kind==='heatmap')return heat(q,keys,dates,val);
 const curve=q.kind||(q.comp?'fdc':'hydro');
 if(curve==='hydro'){
 const out={kind:curve,dates:dates.map(iso),years:[],missing_days:dates.filter(d=>keys.some(k=>!Number.isFinite(val(k,d)))).length};
 for(const k of keys)out[k]=dates.map(d=>round(val(k,d),2));out.flow=out.base;
 for(const w of new Set(dates.map(wy))){const ds=dates.filter(d=>wy(d)===w);if(ds.length===daysIn(w)&&ds.every(d=>keys.every(k=>Number.isFinite(val(k,d))))){const y={wy:w};for(const k of keys)y[k]=round(ds.reduce((a,d)=>a+val(k,d),0),1);out.years.push(y);}}return out;
 }
 const observed=dates.filter(d=>keys.every(k=>Number.isFinite(val(k,d))));
 if(!observed.length)throw Error('the scenarios share no observed dates at this gauge');
 if(curve==='mdfdc'){
 if(!q.comp)throw Error('the monthly differential flow duration needs two scenarios');
 const months=[7,8,9,10,11,12,1,2,3,4,5,6],by={};let top=0;
 for(const k of keys){by[k]=months.map(m=>sort(observed.filter(d=>month(d)===m).map(d=>val(k,d))));for(const a of by[k])if(a.length)top=Math.max(top,pct(a,99));}
 let ymax=q.ymax?+q.ymax:top;if(ymax<=0)throw Error('no positive flows to plot');let flows;
 if(q.ymin!=null&&q.ymin!==''){const ymin=Math.max(0,+q.ymin);if(ymax<=ymin)throw Error('the flow range is empty');flows=range(200,i=>ymin+(ymax-ymin)*i/199);}else{ymax=nice(ymax);flows=range(200,i=>ymax*(i+1)/200);}
 const b=flows.map(x=>by.base.map(a=>exceed(a,x))),c=flows.map(x=>by.comp.map(a=>exceed(a,x))),z=a=>a.map(r=>r.map(v=>round(v)));
 return {kind:curve,months,flows:flows.map(v=>round(v,2)),base:z(b),comp:z(c),diff:z(c.map((r,i)=>r.map((v,j)=>v-b[i][j]))),ymax};
 }
 const vals={},sm=+q.sm,em=+q.em,ds=observed.filter(d=>!(sm>=1&&sm<=12&&em>=1&&em<=12)||window(d,sm,em));
 if(!ds.length)throw Error('no flow data inside that timing window');for(const k of keys)vals[k]=sort(ds.map(d=>val(k,d)));
 const marks=(q.thr||'').split(',').filter(t=>t.trim()).map(t=>Object.fromEntries([['thr',+t],...keys.map(k=>[k,round(exceed(vals[k],+t))])]));
 if(curve==='fdc'){const p=range(401,i=>i/4),out={kind:curve,p,marks};for(const k of keys)out[k]=p.map(p=>round(pct(vals[k],100-p),2));if(q.comp)out.diff=out.comp.map((v,i)=>round(v-out.base[i],2));return out;}
 if(curve==='dfdc'){
 if(!q.comp)throw Error('the differential flow duration curve needs two scenarios');let lo=Infinity,hi=0;for(const k of keys)for(const v of vals[k])if(v>0){lo=Math.min(lo,v);hi=Math.max(hi,v);}if(!hi)throw Error('no positive flows to plot');lo=Math.max(lo,.01);
 const xs=range(500,i=>Math.exp(Math.log(lo)+(Math.log(hi)-Math.log(lo))*i/499)),b=xs.map(x=>exceed(vals.base,x)),c=xs.map(x=>exceed(vals.comp,x));return {kind:curve,x:xs.map(x=>round(x)),base:b.map(x=>round(x)),comp:c.map(x=>round(x)),diff:c.map((x,i)=>round(x-b[i])),marks};}
 throw Error('unknown curve kind '+curve);
}
function heat(q,keys,dates,val){
 dates=dates.filter(d=>keys.some(k=>Number.isFinite(val(k,d))));if(!dates.length)throw Error('the scenarios share no dates at this gauge');
 const all=sort(dates.flatMap(d=>keys.map(k=>val(k,d))).filter(Number.isFinite));
 const xlim=Math.max(10,Math.min(q.xlim?Math.trunc(+q.xlim):nice(pct(all,99.5)),200000)),dmax=Math.max(2,Math.min(Math.trunc(+(q.dmax||120)),366));
 const xmin=Math.max(0,Math.min(Math.trunc(+(q.xmin||0)),xlim-1)),dmin=Math.max(1,Math.min(Math.trunc(+(q.dmin||1)),dmax)),cols=Math.max(40,Math.min(Math.trunc(+(q.cols||400)),1500));
 const spacing=Math.max(1,Math.ceil((xlim-xmin+1)/cols)),thresholds=range(Math.floor((xlim-xmin)/spacing)+1,i=>xmin+i*spacing),durations=range(dmax-dmin+1,i=>dmin+i),sm=+(q.sm||7),em=+(q.em||6),consecutive=q.days==='consec';
 const groups=[];
 for(const w of new Set(dates.map(wy))){const ds=dates.filter(d=>wy(d)===w);let expected=0;for(let d=Date.UTC(w,6,1)/DAY;d<Date.UTC(w+1,6,1)/DAY;d++)if(window(d,sm,em))expected++;
 if(expected&&keys.every(k=>ds.filter(d=>window(d,sm,em)&&Number.isFinite(val(k,d))).length===expected))groups.push(ds);}
 function annual(k,ds,d){const a=ds.map(day=>window(day,sm,em)?val(k,day):-Infinity);if(!consecutive)return sort(a)[a.length-d]??-Infinity;
 // Monotone deque: maximum rolling minimum, resetting across missing dates.
 let best=-Infinity,left=0;const deque=[];let head=0;
 for(let i=0;i<a.length;i++){if(i&&ds[i]!==ds[i-1]+1){left=i;head=0;deque.length=0;}while(deque.length>head&&a[deque[deque.length-1]]>=a[i])deque.pop();deque.push(i);while(head<deque.length&&deque[head]<=i-d)head++;if(i-left+1>=d)best=Math.max(best,a[deque[head]]);}return best;}
 const rate=(a,t)=>groups.length?Math.fround(100*a.filter(x=>x>=t).length/groups.length):NaN;
 const matrices=keys.map(k=>durations.map(d=>{const a=groups.map(ds=>annual(k,ds,d));return thresholds.map(t=>rate(a,t));}));
 const z=a=>a.map(r=>r.map(x=>round(x))),out={thresholds,durations,xlim,xmin,spacing,years:groups.length,dates:[iso(dates[0]),iso(dates[dates.length-1])],base:z(matrices[0]),consecutive};
 if(q.comp){out.comp=z(matrices[1]);out.diff=z(matrices[1].map((r,i)=>r.map((v,j)=>Math.fround(v-matrices[0][i][j]))));}
 if(q.mthr&&q.mdur&&groups.length)out.mark=keys.map(k=>round(rate(groups.map(ds=>annual(k,ds,Math.trunc(+q.mdur))),+q.mthr)));
 return out;
}
root.OfflineNumerics={calculate,decode,pair};
if(typeof module!=='undefined')module.exports=root.OfflineNumerics;
})(globalThis);
