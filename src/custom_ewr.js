// User EWRs. Part 1 is pure and shared with Node tests; part 2 connects the authoritative server registry to dashboard state.
// ---- part 1: definitions, validation and row/result transforms ------------------------------------------------------
const UEWR_KEY='ewrUserEwrs', UEWR_DELETED_KEY='ewrUserEwrsDeleted';
const CODE_RE=/^U_[A-Za-z0-9]{1,20}(_[A-Za-z0-9]{1,8})?$/;
const userResultKey=v=>[v[0],v[1],v[2]].join('|');
const userIdentity=s=>[s.gauge,s.code,...s.pus.slice().sort()].join('|');
const userVersion=v=>v?.[11]?.uewrVer??null;
const userUnit=s=>s.kind==='S'?'EC':s.kind==='L'?'m':'ML/d';
const userClone=v=>Array.isArray(v)?v.map(userClone):v&&typeof v==='object'?Object.fromEntries(Object.entries(v).map(([k,x])=>[k,userClone(x)])):v;
const userMonthLength=m=>new Date(Date.UTC(2000,m,0)).getUTCDate();

function validateSpec(spec,schema,ctx={}){
  const errors=Object.create(null), d=spec&&typeof spec==='object'&&!Array.isArray(spec)?userClone(spec):null;
  if(!d)return {ok:false,errors:{spec:'Expected an object'},spec:null};
  const allowed=['id','ver','code','name','kind','handler','gauge','second_gauge','pus','objectives','regime','params','salinity','created','updated'];
  for(const k of Object.keys(d))if(!allowed.includes(k))errors[k]='Unknown field';
  if(!CODE_RE.test(d.code||''))errors.code='Use a valid U_ code without hyphens';
  if(d.id!=null&&(typeof d.id!=='string'||!/^[a-z0-9_]{1,24}$/.test(d.id)))errors.id='Invalid user EWR id';
  if(typeof d.name!=='string'||!d.name.trim()||d.name.length>80)errors.name='A name of 1–80 characters is required';
  const kind=typeof d.kind==='string'&&schema&&Object.hasOwn(schema,d.kind)?schema[d.kind]:null;
  const def=kind&&typeof d.handler==='string'&&Object.hasOwn(kind,d.handler)?kind[d.handler]:null;
  if(!kind)errors.kind='Choose F, V, L or S';
  if(!def)errors.handler='Handler is not offered for this kind';
  const gs=ctx.gauges||[], pus=Array.isArray(ctx.pusByGauge?.[d.gauge])?ctx.pusByGauge[d.gauge]:[];
  if(!gs.includes(d.gauge)||!pus.length)errors.gauge='Choose a review gauge with parameter-sheet rows';
  if(!Array.isArray(d.pus)||!d.pus.length||d.pus.some(p=>!pus.includes(p)))errors.pus='Choose at least one review planning unit at this gauge';
  d.objectives??=[];d.regime??=null;d.second_gauge??=null;
  if(!Array.isArray(d.objectives)||d.objectives.some(o=>!(ctx.objs||[]).includes(o)))errors.objectives='Choose objectives from the review';
  if(d.regime!=null&&!(ctx.regimes||[]).includes(d.regime))errors.regime='Choose a regime from the review';
  if((ctx.specs||[]).some(s=>s.code===d.code&&s.id!==d.id)||(ctx.sheetCodes||[]).includes(d.code))errors.code='Code is already in use';
  if(!def)return {ok:false,errors,spec:null};
  let input=d.params===undefined?{}:d.params;if(input===null||typeof input!=='object'||Array.isArray(input)){errors.params='Expected an object';input={};}
  for(const k of Object.keys(input))if(!def.fields.includes(k))errors['params.'+k]='Parameter is not used by this handler';
  const p={},tds=d.kind==='S'&&d.salinity?.unit==='TDS';
  for(const f of def.fields){
    let v=input[f];const blank=v==null||v==='';if(blank)v=def.defaults[f]??null;
    if(f==='MinSpell'&&blank)v=d.handler.startsWith('lowflow_handle')?1:p.Duration;
    if(v==null){p[f]=null;continue;}
    const n=typeof v==='number'||typeof v==='string'&&v.trim()!==''?Number(v):NaN,b=def.bounds[f]||{},integer=b.integer&&!(tds&&['FlowThresholdMin','FlowThresholdMax'].includes(f));
    if(!Number.isFinite(n)||(integer&&!Number.isInteger(n))||(b.min!=null&&n<b.min)||(b.max!=null&&n>b.max)){
      errors['params.'+f]='Enter a finite number within the allowed bounds'+(integer?' (integer required)':'');p[f]=null;
    }else p[f]=n;
  }
  for(const f of ['Duration','MinSpell','TargetFrequency'])if(p[f]==null)errors['params.'+f]??='Required';
  if(p.MinSpell!=null&&p.Duration!=null&&p.MinSpell>p.Duration)errors['params.MinSpell']='MinSpell must be between 1 and Duration';
  if(p.MaxSpell!=null&&p.Duration!=null&&p.MaxSpell<p.Duration)errors['params.MaxSpell']='MaxSpell must be at least Duration';
  if(p.DrawdownRate!=null&&p.DrawdownRate<=0)errors['params.DrawdownRate']='Must be positive';
  for(const prefix of ['Start','End'])if(p[prefix+'Month']!=null&&p[prefix+'Day']!=null&&p[prefix+'Day']>userMonthLength(p[prefix+'Month']))errors['params.'+prefix+'Day']='Invalid day for this month';
  if(!['StartMonth','EndMonth','StartDay','EndDay'].some(f=>errors['params.'+f])){
    const sm=p.StartMonth,em=p.EndMonth,sd=p.StartDay||1,ed=p.EndDay||userMonthLength(em);
    const start=Date.UTC(2000,sm-1,sd),end=Date.UTC(2000,em-1,ed),window=end>=start?(end-start)/86400000+1:367-(start-end)/86400000;
    if(sm===em&&sd>ed)errors['params.EndDay']='End day must not precede start day within the same month';
    if(p.Duration!=null&&p.Duration>window)errors['params.Duration']='Duration is longer than the timing window';
    if(d.handler==='flow_handle_anytime'&&(sm!==7||sd!==1||em!==6||ed!==30))errors['params.StartMonth']='Across-year spells require a July–June whole-year window';
  }
  for(const f of ['Flow','Level']){
    const lo=p[f+'ThresholdMin'],hi=p[f+'ThresholdMax'];
    if(f==='Level'&&d.handler==='level_handle'&&(lo==null||hi==null))errors['params.LevelThresholdMin']='Both level limits are required';
    if(lo!=null&&hi!=null&&hi<=lo)errors['params.'+f+'ThresholdMax']='Upper limit must exceed lower limit';
  }
  if(p.TargetFrequency!=null&&((p.TargetFrequencyMin!=null&&p.TargetFrequencyMin>p.TargetFrequency)||(p.TargetFrequencyMax!=null&&p.TargetFrequencyMax<p.TargetFrequency)))errors['params.TargetFrequency']='Target must lie within the acceptable frequency range';
  if(d.kind==='V'){
    if(p.VolumeThreshold==null||p.VolumeThreshold<=0)errors['params.VolumeThreshold']='A positive volume is required';
    if(p.AccumulationPeriod==null||(p.Duration!=null&&p.AccumulationPeriod<p.Duration))errors['params.AccumulationPeriod']='AccumulationPeriod must be at least Duration';
  }
  if(d.handler.endsWith('_multi')){
    if(d.second_gauge===d.gauge||!gs.includes(d.second_gauge))errors.second_gauge='Choose a different review gauge with stored flows';
    else if(!d.id&&!(ctx.flowScenarios||[]).some(g=>g.includes(d.gauge)&&g.includes(d.second_gauge)))errors.second_gauge='Both gauges need stored flows in at least one uploaded scenario';
  }else if(d.second_gauge!=null)errors.second_gauge='Only multigauge handlers accept a second gauge';
  if(d.handler==='rise_and_fall_handle'){
    const rise=d.kind==='L'?['RateOfRiseRiverLevel']:['RateOfRiseMax1','RateOfRiseMax2','RateOfRiseThreshold1','RateOfRiseThreshold2'],fall=d.kind==='L'?'RateOfFallRiverLevel':'RateOfFallMin';
    const r=rise.some(f=>p[f]!=null),f=p[fall]!=null;if(r===f||(r&&rise.some(f=>p[f]==null)))errors['params.rates']='Supply all rise limits or one fall limit, for exactly one direction';
  }
  if(d.kind==='S'){
    const s=d.salinity;
    if(!s||!['EC','TDS'].includes(s.unit)||Object.keys(s).some(k=>!['unit','tds_factor'].includes(k)))errors.salinity='Specify EC or TDS and a positive TDS-to-EC factor';
    else{
      const raw=s.tds_factor??.6,factor=typeof raw==='number'||typeof raw==='string'&&raw.trim()!==''?Number(raw):NaN;
      if(!Number.isFinite(factor)||factor<=0)errors['salinity.tds_factor']='Factor must be finite and positive';
      else{
        if(tds)for(const k of ['FlowThresholdMin','FlowThresholdMax'])if(p[k]!=null){const value=p[k]*factor;if(Math.abs(value-Math.round(value))>1e-9)errors['params.'+k]='Converted EC must be a whole number (py_ewr threshold requirement)';else if(def.bounds[k]?.max!=null&&value>def.bounds[k].max)errors['params.'+k]='Converted EC is above the allowed maximum';else p[k]=Math.round(value);}
        d.salinity={unit:'EC',tds_factor:factor};
      }
    }
  }else if(d.salinity!=null)errors.salinity='Only salinity EWRs accept salinity units';
  d.params=p;d.name=typeof d.name==='string'?d.name.trim():d.name;
  return {ok:!Object.keys(errors).length,errors,spec:d};
}

// Portable files contain definitions only. Import always creates a draft, never an update to a server id.
const UEWR_FILE_FORMAT='ewr-user-definition',UEWR_FILE_LIMIT=64*1024;
const UEWR_FILE_FIELDS=['code','name','kind','handler','gauge','second_gauge','pus','objectives','regime','params','salinity'];
/** A shareable JSON file for a definition: its portable fields only (never server ids, versions or results). */
function exportDefinition(spec){
  const definition=Object.fromEntries(UEWR_FILE_FIELDS.map(k=>[k,userClone(spec[k]??null)]));
  return JSON.stringify({format:UEWR_FILE_FORMAT,version:1,spec:definition},null,2)+'\n';
}
/** Parse and validate an imported definition file, returning a draft spec; throws an Error with a readable reason. */
function parseDefinition(text,schema,ctx){
  const fail=message=>{throw Error('Cannot import EWR: '+message);};
  if(typeof text!=='string'||new TextEncoder().encode(text).length>UEWR_FILE_LIMIT)fail('choose a UTF-8 JSON file no larger than 64 KiB.');
  text=text.replace(/^\uFEFF/,'');
  let data;try{data=JSON.parse(text);}catch{fail('the file is not valid JSON. Export an EWR from this dashboard to get a valid example.');}
  // Scan the JSON tokens, rather than parsed objects, so duplicate and escaped dangerous keys cannot be hidden by JSON.parse.
  const stack=[];
  for(let i=0;i<text.length;i++){
    const c=text[i];
    if(c==='{'||c==='['){stack.push(c==='{'?new Set():null);if(stack.length>12)fail('the JSON is nested too deeply.');}
    else if(c==='}'||c===']')stack.pop();
    else if(c==='"'){
      const start=i++;for(;i<text.length;i++){if(text[i]==='\\')i++;else if(text[i]==='"')break;}
      let j=i+1;while(/\s/.test(text[j]||'')&&j<text.length)j++;
      if(text[j]===':'){
        const key=JSON.parse(text.slice(start,i+1)),keys=stack.at(-1);
        if(['__proto__','prototype','constructor'].includes(key))fail('unsafe property names are not allowed.');
        if(keys?.has(key))fail('duplicate property "'+key+'" is not allowed.');keys?.add(key);
      }
    }
  }
  const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
  if(!object(data)||Object.keys(data).some(k=>!['format','version','spec'].includes(k))||data.format!==UEWR_FILE_FORMAT||data.version!==1)fail('expected an ewr-user-definition file with version 1.');
  const s=data.spec;
  if(!object(s)||Object.keys(s).some(k=>!UEWR_FILE_FIELDS.includes(k)))fail('the definition contains unsupported fields. Server ids, versions and results cannot be imported.');
  const required=['code','name','kind','handler','gauge','pus','objectives','regime','params'];
  for(const k of required)if(!Object.hasOwn(s,k))fail('missing required field: '+k+'.');
  for(const k of ['code','name','kind','handler','gauge'])if(typeof s[k]!=='string')fail(k+' must be text.');
  for(const k of ['pus','objectives'])if(!Array.isArray(s[k])||s[k].some(x=>typeof x!=='string')||new Set(s[k]).size!==s[k].length)fail(k+' must be a list of unique text values.');
  for(const k of ['regime','second_gauge'])if(s[k]!=null&&typeof s[k]!=='string')fail(k+' must be text or null.');
  if(!['F','V','L','S'].includes(s.kind)||!Object.hasOwn(schema?.[s.kind]||{},s.handler))fail('the measurement kind or rule type is not supported.');
  if(!object(s.params))fail('params must be an object of parameter values.');
  for(const k of ['StartMonth','EndMonth','Duration','MinSpell','TargetFrequency'])if(!Object.hasOwn(s.params,k)||s.params[k]===null)fail('missing required parameter: '+k+'.');
  for(const [k,v] of Object.entries(s.params))if(v!==null&&(typeof v!=='number'||!Number.isFinite(v)))fail(k+' must be a finite JSON number or null.');
  if(s.kind==='S'){
    if(!object(s.salinity)||!['EC','TDS'].includes(s.salinity.unit)||typeof s.salinity.tds_factor!=='number'||!Number.isFinite(s.salinity.tds_factor)||s.salinity.tds_factor<=0)fail('salinity needs EC or TDS units and a positive numeric tds_factor.');
  }else if(s.salinity!=null)fail('salinity units are only valid for salinity EWRs.');
  // A code collision is resolved explicitly in the draft form; it never overwrites the existing definition.
  const checked=validateSpec(s,schema,{...ctx,specs:[]});
  if(!checked.ok)fail(Object.entries(checked.errors).map(([k,v])=>k+': '+v).join('; '));
  const canonical=validateSpec(checked.spec,schema,{...ctx,specs:[]});
  if(!canonical.ok)fail(Object.entries(canonical.errors).map(([k,v])=>k+': '+v).join('; '));
  return canonical.spec;
}

/** One-sentence plain-language summary of a definition's rule. */
function ruleSentence(s){
  const p=s.params||{},unit=userUnit(s),lo=p[s.kind==='L'?'LevelThresholdMin':'FlowThresholdMin'],hi=p[s.kind==='L'?'LevelThresholdMax':'FlowThresholdMax'];
  const range=lo!=null&&hi!=null?`${lo}–${hi} ${unit}`:lo!=null?`at least ${lo} ${unit}`:hi!=null?`at most ${hi} ${unit}`:'the specified range';
  return `${s.code}: ${s.kind==='V'?`${p.VolumeThreshold} ML over ${p.AccumulationPeriod} days`:range} at ${s.gauge}${s.second_gauge?' plus '+s.second_gauge:''}, for ${p.Duration} ${s.handler.startsWith('lowflow_handle')?'total':'consecutive'} days; minimum spell ${p.MinSpell} days, ${p.EventsPerYear??1} event(s) per year, window ${p.StartMonth}${p.StartDay?'.'+p.StartDay:''}–${p.EndMonth}${p.EndDay?'.'+p.EndDay:''}; target ${p.TargetFrequency}%, maximum gap ${p['MaxInter-event']??0} years.`;
}
/** A definition as an EWR guide row (the page's short parameter keys) for one planning unit. */
function guideRow(s,pu,base={}){
  const p=s.params;return {...base,key:`${s.gauge}|${s.code}|${pu}`,g:s.gauge,c:s.code,pu,k:s.kind,h:s.handler,u:true,user:s.id,name:s.name,eo:s.objectives.join('+'),
    sm:p.StartMonth,sd:p.StartDay,em:p.EndMonth,ed:p.EndDay,dur:p.Duration,ms:p.MinSpell,tmin:p.FlowThresholdMin,tmax:p.FlowThresholdMax,lmin:p.LevelThresholdMin,lmax:p.LevelThresholdMax,
    vol:p.VolumeThreshold,acc:p.AccumulationPeriod,mi:p['MaxInter-event'],tf:p.TargetFrequency,tfl:p.TargetFrequencyMin,tfh:p.TargetFrequencyMax,epy:p.EventsPerYear,gap:p.WithinEventGapTolerance,mg:s.second_gauge};
}
/** Result-table rows (ROWS layout, empty metrics) for a definition: one per planning unit and SDL unit it applies to. */
function synthRows(spec,meta,sheetIdx){
  const {rows=[],sheet=[]}=Array.isArray(sheetIdx)?{rows:sheetIdx}:sheetIdx||{},width=meta.policies.length*meta.climates.length,blank=()=>Array(width).fill(null),out=[];
  const gauge=meta.gauges.indexOf(spec.gauge);if(gauge<0)throw Error('Unknown review gauge '+spec.gauge);
  for(const pu of spec.pus){
    const pi=meta.pus.indexOf(pu);if(pi<0)throw Error('Unknown planning unit '+pu);
    let sdls=[...new Set(rows.filter(r=>!r.user&&r[0]===pi&&r[2]===gauge).map(r=>r[1]))];
    if(!sdls.length)sdls=[...new Set(sheet.filter(r=>r.g===spec.gauge&&r.pu===pu).map(r=>meta.sdls.indexOf(r.sdl)).filter(i=>i>=0))];
    if(!sdls.length)throw Error('No SDL mapping for '+pu+' / '+spec.gauge);
    for(const sdl of sdls){
      const objectives=spec.objectives.map(o=>meta.objs.indexOf(o)).filter(i=>i>=0),regime=meta.regimes.indexOf(spec.regime);
      const r=[pi,sdl,gauge,spec.code,objectives,regime<0?[]:[regime],blank(),blank(),{T:spec.params.TargetFrequency,ny:null,dur:spec.params.Duration,mrip:spec.params['MaxInter-event'],uewrVerAt:{}},blank(),null,null,null];
      r.user=spec.id;r.userVer=spec.ver;r.t=[...new Set(objectives.map(o=>meta.objTheme[o]))];if(!r.t.length)r.t=[meta.themes.indexOf('Unclassified')];out.push(r);
    }
  }return out;
}
/** Merge new result rows into a scenario's `vals`, replacing rows for the same (planning unit, gauge, EWR) and stamping the definition version. */
function mergeResults(vals,rows,specVer){
  const merged=new Map(vals.map(v=>[userResultKey(v),userClone(v)]));
  for(const row of rows){const v=Array.isArray(row)?userClone(row):['pu','gauge','ewr','fr','et','ey','ny','sev','dr','nm','win'].map(k=>row[k]??null),version=Array.isArray(row)?userVersion(row):row.uewr_ver;
    v[11]={uewrVer:version??specVer??null};merged.set(userResultKey(v),v);
  }return [...merged.values()];
}
/** Everything in the page state that refers to a definition (scenarios, rows, guide, flow and table selections), for edit or delete. */
function cascadePlan(spec,state){
  const matches=v=>v[1]===spec.gauge&&v[2]===spec.code,guidePrefix=spec.gauge+'|'+spec.code+'|';
  return {scenarioKeys:(state.scenarios||[]).filter(c=>c.vals.some(matches)).map(c=>c.key),rowIndices:(state.rows||[]).flatMap((r,i)=>r.user===spec.id?[i]:[]),
    guideKeys:(state.guideSelection||[]).filter(k=>k.startsWith(guidePrefix)),flowSelected:[state.flowEwr,state.eventEwr].some(k=>k?.startsWith(spec.code+'|')),
    thresholdKeys:(state.thresholdKeys||[]).filter(k=>k.startsWith('g:'+spec.code+'|')),
    cellSelected:!!state.cell&&(state.cell.code===spec.code||state.cell.ewr===spec.code||String(state.cell.c||'').startsWith('e'+spec.code+'|'))};
}
// ---- part 2: registry and dashboard state -------------------------------------------------------------------------
const UEWR=(()=>{
  const specs=new Map(),orphaned=new Set(),unmapped=new Set(),pendingDeletes=new Set(),retired=new Set(),statuses=new Map();
  let schema=null,online=false,loaded=false,syncing=null,epoch=0;
  const read=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}};
  const write=(key,value)=>{try{localStorage.setItem(key,JSON.stringify(value));}catch{}};
  const persist=()=>{write(UEWR_KEY,[...specs.values()]);write(UEWR_DELETED_KEY,[...pendingDeletes]);};
  const sheet=()=>window.ewrGuideApi?.rows?.filter(r=>!r.u)||ewrRows(EWRIDX);
  const context=()=>{
    const pairs={};for(const r of ROWS)if(!r.user)(pairs[META.gauges[r[2]]]??=new Set()).add(META.pus[r[0]]);
    const table=sheet();for(const g of Object.keys(pairs))pairs[g]=[...pairs[g]].filter(pu=>table.some(r=>r.g===g&&r.pu===pu));
    return {gauges:META.gauges,pusByGauge:pairs,objs:META.objs,regimes:META.regimes,sheetCodes:table.map(r=>r.c),specs:[...specs.values()].filter(s=>!orphaned.has(s.id)),flowScenarios:CUSTOM.filter(c=>!c.removed&&c.id).map(c=>c.flowGauges||[])};
  };
  const announce=()=>{if(typeof window.dispatchEvent==='function')window.dispatchEvent(new CustomEvent('uewr-change'));};
  const invalidate=gauge=>{performanceRevision++;flowsChanged();if(gauge)EWRLIST.delete(gauge);if(typeof invalidateInventory==='function')invalidateInventory(gauge);};
  const redraw=()=>{if(typeof refresh==='function')refresh();announce();};
  const scenarioMode=()=>S.src==='user'||S.src==='observed';
  const assertUser=()=>{if(!scenarioMode())throw Error('User EWRs are available only with Your data or Observed scenarios.');};
  const assertOnline=()=>{assertUser();if(!online||!schema)throw Error('Connect to the local analysis server to change or evaluate user EWRs.');};
  async function request(path,options){
    const response=await fetch(path,{cache:'no-store',...options}),payload=await response.json();
    if(!response.ok){const error=Error(payload.message||payload.error||Object.values(payload.errors||{}).join('; ')||'User EWR request failed');error.errors=payload.errors||{};throw error;}
    return payload;
  }
  const post=(path,body,options={})=>request(path,{...options,method:'POST',headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
  function clearSelections(spec){
    const plan=cascadePlan(spec,{scenarios:CUSTOM,rows:ROWS,guideSelection:window.ewrGuide?.getSel?.()||read('ewrGuideSel',[]),flowEwr:S.flow.ewr,eventEwr:S.ev.ewr,thresholdKeys:S.flow.thr,cell:S.cell});
    if(plan.flowSelected){if(S.flow.ewr.startsWith(spec.code+'|'))S.flow.ewr='';if(S.ev.ewr.startsWith(spec.code+'|'))S.ev.ewr='';}
    S.flow.thr=S.flow.thr.filter(k=>!plan.thresholdKeys.includes(k));if(plan.cellSelected)S.cell=null;
    const selected=read('ewrGuideSel',[]).filter(k=>!plan.guideKeys.includes(k));write('ewrGuideSel',selected);window.ewrGuide?.setSel?.(selected);
    return plan;
  }
  function clearStatuses(spec){for(const key of statuses.keys())if(key.endsWith('|'+spec.code))statuses.delete(key);}
  function removeRows(spec,preserveGuide=false){
    for(let i=ROWS.length-1;i>=0;i--)if(ROWS[i].user===spec.id)ROWS.splice(i,1);
    delete META.ewrThr[spec.gauge+'|'+spec.code];reindexRows();
    window.ewrGuide?.removeRows?.(spec.pus.map(pu=>`${spec.gauge}|${spec.code}|${pu}`),{preserveSelection:preserveGuide});
  }
  function replay(spec){
    for(const c of CUSTOM)if(!c.removed)for(const v of c.vals||[])if(v[1]===spec.gauge&&v[2]===spec.code){
      for(const r of PUKEY.get(userResultKey(v))||[])putVals(r,c.P,v);
    }
  }
  function addRows(spec){
    const rows=synthRows(spec,META,{rows:ROWS,sheet:sheet()});
    removeRows(spec,true);ROWS.push(...rows);
    const threshold=spec.params[spec.kind==='L'?'LevelThresholdMin':'FlowThresholdMin'];if(threshold!=null)META.ewrThr[spec.gauge+'|'+spec.code]=threshold;
    reindexRows();replay(spec);
    window.ewrGuide?.addRows?.(spec.pus.map(pu=>guideRow(spec,pu,sheet().find(r=>r.g===spec.gauge&&r.pu===pu)||{})));
  }
  function loadLocal(){
    if(loaded)return;loaded=true;
    const deleted=read(UEWR_DELETED_KEY,[]);if(Array.isArray(deleted))for(const id of deleted)if(typeof id==='string'&&/^[a-z0-9_]{1,24}$/.test(id))pendingDeletes.add(id);
    const mirror=read(UEWR_KEY,[]);if(!Array.isArray(mirror))return;
    for(const s of mirror){
      if(!s||typeof s.id!=='string'||!/^[a-z0-9_]{1,24}$/.test(s.id)||pendingDeletes.has(s.id)||!CODE_RE.test(s.code)||!Array.isArray(s.pus)||!Array.isArray(s.objectives)||!s.params||!(s.params.MinSpell>=1&&s.params.MinSpell<=s.params.Duration))continue;
      try{addRows(s);specs.set(s.id,userClone(s));}catch{/* Invalid or unmappable local mirrors do not stop the dashboard loading. */}
    }
  }
  async function retryDelete(id){
    try{await post('/api/uewr/delete?id='+encodeURIComponent(id));pendingDeletes.delete(id);}
    catch(error){if(error.errors?.id==='User EWR does not exist')pendingDeletes.delete(id);else throw error;}
    finally{persist();}
  }
  async function sync(){
    loadLocal();if(syncing)return syncing;
    const generation=epoch;
    syncing=(async()=>{
      try{
        const [definitions,nextSchema]=await Promise.all([request('/api/uewr'),request('/api/uewr/schema')]);
        if(!Array.isArray(definitions.specs)||!nextSchema.F)throw Error('Invalid user EWR server response');
        online=true;schema=nextSchema;
        const deletedAtStart=new Set(pendingDeletes);
        for(const id of [...pendingDeletes])try{await retryDelete(id);}catch{/* Keep tombstones for the next sync. */}
        if(generation!==epoch)return false;
        const received=new Set();
        for(const s of definitions.specs){
          if(pendingDeletes.has(s.id)||deletedAtStart.has(s.id))continue;
          received.add(s.id);
          // One definition this page cannot map must not take the registry offline: keep it orphaned so it can still be deleted.
          try{
          for(const previous of [...specs.values()])if(previous.id!==s.id&&previous.code===s.code){
            clearSelections(previous);clearStatuses(previous);for(const c of CUSTOM)c.vals=c.vals.filter(v=>v[1]!==previous.gauge||v[2]!==previous.code);
            removeRows(previous);specs.delete(previous.id);orphaned.delete(previous.id);retired.add(previous.id);invalidate(previous.gauge);
          }
          const old=specs.get(s.id);
          if(old&&userIdentity(old)!==userIdentity(s)){
            clearSelections(old);for(const c of CUSTOM)c.vals=c.vals.filter(v=>v[1]!==old.gauge||v[2]!==old.code);removeRows(old);invalidate(old.gauge);
          }
          if(!old||unmapped.has(s.id)||JSON.stringify(old)!==JSON.stringify(s)){if(old)clearStatuses(old);addRows(s);specs.set(s.id,userClone(s));invalidate(s.gauge);}
          orphaned.delete(s.id);unmapped.delete(s.id);
          }catch{
            const stale=specs.get(s.id);if(stale)try{removeRows(stale);}catch{/* Rows were never added. */}
            specs.set(s.id,userClone(s));orphaned.add(s.id);unmapped.add(s.id);
          }
        }
        for(const id of specs.keys())if(!received.has(id)){orphaned.add(id);unmapped.delete(id);}
        persist();persistCustom();redraw();return true;
      }catch(error){online=false;announce();return false;}
      finally{syncing=null;}
    })();return syncing;
  }
  async function save(draft){
    assertOnline();const lost=orphaned.has(draft.id)&&!unmapped.has(draft.id)?specs.get(draft.id):null;const input=userClone(draft);if(lost)delete input.id;const check=validateSpec(input,schema,context());if(!check.ok){const error=Error('Fix the highlighted definition fields.');error.errors=check.errors;throw error;}
    const {spec}=await post('/api/uewr',check.spec);
    if(retired.has(spec.id)||pendingDeletes.has(spec.id))throw Error('This user EWR was deleted while saving.');
    const latest=specs.get(spec.id);if(latest&&latest.ver>spec.ver)return userClone(latest);
    epoch++;
    if(lost){if(userIdentity(lost)!==userIdentity(spec)){clearSelections(lost);for(const c of CUSTOM)c.vals=c.vals.filter(v=>v[1]!==lost.gauge||v[2]!==lost.code);}else for(const c of CUSTOM)for(const v of c.vals)if(v[1]===lost.gauge&&v[2]===lost.code)v[11]={uewrVer:null};removeRows(lost);specs.delete(lost.id);orphaned.delete(lost.id);}
    const old=specs.get(spec.id);
    if(old&&userIdentity(old)!==userIdentity(spec)){
      clearSelections(old);for(const c of CUSTOM)c.vals=c.vals.filter(v=>v[1]!==old.gauge||v[2]!==old.code);removeRows(old);invalidate(old.gauge);
    }
    if(old)clearStatuses(old);if(lost)clearStatuses(lost);
    specs.set(spec.id,userClone(spec));addRows(spec);orphaned.delete(spec.id);unmapped.delete(spec.id);persist();persistCustom();invalidate(spec.gauge);redraw();return spec;
  }
  function applyResults(job,evaluated=null){
    const current=code=>{const s=[...specs.values()].find(s=>s.code===code);return s&&!orphaned.has(s.id)&&(!evaluated||evaluated.get(s.id)===s.ver)?s:null;};
    for(const [scn,rows] of Object.entries(job.results||{})){
      const c=CUSTOM.find(c=>!c.removed&&c.id===scn);if(!c)continue;
      const valid=rows.filter(row=>{const spec=current(row.ewr);return spec&&spec.gauge===row.gauge&&spec.pus.includes(row.pu)&&row.uewr_ver===spec.ver;});
      c.vals=mergeResults(c.vals,valid);
      for(const row of valid){const v=mergeResults([], [row])[0];for(const r of PUKEY.get(userResultKey(v))||[]){putVals(r,c.P,v);if(!c.sdls.includes(META.sdls[r[1]]))c.sdls.push(META.sdls[r[1]]);}}
      for(const row of valid)statuses.delete(scn+'|'+row.ewr);
    }
    for(const family of ['skipped','errors'])for(const [scn,codes] of Object.entries(job[family]||{}))for(const [code,reason] of Object.entries(codes))if(current(code)&&CUSTOM.some(c=>!c.removed&&c.id===scn))statuses.set(scn+'|'+code,{state:family,reason});
    persistCustom();performanceRevision++;flowsChanged();EWRLIST.clear();if(typeof invalidateInventory==='function')invalidateInventory();redraw();
  }
  async function run(ids,scns,onProgress=()=>{}){
    assertOnline();const selected=ids.map(id=>specs.get(id));if(selected.some(s=>!s||orphaned.has(s.id)))throw Error('Re-save orphaned user EWRs before evaluating them.');
    const allowed=new Set(CUSTOM.filter(c=>!c.removed&&c.id).map(c=>c.id));if(!scns.length||scns.some(id=>!allowed.has(id)))throw Error('Select uploaded scenarios with stored series.');
    const evaluated=new Map(selected.map(s=>[s.id,s.ver]));
    const {job}=await post('/api/uewr/run',{ids,scns});
    for(;;){
      await new Promise(resolve=>setTimeout(resolve,500));const result=await request('/api/job?id='+encodeURIComponent(job));
      onProgress(result.progress,result.elapsed);if(result.status==='running')continue;
      if(result.status!=='done')throw Error(result.message||'User EWR evaluation failed');applyResults(result,evaluated);return result;
    }
  }
  async function remove(id){
    assertUser();const spec=specs.get(id);if(!spec)return;
    epoch++;retired.add(id);const plan=clearSelections(spec);for(const c of CUSTOM)c.vals=c.vals.filter(v=>v[1]!==spec.gauge||v[2]!==spec.code);
    clearStatuses(spec);removeRows(spec);specs.delete(id);orphaned.delete(id);unmapped.delete(id);pendingDeletes.add(id);persist();persistCustom();invalidate(spec.gauge);redraw();
    if(online)try{await retryDelete(id);}catch{/* Local deletion is complete; the tombstone prevents resurrection after a failed request. */}
    return {...plan,pending:pendingDeletes.has(id)};
  }
  function atGauge(gauge){
    if(!scenarioMode())return [];
    return [...specs.values()].filter(s=>s.gauge===gauge&&!orphaned.has(s.id)).flatMap(s=>s.pus.map(pu=>({code:s.code,pu,pus:[pu],kind:s.kind,custom:true,user:true,name:s.name,handler:s.handler,
      consecutive:!s.handler.startsWith('lowflow_handle'),thr:s.params[s.kind==='L'?'LevelThresholdMin':'FlowThresholdMin']??0,thr_max:s.params[s.kind==='L'?'LevelThresholdMax':'FlowThresholdMax'],dur:s.params.Duration,sm:s.params.StartMonth,em:s.params.EndMonth,target:s.params.TargetFrequency,maxint:s.params['MaxInter-event'],detailed:['F','V'].includes(s.kind)})));
  }
  function offerRun(scns){const available=scns.filter(id=>CUSTOM.some(c=>!c.removed&&c.id===id));if(!specs.size||!available.length||!scenarioMode())return null;api.pendingScenarios=available;announce();return {ids:[...specs.keys()].filter(id=>!orphaned.has(id)),scns:available};}
  const api={specs,orphaned,unmapped,pendingDeletes,statuses,get schema(){return schema;},get online(){return online;},context,loadLocal,sync,save,run,applyResults,remove,addRows,removeRows,atGauge,offerRun,pendingScenarios:[],request,post};
  return api;
})();
if(typeof window!=='undefined')window.UEWR=UEWR;
// ---- part 3: guide controls and configuration form ----------------------------------------------------------------
(()=>{
  const add=document.getElementById('uewrAdd'),list=document.getElementById('uewrList');if(!add||!list)return;
  const scenarioMode=()=>S.src==='user'||S.src==='observed';
  const KINDS={F:'Flow',V:'Accumulated volume',L:'Level',S:'Salinity'};
  const RULES={flow_handle:'Continuous spell',lowflow_handle:'Total days in the window',ctf_handle:'Cease-to-flow spell',flow_handle_anytime:'Spell across 30 June',flow_handle_check_ctf:'Spell after a dry period',cumulative_handle:'Accumulated volume',rise_and_fall_handle:'Rate of rise or fall',flow_handle_multi:'Continuous spell: two gauges',lowflow_handle_multi:'Total days: two gauges',ctf_handle_multi:'Cease to flow: two gauges',cumulative_handle_multi:'Accumulated volume: two gauges',level_handle:'Level spell with drawdown limit'};
  const STEPS_USER=[['Reading series','Read stored daily series'],['Evaluating','Evaluate your EWRs'],['Summarising','Summarise results']];
  const dialog=document.createElement('dialog');dialog.id='uewrDlg';dialog.className='uewr-dialog';dialog.setAttribute('aria-labelledby','uewrTitle');document.body.append(dialog);
  let draft=null,original=null,busy=false,minAuto=true,notice='',noticeTone='success',deleteId=null,previewKey='',previewSignature='',formEpoch=0,previewController=null,tutorial=null,tutorialRAF=0;
  const pendingActions=new Set();
  const importInput=document.createElement('input');importInput.type='file';importInput.id='uewrImportFile';importInput.accept='.json,application/json';importInput.hidden=true;document.body.append(importInput);
  const toast=document.createElement('div');toast.id='uewrToast';toast.className='uewr-toast';toast.hidden=true;toast.innerHTML='<span></span><button type="button" class="ghost" aria-label="Dismiss notification" title="Dismiss notification">×</button>';document.body.append(toast);
  toast.querySelector('button').onclick=()=>{toast.hidden=true;};
  const el=id=>document.getElementById(id),live=()=>CUSTOM.filter(c=>!c.removed&&c.id);
  const errorText=e=>(Object.entries(e.errors||{}).map(([k,v])=>k+': '+v).join('\n')||e.message||'Something went wrong. Please try again.').slice(0,1200);
  function formStatus(text,tone='info'){const node=el('uewrStatus');if(!node)return;node.textContent=text;node.dataset.tone=tone;node.setAttribute('role',tone==='error'?'alert':'status');}
  const showNotice=(text,tone='success')=>{notice=text;noticeTone=tone;toast.querySelector('span').textContent=text;toast.dataset.tone=tone;toast.setAttribute('role',tone==='error'?'alert':'status');toast.hidden=!scenarioMode();render();};
  const safeAsync=fn=>{Promise.resolve().then(fn).catch(e=>{if(dialog.open&&el('uewrStatus'))formStatus(errorText(e),'error');else showNotice(errorText(e),'error');});};
  async function actionTask(key,work){if(pendingActions.has(key))return;pendingActions.add(key);render();try{return await work();}finally{pendingActions.delete(key);render();}}
  const currentForm=token=>dialog.open&&token===formEpoch;
  function stopTutorial(){if(tutorialRAF)cancelAnimationFrame(tutorialRAF);tutorialRAF=0;if(tutorial)tutorial.playing=false;const play=el('uewrTutorialPlay');if(play){play.textContent='Play';play.setAttribute('aria-pressed','false');}}
  function paintTutorial(progress=1){
    if(!tutorial||!el('uewrTutorialSvg'))return;const stage=tutorial.steps[tutorial.index];
    el('uewrTutorialSvg').innerHTML=stage.draw(progress);el('uewrTutorialStage').textContent=`Step ${tutorial.index+1} of ${tutorial.steps.length} · ${stage.title}`;
    if(el('uewrTutorialCaption').dataset.index!==String(tutorial.index)){el('uewrTutorialCaption').innerHTML=stage.cap;el('uewrTutorialCaption').dataset.index=tutorial.index;}
    el('uewrTutorialPrev').disabled=tutorial.index===0;el('uewrTutorialNext').disabled=tutorial.index===tutorial.steps.length-1;
  }
  function tutorialTick(now){
    tutorialRAF=0;if(!tutorial?.playing||!dialog.open||document.hidden)return stopTutorial();
    const stage=tutorial.steps[tutorial.index],elapsed=now-tutorial.start;paintTutorial(Math.min(1,elapsed/stage.ms));
    if(elapsed>=stage.ms+750){if(tutorial.index===tutorial.steps.length-1)return stopTutorial();tutorial.index++;tutorial.start=now;}
    tutorialRAF=requestAnimationFrame(tutorialTick);
  }
  function renderTutorial(spec){
    stopTutorial();tutorial=null;const target=el('uewrTutorial'),api=window.ewrGuideApi;
    if(!['flow_handle','lowflow_handle'].includes(spec.handler)||!['F','S'].includes(spec.kind)||!api){
      target.innerHTML='<p class="note">Read the timing window, thresholds, minimum spell and target above. This advanced rule has no illustrative animation; Test on one scenario uses py_ewr to check its full rule against your daily series.</p>';return;
    }
    const row=guideRow(spec,spec.pus[0]),illustration=api.ewrSpec(row);
    const unavailable=()=>{target.textContent='This rule does not fit the illustrative 365-day record. Use Test on one scenario to evaluate the full rule, including leap days and the complete timing window.';};
    if(illustration.dur!==spec.params.Duration||illustration.ms!==spec.params.MinSpell){unavailable();return;}
    let model;try{model={...api.buildTutorial(illustration),row};}catch{unavailable();return;}
    if(!model.metEx||!model.missEx||!Object.values(model.L||{}).every(Number.isFinite)||model.record.some(y=>y.flow.some(v=>!Number.isFinite(v)))){unavailable();return;}
    tutorial={steps:api.stages(model),index:0,playing:false,start:0};
    target.innerHTML='<div class="uewr-tutorial"><b id="uewrTutorialStage" aria-live="polite"></b><svg id="uewrTutorialSvg" viewBox="0 0 520 300" role="img" aria-label="Illustrative EWR rule animation"></svg><p id="uewrTutorialCaption"></p><div class="uewr-actions"><button type="button" id="uewrTutorialPrev" data-tutorial="prev" title="Show the previous explanation step">Previous</button><button type="button" id="uewrTutorialPlay" data-tutorial="play" aria-pressed="false" title="Play or pause the illustrative rule explanation">Play</button><button type="button" id="uewrTutorialNext" data-tutorial="next" title="Show the next explanation step">Next</button><button type="button" data-tutorial="replay" title="Restart the explanation">Replay</button></div><small>Illustrative examples generated from this rule, not your gauge data. Test on one scenario gives evaluated results. Reduced-motion settings are respected.</small></div>';paintTutorial();
  }
  dialog.addEventListener('close',()=>{if(dialog.open)return;formEpoch++;previewController?.abort();previewController=null;busy=false;stopTutorial();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopTutorial();});
  const uploadedGauges=kind=>new Set(live().flatMap(c=>c[kind==='L'?'levelGauges':kind==='S'?'salinityGauges':'flowGauges']||[]));
  function scenarioState(s,c){
    const values=c.vals.filter(v=>v[1]===s.gauge&&v[2]===s.code),status=UEWR.statuses.get(c.id+'|'+s.code);
    if(status)return (status.state==='skipped'?'Skipped':'Could not evaluate')+': '+status.reason;
    return values.length?values.every(v=>userVersion(v)===s.ver)?'evaluated':'stale':'not run';
  }
  function render(){
    const opened=new Set([...list.querySelectorAll('details.uewr-item[open]')].map(x=>x.dataset.spec));const visible=scenarioMode();add.hidden=!visible;list.hidden=!visible;
    if(!visible){toast.hidden=true;if(dialog.open)dialog.close();return;}
    add.disabled=!UEWR.online||!UEWR.schema||!live().length;
    add.title=add.disabled?'Upload a scenario and connect to the local analysis server to define user EWRs.':'Define an EWR for your uploaded scenarios';
    const scns=live(),specs=[...UEWR.specs.values()];
    list.innerHTML=`<div class="uewr-heading"><h3>Your EWRs (${specs.length})</h3><div class="uewr-actions"><button class="ghost" data-u="import" title="Open an exported JSON definition as a new draft"${!UEWR.online||!UEWR.schema||!scns.length?' disabled':''}>Import JSON</button><button class="ghost" data-u="sync" title="Reconnect and reconcile definitions with the local server"${pendingActions.has('sync')?' disabled':''}>${pendingActions.has('sync')?'Connecting…':'Reconnect / sync'}</button></div></div>`+
      (!UEWR.online?'<p class="note">Offline: saved definitions and results are available to read. Start the local analysis server to save or run EWRs.</p>':'')+
      (notice?`<p class="uewr-notice" data-tone="${noticeTone}">${esc(notice)}</p>`:'')+
      (UEWR.pendingScenarios.length?`<p class="uewr-offer">Evaluate your EWRs on the newly uploaded scenario? <button data-u="offer">Run</button> <button class="ghost" data-u="dismiss">Dismiss</button></p>`:'')+
      specs.map(s=>{
        const states=scns.map(c=>scenarioState(s,c)),ok=states.filter(x=>x==='evaluated').length,stale=states.includes('stale'),orphan=UEWR.orphaned.has(s.id);
        return `<details class="uewr-item" data-spec="${esc(s.id)}"${opened.has(s.id)?' open':''}><summary><b>★ ${esc(s.code)}</b> · ${esc(s.name)}<small>${esc(KINDS[s.kind])} · ${esc(s.gauge)} · ${UEWR.unmapped.has(s.id)?'cannot be shown on this dashboard: delete it, or edit its gauge or planning units':orphan?'orphaned: re-save required':stale?'stale · re-run':ok+'/'+scns.length+' scenarios evaluated'}</small></summary>
          <p>${esc(ruleSentence(s))}</p>${scns.map(c=>`<div>${esc(c.name)}: ${esc(scenarioState(s,c))}</div>`).join('')}
          <div class="uewr-actions"><button data-u="run" data-id="${esc(s.id)}"${!UEWR.online||orphan||!scns.length||pendingActions.has('run:'+s.id)?' disabled':''} title="Evaluate this definition on all uploaded scenarios">${pendingActions.has('run:'+s.id)?'Running…':stale?'Re-run':'Run all'}</button><button class="ghost" data-u="edit" data-id="${esc(s.id)}"${!UEWR.online?' disabled':''}>${orphan?'Re-save':'Edit'}</button><button class="ghost" data-u="duplicate" data-id="${esc(s.id)}"${!UEWR.online?' disabled':''}>Duplicate</button><button class="ghost" data-u="export" data-id="${esc(s.id)}" title="Download this definition as JSON; scenario data and results are excluded">Export JSON</button><button class="ghost" data-u="delete" data-id="${esc(s.id)}" title="Remove this definition and its results from every view">Delete</button></div>
          ${deleteId===s.id?`<p role="alert">Delete ${esc(s.code)}? This removes its rows and results from ${scns.filter(c=>c.vals.some(v=>v[1]===s.gauge&&v[2]===s.code)).length} scenarios and clears its chart and guide selections.</p><button data-u="confirm-delete" data-id="${esc(s.id)}">Delete EWR</button> <button class="ghost" data-u="cancel-delete">Cancel</button>`:''}</details>`;
      }).join('')+(specs.length?'':'<p class="note">Add a flow, level or salinity rule and evaluate it on your uploaded scenarios.</p>');
  }
  UEWR.renderUI=render;window.addEventListener('uewr-change',render);
  list.addEventListener('click',event=>{
    const button=event.target.closest('[data-u]');if(!button)return;const action=button.dataset.u,id=button.dataset.id,s=UEWR.specs.get(id);
    if(action==='sync')return safeAsync(()=>actionTask('sync',async()=>{await UEWR.sync();showNotice(UEWR.online?'Connected. Your EWR definitions are up to date.':'The local analysis server is unavailable. Start it, then reconnect.',UEWR.online?'success':'error');}));
    if(action==='import'){importInput.value='';importInput.click();return;}
    if(action==='export')return safeAsync(async()=>{
      if(!s)return;const blob=new Blob([exportDefinition(s)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');
      link.href=url;link.download=s.code+'.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);showNotice('Exported '+s.code+'. The file contains its definition only.');
    });
    if(action==='edit')return openForm(s);
    if(action==='duplicate'){const copy=userClone(s);delete copy.id;delete copy.ver;delete copy.created;delete copy.updated;let n=2,stem=copy.code.split('_')[1].slice(0,20);while([...UEWR.specs.values()].some(x=>x.code===`U_${stem}_${n}`))n++;copy.code=`U_${stem}_${n}`;return openForm(copy);}
    if(action==='delete'){deleteId=id;render();const details=list.querySelector(`[data-u="confirm-delete"][data-id="${id}"]`)?.closest('details');if(details)details.open=true;return;}
    if(action==='cancel-delete'){deleteId=null;return render();}
    if(action==='confirm-delete')return safeAsync(()=>actionTask('delete:'+id,async()=>{await UEWR.remove(id);deleteId=null;showNotice('Deleted '+s.code+(UEWR.pendingDeletes.has(id)?'; server deletion will retry after reconnecting.':'.'),UEWR.pendingDeletes.has(id)?'warning':'success');}));
    if(action==='dismiss'){UEWR.pendingScenarios=[];return render();}
    if(action==='run'||action==='offer')return safeAsync(()=>actionTask('run:'+(id||'offer'),async()=>{
      const ids=action==='run'?[id]:[...UEWR.specs.keys()].filter(k=>!UEWR.orphaned.has(k)),scns=action==='run'?live().map(c=>c.id):UEWR.pendingScenarios.slice();
      showNotice('Evaluating user EWRs…','info');const job=await UEWR.run(ids,scns,p=>showNotice(p?`${p.stage}: ${p.done}/${p.total}`:'Evaluating…','info'));
      const partial=Object.keys(job.skipped||{}).length||Object.keys(job.errors||{}).length;UEWR.pendingScenarios=[];showNotice('Evaluation complete.'+(partial?' Some scenarios were skipped or failed; expand the EWR for details.':''),partial?'warning':'success');
    }));
  });
  importInput.addEventListener('change',()=>safeAsync(async()=>{
    const file=importInput.files?.[0];if(!file)return;
    if(file.size>UEWR_FILE_LIMIT)throw Error('Cannot import EWR: choose a JSON file no larger than 64 KiB.');
    let text;try{text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());}catch{throw Error('Cannot import EWR: the file must use UTF-8 text encoding.');}
    if(!scenarioMode()||!UEWR.online||!UEWR.schema||!live().length)throw Error('Connect to the local server and select a scenario before importing.');
    const imported=parseDefinition(text,UEWR.schema,UEWR.context());openForm(imported);
    const collision=[...UEWR.specs.values()].some(s=>s.code===imported.code);
    formStatus(collision?'Imported as a new draft. This code already exists; choose a new U_ code before saving.':'Imported as a new draft. Review the rule, test a scenario, then Save or Save & run.','info');
  }));
  const option=(value,label,selected=false,disabled=false)=>`<option value="${esc(value)}"${selected?' selected':''}${disabled?' disabled':''}>${esc(label)}</option>`;
  function openForm(s){
    if(!scenarioMode()||!UEWR.online||!UEWR.schema||!live().length)return;
    formEpoch++;previewController?.abort();previewController=null;stopTutorial();previewKey='';previewSignature='';
    original=s?.id?userClone(s):null;draft=s?userClone(s):{code:'U_',name:'',kind:'F',handler:'flow_handle',gauge:'',pus:[],objectives:[],regime:null,second_gauge:null,params:{Duration:5,TargetFrequency:50,MinSpell:5},salinity:null};
    minAuto=!s?.params?.MinSpell;busy=false;
    const context=UEWR.context();if(!draft.gauge)draft.gauge=context.gauges.find(g=>uploadedGauges(draft.kind).has(g))||'';
    if(!draft.pus.length)draft.pus=(context.pusByGauge[draft.gauge]||[]).slice(0,1);
    dialog.innerHTML=`<form id="uewrForm" novalidate><div class="uewr-heading"><h2 id="uewrTitle">${original?'Edit':'New'} EWR</h2><button type="button" class="ghost" data-form="close" aria-label="Close EWR form">×</button></div>
      <div class="uewr-layout"><nav aria-label="Configuration steps">${['Basics','Rule type','Parameters','Review & run'].map((name,i)=>`<button type="button" data-step="${i+1}">${i+1}. ${name} <span id="uewrStep${i+1}"></span></button>`).join('')}</nav><div class="uewr-sections">
      <fieldset id="uewrSection1"><legend>1. Basics</legend><div class="uewr-fields"><label>What does it measure?<select id="uewrKind">${Object.entries(KINDS).map(([k,v])=>option(k,v,draft.kind===k)).join('')}</select></label><label>Gauge<select id="uewrGauge"></select><small data-error="gauge"></small></label>
      <label>Code<input id="uewrCode" maxlength="31" value="${esc(draft.code)}" placeholder="U_FRESH1" required><small data-error="code"></small></label><label>Name<input id="uewrName" maxlength="80" value="${esc(draft.name)}" required><small data-error="name"></small></label></div>
      <div id="uewrPus"></div><small data-error="pus"></small><div class="uewr-fields"><label>Objectives<select id="uewrObjectives" multiple size="4">${META.objs.map(o=>option(o,o,draft.objectives.includes(o))).join('')}</select><small>Leave empty for Unclassified.</small></label><label>Flow regime<select id="uewrRegime">${option('','Unclassified')+META.regimes.map(r=>option(r,r,draft.regime===r)).join('')}</select></label><label>Start from an existing EWR<select id="uewrCopy"></select></label></div></fieldset>
      <fieldset id="uewrSection2"><legend>2. Rule type</legend><div id="uewrRules" class="uewr-rules" role="radiogroup" aria-label="Rule type"></div><label id="uewrSecondWrap" hidden>Second gauge<select id="uewrSecond"></select><small data-error="second_gauge"></small></label><p class="note">Specialised barrage, Coorong, nesting and weir-pool rules use site-specific logic and are not offered at arbitrary gauges.</p></fieldset>
      <fieldset id="uewrSection3"><legend>3. Parameters</legend><div id="uewrSalinity" class="uewr-fields" hidden><label>Input salinity units<select id="uewrUnit"><option value="EC">EC (µS/cm)</option><option value="TDS">TDS (mg/L)</option></select></label><label>TDS → EC multiplier<input id="uewrFactor" type="number" step="any" min="0" value="${esc(draft.salinity?.tds_factor??.6)}"><small data-error="salinity.tds_factor"></small></label></div><div id="uewrParams"></div><small data-error="params.rates"></small><small data-error="salinity"></small></fieldset>
      <fieldset id="uewrSection4"><legend>4. Review & run</legend><p id="uewrSentence"></p><div id="uewrTutorial"></div><p id="uewrIdentityWarning" class="note"></p><div id="uewrScenarios">${live().map(c=>`<label class="uewr-check"><input type="checkbox" data-scenario="${esc(c.id)}" checked> ${esc(c.name)}</label>`).join('')}</div><label>Preview scenario<select id="uewrPreviewScenario">${live().map(c=>option(c.id,c.name)).join('')}</select></label><div class="uewr-actions"><button type="button" id="uewrPreview" title="Evaluate this unsaved definition on the chosen scenario without changing dashboard results">Test on one scenario</button><button type="button" id="uewrPreviewCancel" class="ghost" hidden>Cancel test</button><button type="submit" id="uewrSave">Save</button><button type="button" id="uewrSaveRun">Save & run</button><button type="button" class="ghost" data-form="close">Cancel</button></div><p class="note">Tests use stored daily series and do not save the definition or its results. Save & run evaluates the scenarios checked above.</p><p id="uewrStatus" role="status" class="uewr-notice"></p><div id="uewrPreviewResults"></div><ol id="uewrSteps" class="steps" hidden></ol></fieldset>
      </div></div></form>`;
    el('uewrUnit').value=draft.salinity?.unit||'EC';populateGauge();renderRules();renderParams();if(!dialog.open)dialog.showModal();validate();el('uewrCode').focus();
  }
  UEWR.openForm=openForm;add.onclick=()=>openForm();
  function populateGauge(){
    const context=UEWR.context(),available=uploadedGauges(draft.kind);
    el('uewrGauge').innerHTML=option('','Choose a gauge')+context.gauges.filter(g=>context.pusByGauge[g]?.length).map(g=>option(g,g+' · '+(GAUGES.find(x=>x.id===g)?.name||''),g===draft.gauge,!available.has(g)&&g!==draft.gauge)).join('');
    el('uewrPus').innerHTML='<b>Planning units</b>'+(context.pusByGauge[draft.gauge]||[]).map(pu=>`<label class="uewr-check"><input type="checkbox" data-pu="${esc(pu)}"${draft.pus.includes(pu)?' checked':''}> ${esc(pu)}</label>`).join('');
    el('uewrSecond').innerHTML=option('','Choose a second gauge')+context.gauges.filter(g=>g!==draft.gauge&&context.pusByGauge[g]?.length).map(g=>option(g,g,g===draft.second_gauge,!uploadedGauges('F').has(g))).join('');
    const candidates=window.ewrGuideApi.rows.filter(r=>!r.u&&r.g===draft.gauge&&UEWR.schema[r.k]?.[r.h]);
    el('uewrCopy').innerHTML=option('','Choose an existing EWR')+candidates.map(r=>option(r.key,r.c+' · '+r.pu)).join('');
  }
  function renderRules(){
    if(!UEWR.schema[draft.kind][draft.handler])draft.handler=Object.keys(UEWR.schema[draft.kind])[0];
    el('uewrRules').innerHTML=Object.keys(UEWR.schema[draft.kind]).map(h=>`<button type="button" data-handler="${h}" role="radio" aria-checked="${h===draft.handler}" class="${h===draft.handler?'on':''}">${esc(RULES[h]||h)}</button>`).join('');
    el('uewrSecondWrap').hidden=!draft.handler.endsWith('_multi');el('uewrSalinity').hidden=draft.kind!=='S';
  }
  function renderParams(){
    const def=UEWR.schema[draft.kind][draft.handler],old=draft.params;draft.params={};
    const groups={Timing:[],Thresholds:[],Events:[],Volume:[],Rates:[],Targets:[]};
    for(const f of def.fields){
      let v=old[f];if(v==null||v==='')v=def.defaults[f]??'';if(f==='MinSpell'&&(v===''||v==='Duration'||minAuto))v=draft.handler.startsWith('lowflow_handle')?1:Number(old.Duration||5);
      draft.params[f]=v;const b=def.bounds[f]||{},group=f.startsWith('Start')||f.startsWith('End')?'Timing':f.startsWith('Target')||f==='MaxInter-event'?'Targets':f.includes('Threshold')&&!f.startsWith('Rate')?'Thresholds':f==='VolumeThreshold'||f==='AccumulationPeriod'?'Volume':f.startsWith('Rate')||f==='DrawdownRate'?'Rates':'Events';
      const label=f.startsWith('FlowThreshold')&&draft.kind==='S'?(f.endsWith('Min')?'Lower':'Upper')+' salinity limit (input units above)':f.startsWith('LevelThreshold')?(def.labels[f]||f)+' (m)':def.labels[f]||f;
      groups[group].push(`<label>${esc(label)}${f.endsWith('Month')?`<select data-param="${f}">${MONTHS.map((m,i)=>option(i+1,m,Number(v)===i+1)).join('')}</select>`:`<input type="number" data-param="${f}" value="${esc(v)}"${b.min!=null?` min="${b.min}"`:''}${b.max!=null?` max="${b.max}"`:''} step="${b.integer?'1':'any'}"${['Duration','MinSpell','TargetFrequency'].includes(f)?' required':''}>`}<small data-error="params.${f}"></small>${f==='MinSpell'?`<small id="uewrMinHelp">Default: ${draft.handler.startsWith('lowflow_handle')?'1 day for total-day rules':'Duration for spell rules'}. Always at least 1 and at most Duration.</small>`:f==='MaxInter-event'?'<small>0 means no maximum gap; ETP and MRIP metrics remain empty.</small>':''}</label>`);
    }
    el('uewrParams').innerHTML=Object.entries(groups).filter(([,fields])=>fields.length).map(([name,fields])=>`<h4>${name}</h4><div class="uewr-fields">${fields.join('')}</div>`).join('');
  }
  function collect(){
    const s=userClone(draft);s.code=el('uewrCode').value.trim();s.name=el('uewrName').value.trim();s.gauge=el('uewrGauge').value;s.pus=[...dialog.querySelectorAll('[data-pu]:checked')].map(x=>x.dataset.pu);
    s.objectives=[...el('uewrObjectives').selectedOptions].map(x=>x.value);s.regime=el('uewrRegime').value||null;s.second_gauge=s.handler.endsWith('_multi')?el('uewrSecond').value||null:null;
    for(const field of dialog.querySelectorAll('[data-param]'))s.params[field.dataset.param]=field.value;
    s.salinity=s.kind==='S'?{unit:el('uewrUnit').value,tds_factor:el('uewrFactor').value}:null;return s;
  }
  function validate(){
    if(!draft)return null;const raw=collect(),check=validateSpec(raw,UEWR.schema,UEWR.context());
    if(raw.params.MinSpell===''){check.errors['params.MinSpell']='Enter the minimum spell shown in the default hint.';check.ok=false;}
    for(const node of dialog.querySelectorAll('[data-error]'))node.textContent=check.errors[node.dataset.error]||'';
    for(const input of dialog.querySelectorAll('[data-param]'))input.setAttribute('aria-invalid',String(!!check.errors['params.'+input.dataset.param]));
    for(const id of ['uewrPreview','uewrSave','uewrSaveRun'])el(id).disabled=busy||!check.ok||!UEWR.online||!scenarioMode();
    const selected=dialog.querySelectorAll('[data-scenario]:checked').length;el('uewrSaveRun').disabled||=!selected;
    el('uewrSentence').textContent=check.ok?ruleSentence(check.spec):'Complete the required fields to review your EWR.';
    if(check.ok){const key=JSON.stringify(check.spec);if(key!==previewKey){previewKey=key;renderTutorial(check.spec);}}
    else {stopTutorial();tutorial=null;previewKey='';el('uewrTutorial').textContent='Complete the required fields to see the rule guidance.';}
    if(previewSignature&&previewSignature!==JSON.stringify(check.spec)){previewSignature='';el('uewrPreviewResults').replaceChildren();formStatus('Definition changed. Test again to update the preview.');}
    for(const input of dialog.querySelectorAll('input,select,[data-handler]'))input.disabled=busy;
    dialog.setAttribute('aria-busy',String(busy));el('uewrPreviewCancel').hidden=!previewController;
    el('uewrPreview').textContent=previewController?'Testing…':'Test on one scenario';
    const compatible=live().filter(c=>(c[raw.kind==='L'?'levelGauges':raw.kind==='S'?'salinityGauges':'flowGauges']||[]).includes(raw.gauge)&&(!raw.second_gauge||(c.flowGauges||[]).includes(raw.second_gauge)));
    const chosen=el('uewrPreviewScenario').value;el('uewrPreviewScenario').innerHTML=compatible.map(c=>option(c.id,c.name,c.id===chosen)).join('');
    el('uewrPreview').disabled||=!compatible.length;
    el('uewrPreview').title=compatible.length?'Evaluate this unsaved definition without saving it or changing dashboard results':'Upload a scenario with matching daily series to test this rule';
    el('uewrSave').textContent=original&&live().some(c=>c.vals.some(v=>v[1]===original.gauge&&v[2]===original.code))?'Save & re-run previous scenarios':'Save';
    el('uewrIdentityWarning').textContent=original&&userIdentity({...raw,pus:raw.pus||[]})!==userIdentity(original)?'Changing code, gauge or planning units removes this EWR’s previous rows and scenario results.':'';
    for(let i=1;i<=4;i++){const keys=Object.keys(check.errors),bad=keys.some(k=>i===1?['code','name','gauge','pus','objectives','regime'].includes(k):i===2?['kind','handler','second_gauge'].includes(k):i===3?k.startsWith('params')||k.startsWith('salinity'):true);el('uewrStep'+i).textContent=bad?'⚠':'✓';}
    return check;
  }
  dialog.addEventListener('input',event=>{
    if(event.target.dataset.param==='MinSpell')minAuto=false;
    if(event.target.dataset.param==='Duration'&&minAuto){const field=dialog.querySelector('[data-param="MinSpell"]');if(field)field.value=draft.handler.startsWith('lowflow_handle')?1:event.target.value;}
    validate();
  });
  dialog.addEventListener('change',event=>{
    if(event.target.id==='uewrKind'){draft=collect();draft.kind=event.target.value;draft.handler=Object.keys(UEWR.schema[draft.kind])[0];draft.second_gauge=null;minAuto=true;if(!uploadedGauges(draft.kind).has(draft.gauge)){draft.gauge=UEWR.context().gauges.find(g=>uploadedGauges(draft.kind).has(g))||'';draft.pus=(UEWR.context().pusByGauge[draft.gauge]||[]).slice(0,1);}populateGauge();renderRules();renderParams();}
    if(event.target.id==='uewrGauge'){draft=collect();draft.gauge=event.target.value;draft.pus=(UEWR.context().pusByGauge[draft.gauge]||[]).slice(0,1);populateGauge();}
    if(event.target.id==='uewrCopy'&&event.target.value){
      const r=window.ewrGuideApi.rows.find(r=>r.key===event.target.value),mapping={StartMonth:'sm',StartDay:'sd',EndMonth:'em',EndDay:'ed',Duration:'dur',MinSpell:'ms',FlowThresholdMin:'tmin',FlowThresholdMax:'tmax',LevelThresholdMin:'lmin',LevelThresholdMax:'lmax',VolumeThreshold:'vol',AccumulationPeriod:'acc',EventsPerYear:'epy',WithinEventGapTolerance:'gap',TargetFrequency:'tf',TargetFrequencyMin:'tfl',TargetFrequencyMax:'tfh','MaxInter-event':'mi'};
      draft=collect();draft.kind=r.k;draft.handler=r.h;draft.params={};for(const f of UEWR.schema[r.k][r.h].fields)if(mapping[f]&&r[mapping[f]]!=null)draft.params[f]=r[mapping[f]];
      draft.params.Duration=Math.max(1,draft.params.Duration||5);draft.params.MinSpell=Math.max(1,draft.params.MinSpell||1);el('uewrKind').value=r.k;minAuto=false;renderRules();renderParams();
    }validate();
  });
  async function submit(run){
    const check=validate();if(!check.ok)return;const token=formEpoch,selected=[...dialog.querySelectorAll('[data-scenario]:checked')].map(x=>x.dataset.scenario);busy=true;validate();
    try{
      const previous=original?live().filter(c=>c.vals.some(v=>v[1]===original.gauge&&v[2]===original.code)).map(c=>c.id):[];
      formStatus('Saving your definition…');const saved=await UEWR.save(check.spec);
      if(currentForm(token)){original=userClone(saved);draft=userClone(saved);}
      const scns=run?selected:previous;
      if(scns.length){if(currentForm(token))formStatus('Evaluating the selected scenarios…');const job=await UEWR.run([saved.id],scns,(p,t)=>{if(p&&currentForm(token))showSteps(p.stage,p,t,STEPS_USER,'uewrSteps');});showNotice('Saved '+saved.code+'; evaluation complete.'+(Object.keys(job.skipped||{}).length||Object.keys(job.errors||{}).length?' Expand the EWR to review skips or errors.':''));}
      else showNotice('Saved '+saved.code+'. Run it on uploaded scenarios when ready.');if(currentForm(token))dialog.close();
    }catch(error){if(currentForm(token))formStatus(errorText(error),'error');else showNotice(errorText(error),'error');}finally{if(currentForm(token)){busy=false;validate();}}
  }
  dialog.addEventListener('submit',event=>{event.preventDefault();safeAsync(()=>submit(false));});
  dialog.addEventListener('click',event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.dataset.form==='close'){dialog.close();return;}
    if(button.dataset.step){el('uewrSection'+button.dataset.step).scrollIntoView({block:'start',behavior:'smooth'});return;}
    if(button.dataset.handler){draft=collect();draft.handler=button.dataset.handler;minAuto=true;renderRules();renderParams();validate();return;}
    if(button.id==='uewrSaveRun')return safeAsync(()=>submit(true));
    if(button.dataset.tutorial){
      if(!tutorial)return;const command=button.dataset.tutorial;
      if(command==='prev'||command==='next'){stopTutorial();tutorial.index=Math.max(0,Math.min(tutorial.steps.length-1,tutorial.index+(command==='next'?1:-1)));paintTutorial();return;}
      if(command==='play'&&tutorial.playing){stopTutorial();return;}
      stopTutorial();if(command==='replay'||tutorial.index===tutorial.steps.length-1)tutorial.index=0;
      if(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches){stopTutorial();paintTutorial();return;}
      tutorial.playing=true;tutorial.start=performance.now();el('uewrTutorialPlay').textContent='Pause';el('uewrTutorialPlay').setAttribute('aria-pressed','true');tutorialRAF=requestAnimationFrame(tutorialTick);return;
    }
    if(button.id==='uewrPreviewCancel'){previewController?.abort();return;}
    if(button.id==='uewrPreview')return safeAsync(async()=>{
      const check=validate();if(!check.ok||busy)return;const token=formEpoch,scn=el('uewrPreviewScenario').value;if(!scn)return;
      const controller=new AbortController();previewController=controller;busy=true;el('uewrPreviewResults').replaceChildren();validate();formStatus('Testing on '+live().find(c=>c.id===scn).name+'… This may take a moment.');
      try{
        const result=await UEWR.post('/api/uewr/preview',{spec:check.spec,scn},{signal:controller.signal});if(!currentForm(token))return;
        const rows=result.rows?.length?result.rows:result.row?[result.row]:[],fmt=(n,dp=2)=>Number.isFinite(n)?n.toLocaleString(undefined,{maximumFractionDigits:dp}):'–';
        formStatus(rows.length?'Preview: test complete. The definition and results have not been saved.':'No result was returned. Check that the scenario contains this rule’s daily series.',rows.length?'success':'warning');
        if(rows.length)el('uewrPreviewResults').innerHTML='<div class="uewr-preview-table"><table><caption>Unsaved test · '+esc(live().find(c=>c.id===scn)?.name||scn)+'</caption><tr><th>Planning unit</th><th title="Achieved frequency divided by target frequency">FR</th><th>Achieved years</th><th title="Percentage of record beyond the maximum inter-event period">Beyond MRIP</th></tr>'+rows.map(r=>`<tr><td>${esc(r.pu||'–')}</td><td>${fmt(r.fr,3)}</td><td>${fmt(r.ey,0)} / ${fmt(r.ny,0)}</td><td>${r.et==null?'–':fmt(r.et*100,1)+'%'}</td></tr>`).join('')+'</table></div>';
        previewSignature=JSON.stringify(check.spec);
      }catch(error){if(currentForm(token))formStatus(error.name==='AbortError'?'Test cancelled. Your definition has not been saved.':errorText(error),error.name==='AbortError'?'info':'error');}
      finally{if(currentForm(token)){previewController=null;busy=false;validate();}}
    });
  });
  render();
})();
