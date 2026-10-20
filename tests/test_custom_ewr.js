const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),{test}=require('node:test');
const source=fs.readFileSync('src/ewr_analysis/custom_ewr.js','utf8'),parts=source.split('// ---- part 2: registry and dashboard state');
const fixtures=JSON.parse(fs.readFileSync('tests/fixtures/uewr_validation_cases.json','utf8'));
const ctx=vm.createContext({TextEncoder});vm.runInContext(parts[0]+';this.api={validateSpec,synthRows,mergeResults,cascadePlan,guideRow,ruleSentence,exportDefinition,parseDefinition,UEWR_FILE_LIMIT};',ctx);
const api=ctx.api,plain=x=>JSON.parse(JSON.stringify(x)),clone=x=>JSON.parse(JSON.stringify(x));
for(const c of fixtures.cases)test('validation parity: '+c.name,()=>{
  const s={...clone(fixtures.base),...c.patch,params:{...fixtures.base.params,...c.patch.params}};
  const before=JSON.stringify(s),r=api.validateSpec(s,fixtures.schema,fixtures.context);
  assert.deepEqual(Object.keys(r.errors).sort(),c.errors.slice().sort());assert.equal(r.ok,c.errors.length===0);assert.equal(JSON.stringify(s),before);
  if(r.ok)assert.ok(r.spec.params.MinSpell>=1&&r.spec.params.MinSpell<=r.spec.params.Duration);
});
const metadata=()=>({ewrThr:{},droughts:[],policies:['review','uploaded'],climates:['climate1','climate2'],gauges:['404216'],pus:[fixtures.base.pus[0]],sdls:['one','two'],objs:['NF1'],regimes:['Small fresh'],themes:['Fish','Unclassified'],objTheme:[0]});
const spec=()=>({...clone(fixtures.base),id:'u_one',ver:1,params:{...fixtures.base.params,MinSpell:5,EventsPerYear:1,StartMonth:7,EndMonth:6}});
test('synthRows sizes late rows for every current scenario and shares SDL mappings without mutating inputs',()=>{
 const m=metadata(),s=spec(),review=[[0,0,0,'F1',[],[],[],[]],[0,1,0,'F1',[],[],[],[]]],before=JSON.stringify([m,s,review]);
 const r=api.synthRows(s,m,{rows:review});assert.equal(r.length,2);
 assert.deepEqual(plain(r.map(x=>x[1])),[0,1]);for(const x of r){assert.equal(x[6].length,4);assert.equal(x[7].length,4);assert.equal(x[9].length,4);assert.equal(x.user,'u_one');assert.deepEqual(plain(x.t),[1]);}
 r[0][6][0]=1;assert.equal(r[1][6][0],null);assert.equal(JSON.stringify([m,s,review]),before);
 m.policies.push('another');assert.equal(api.synthRows(s,m,{rows:review})[0][6].length,6);
});
test('synthRows uses the parameter sheet SDL fallback and rejects missing hierarchy mappings',()=>{
 const m=metadata(),s=spec();assert.equal(api.synthRows(s,m,{rows:[],sheet:[{g:s.gauge,pu:s.pus[0],sdl:'two'}]})[0][1],1);
 assert.throws(()=>api.synthRows(s,m,[]),/No SDL mapping/);
});
test('result replacement preserves unrelated rows and serialises the evaluated spec version',()=>{
 const old=[['PU','404216','U_A',.2],['PU','404216','F1',1]],before=JSON.stringify(old);
 const out=api.mergeResults(old,[{pu:'PU',gauge:'404216',ewr:'U_A',fr:1,uewr_ver:3}],3);
 assert.equal(out.length,2);assert.equal(out[0][3],1);assert.deepEqual(plain(out[0][11]),{uewrVer:3});assert.equal(JSON.stringify(old),before);
 assert.equal(JSON.parse(JSON.stringify(out))[0][11].uewrVer,3);
});
test('delete cascade identifies results, all SDL rows and both chart selections',()=>{
 const s=spec(),v=[s.pus[0],s.gauge,s.code];const r=api.cascadePlan(s,{scenarios:[{key:'a',vals:[v]}],rows:[{user:s.id},{user:s.id}],guideSelection:[`${s.gauge}|${s.code}|${s.pus[0]}`],eventEwr:s.code+'|'+s.pus[0],thresholdKeys:['g:'+s.code+'|200|7|6'],cell:{code:s.code}});
 assert.deepEqual(plain(r.scenarioKeys),['a']);assert.deepEqual(plain(r.rowIndices),[0,1]);assert.equal(r.guideKeys.length,1);assert.equal(r.flowSelected,true);assert.equal(r.cellSelected,true);assert.equal(r.thresholdKeys.length,1);
});

const template=fs.readFileSync('src/ewr_analysis/dashboard_template.html','utf8');
function registryHarness({mirror=[],storedScenarios=[],definitions=[],fail=false}={}){
  const memory=new Map([['ewrUserEwrs',JSON.stringify(mirror)],['ewrCustomScenarios',JSON.stringify(storedScenarios)]]),server=new Map(definitions.map(s=>[s.id,clone(s)]));
  const h={fail,server,requests:[],memory},window={ewrGuideApi:{rows:[{g:'404216',c:'F1',pu:fixtures.base.pus[0],sdl:'one'}]},dispatchEvent(){}};
  const c=vm.createContext({fixtureMeta:metadata(),window,CustomEvent:class{},setTimeout:fn=>{fn();return 1;},localStorage:{getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v)},
    fetch:async(path,options={})=>{
      h.requests.push([path,options]);if(h.fail)throw Error('offline');let payload={};
      if(path==='/api/uewr'&&options.method==='POST'){const s=JSON.parse(options.body),previous=server.get(s.id);s.id??='u_saved';s.ver=(previous?.ver||0)+1;server.set(s.id,s);payload={spec:s};}
      else if(path==='/api/uewr')payload={specs:[...server.values()].map(clone)};
      else if(path==='/api/uewr/schema')payload=fixtures.schema;
      else if(path.startsWith('/api/uewr/delete')){server.delete(new URL('http://test'+path).searchParams.get('id'));payload={ok:true};}
      if(path==='/api/uewr'&&options.method==='POST'&&h.saveGate)await h.saveGate();
      return {ok:true,json:async()=>payload};
    },URL});
  vm.runInContext(`const META=fixtureMeta,NC=META.climates.length,ROWS=[[0,0,0,'F1',[],[],Array(4).fill(null),Array(4).fill(null),{},Array(4).fill(null)]],CUSTOM=[],POL_L={};
    const S={src:'user',flow:{ewr:'',thr:[]},ev:{ewr:''},cell:null},EWRLIST=new Map();let performanceRevision=0;
    function flowsChanged(){}function refresh(){}function ewrRows(){return window.ewrGuideApi.rows;}`,c);
  const indices=template.slice(template.indexOf('// ---- row indices:'),template.indexOf('// ---- end row indices'));
  vm.runInContext(indices,c);
  vm.runInContext(template.slice(template.indexOf('function putVals('),template.indexOf('function removeCustom(')),c);
  vm.runInContext(source.split('// ---- part 3: guide controls')[0],c);
  h.run=code=>vm.runInContext(code,c);h.api=h.run('UEWR');h.ctx=c;return h;
}
test('local definitions restore before scenario values, so U_ results survive a reload',()=>{
 const s=spec(),v=[s.pus[0],s.gauge,s.code,1,.1,4,25,null,null,null,null,{uewrVer:1}];
 const h=registryHarness({mirror:[s],storedScenarios:[{name:'saved',id:'a',vals:[v],flowGauges:[s.gauge]}]});
 h.run('UEWR.loadLocal();restoreCustom();');
 assert.equal(h.run('CUSTOM[0].vals.length'),1);assert.equal(h.run('ROWS.find(r=>r.user)[6][CUSTOM[0].P*NC]'),1);
 assert.equal(h.run('ROWS.find(r=>r.user)[8].uewrVerAt[CUSTOM[0].P*NC]'),1);
});
test('server sync marks missing local definitions orphaned and stays usable offline',async()=>{
 const h=registryHarness({mirror:[spec()]});assert.equal(await h.api.sync(),true);assert.equal(h.api.orphaned.has('u_one'),true);
 h.fail=true;assert.equal(await h.api.sync(),false);assert.equal(h.api.online,false);assert.equal(h.api.specs.size,1);
});
test('save, evaluated results, edits and stale responses preserve versions without mutating incoming results',async()=>{
 const h=registryHarness();await h.api.sync();const s=await h.api.save(fixtures.base);
 h.run("addCustom('uploaded',[],'a',['404216']);");
 const job={results:{a:[{pu:s.pus[0],gauge:s.gauge,ewr:s.code,fr:.8,et:.1,uewr_ver:s.ver}]}};
 const before=JSON.stringify(job);h.api.applyResults(job);assert.equal(JSON.stringify(job),before);
 assert.equal(h.run('CUSTOM[0].vals[0][11].uewrVer'),1);
 s.params.Duration=6;const edited=await h.api.save(s);assert.equal(edited.ver,2);
 assert.equal(h.run('ROWS.find(r=>r.user).userVer'),2);assert.equal(h.run('ROWS.find(r=>r.user)[8].uewrVerAt[CUSTOM[0].P*NC]'),1);
 h.api.applyResults(job);assert.equal(h.run('CUSTOM[0].vals[0][11].uewrVer'),1);
 const newer=clone(job);newer.results.a[0].uewr_ver=2;h.api.applyResults(newer);assert.equal(h.run('CUSTOM[0].vals[0][11].uewrVer'),2);
});
test('delete cascades locally, queues retry after server failure and never resurrects a tombstoned definition',async()=>{
 const h=registryHarness({definitions:[spec()]});await h.api.sync();h.run("addCustom('uploaded',[],'a',['404216']);S.flow.ewr='U_FRESH1|PU';S.ev.ewr='U_FRESH1|PU';S.cell={c:'eU_FRESH1|0|0'};");
 h.fail=true;await h.api.remove('u_one');assert.equal(h.api.specs.size,0);assert.equal(h.run('ROWS.some(r=>r.user)'),false);assert.equal(h.run('S.ev.ewr'),'');assert.equal(h.run('S.cell'),null);assert.equal(h.api.pendingDeletes.has('u_one'),true);
 h.fail=false;await h.api.sync();assert.equal(h.api.specs.size,0);assert.equal(h.api.pendingDeletes.size,0);assert.equal(h.server.size,0);
});
test('Demo mode rejects frontend mutations and exposes no user EWR chart options',async()=>{
 const h=registryHarness({definitions:[spec()]});await h.api.sync();h.run("S.src='demo'");
 assert.deepEqual(plain(h.api.atGauge('404216')),[]);await assert.rejects(h.api.save(fixtures.base),/Your data/);await assert.rejects(h.api.run(['u_one'],['a']),/Your data/);
});
test('Observed mode supports defining and offering custom EWRs on observed scenarios',async()=>{
 const h=registryHarness();await h.api.sync();h.run("S.src='observed';addCustom('Observed – one',[],'obs_one',[], 'Observed data',{source:'observed',observedSdl:'one'})");
 const saved=await h.api.save(fixtures.base);assert.ok(saved.id);
 assert.deepEqual(plain(h.api.offerRun(['obs_one'])),{ids:[saved.id],scns:['obs_one']});
});

test('threshold deletion matches the complete user code and leaves sibling codes selected',()=>{
 const s=spec();const keys=['g:'+s.code+'|200|7|6','g:U_OTHER|200|7|6','g:U|200|7|6'];
 assert.deepEqual(plain(api.cascadePlan(s,{thresholdKeys:keys}).thresholdKeys),[keys[0]]);
});
test('a user EWR exposes every planning unit to detailed charts',async()=>{
 const h=registryHarness({definitions:[spec()]});await h.api.sync();h.api.specs.get('u_one').pus.push('Second planning unit');
 const list=plain(h.api.atGauge('404216'));assert.equal(list.length,2);assert.deepEqual(list.map(e=>e.pu),[spec().pus[0],'Second planning unit']);
 assert.ok(list.every(e=>e.pus.length===1));
});
test('cascade purges scenario values, metadata, statuses and all indices while preserving unrelated rows',async()=>{
 const s=spec(),other={...spec(),id:'u_other',code:'U_OTHER'};const h=registryHarness({definitions:[s,other]});await h.api.sync();
 h.run("addCustom('uploaded',[],'a',['404216']);S.flow.thr=['g:U_FRESH1|200|7|6','g:U_OTHER|200|7|6'];UEWR.statuses.set('a|U_FRESH1',{state:'errors',reason:'old'});");
 h.api.applyResults({results:{a:[s,other].map(s=>({pu:s.pus[0],gauge:s.gauge,ewr:s.code,fr:.8,et:.1,uewr_ver:1}))}});
 const removed=await h.api.remove(s.id);assert.equal(removed.pending,false);assert.equal(h.server.has(s.id),false);
 assert.equal(h.run("CUSTOM[0].vals.some(v=>v[2]==='U_FRESH1')"),false);assert.equal(h.run("CUSTOM[0].vals.some(v=>v[2]==='U_OTHER')"),true);
 assert.equal(h.run("Object.keys(META.ewrThr).some(k=>k.endsWith('|U_FRESH1'))"),false);assert.equal(h.run("[...PUKEY.keys()].some(k=>k.endsWith('|U_FRESH1'))"),false);
 assert.equal(h.run("Object.values(gaugeRows).flat().some(r=>r[3]==='U_FRESH1')"),false);assert.equal(h.run("Object.values(sdlRows).flat().some(r=>r[3]==='U_FRESH1')"),false);
 assert.deepEqual(plain(h.run('S.flow.thr')),['g:U_OTHER|200|7|6']);assert.equal(h.api.statuses.size,0);
 const delayed={results:{a:[{pu:s.pus[0],gauge:s.gauge,ewr:s.code,fr:1,uewr_ver:1}]},skipped:{a:{[s.code]:'deleted during evaluation'}}};h.api.applyResults(delayed,new Map([[s.id,1]]));
 assert.equal(h.api.statuses.size,0);assert.equal(h.run('CUSTOM[0].vals.length'),1);
 assert.ok(!h.memory.get('ewrCustomScenarios').includes('U_FRESH1'));assert.ok(!h.memory.get('ewrUserEwrs').includes('U_FRESH1'));
});
test('edits and reruns replace indices and reject obsolete job errors and invalid planning units',async()=>{
 const s=spec(),h=registryHarness({definitions:[s]});await h.api.sync();h.run("addCustom('uploaded',[],'a',['404216']);");
 h.api.applyResults({results:{a:[{pu:s.pus[0],gauge:s.gauge,ewr:s.code,fr:.7,uewr_ver:1}]}});
 const edited=await h.api.save({...s,params:{...s.params,Duration:6}});
 h.api.applyResults({errors:{a:{[s.code]:'old error'}}},new Map([[s.id,1]]));assert.equal(h.api.statuses.size,0);
 h.api.applyResults({results:{a:[{pu:'invalid',gauge:s.gauge,ewr:s.code,fr:2,uewr_ver:2},{pu:s.pus[0],gauge:s.gauge,ewr:s.code,fr:1.1,uewr_ver:2}]}},new Map([[s.id,2]]));
 assert.equal(edited.ver,2);assert.equal(h.run('CUSTOM[0].vals.length'),1);assert.equal(h.run('CUSTOM[0].vals[0][3]'),1.1);
 assert.equal(h.run('ROWS.filter(r=>r.user).length'),1);assert.equal(h.run('[...PUKEY.values()].flat().filter(r=>r.user).length'),1);
});


test('a save response arriving after deletion cannot resurrect a definition or its indices',async()=>{
 const s=spec(),h=registryHarness({definitions:[s]});await h.api.sync();let release;h.saveGate=()=>new Promise(resolve=>{release=resolve;});
 const saving=h.api.save({...s,params:{...s.params,Duration:6}});await new Promise(resolve=>setImmediate(resolve));await h.api.remove(s.id);release();
 await assert.rejects(saving,/deleted while saving/);assert.equal(h.api.specs.size,0);assert.equal(h.run('ROWS.some(r=>r.user)'),false);assert.equal(h.server.size,0);
});
test('one unmappable server definition stays deletable and does not take the registry offline',async()=>{
 const good=spec(),bad={...spec(),id:'u_bad',code:'U_BAD',pus:['Planning unit missing from this dashboard']};
 const h=registryHarness({definitions:[good,bad]});assert.equal(await h.api.sync(),true);assert.equal(h.api.online,true);
 assert.equal(h.api.specs.size,2);assert.equal(h.api.orphaned.has('u_bad'),true);assert.equal(h.api.unmapped.has('u_bad'),true);assert.equal(h.api.orphaned.has('u_one'),false);
 assert.equal(h.run("ROWS.filter(r=>r.user==='u_one').length"),1);assert.equal(h.run("ROWS.some(r=>r.user==='u_bad')"),false);
 assert.equal(await h.api.sync(),true);assert.equal(h.api.unmapped.has('u_bad'),true);assert.equal(h.api.orphaned.has('u_bad'),true);
 // Re-saving unchanged is stopped by validation before any request; the definition stays orphaned.
 const posts=()=>h.requests.filter(([path,o])=>path==='/api/uewr'&&o.method==='POST').length,before=posts();
 await assert.rejects(h.api.save(h.api.specs.get('u_bad')),error=>!!error.errors?.pus);assert.equal(posts(),before);assert.equal(h.api.orphaned.has('u_bad'),true);
 // Fixing its planning units updates the same server id rather than creating a duplicate code.
 const fixed=await h.api.save({...h.api.specs.get('u_bad'),pus:good.pus});assert.equal(fixed.id,'u_bad');assert.equal(h.server.size,2);
 assert.equal(h.api.unmapped.has('u_bad'),false);assert.equal(h.api.orphaned.has('u_bad'),false);assert.equal(h.run("ROWS.filter(r=>r.user==='u_bad').length"),1);
 await h.api.remove('u_bad');assert.equal(h.server.has('u_bad'),false);assert.equal(h.api.specs.has('u_bad'),false);assert.equal(h.api.unmapped.size,0);
});
test('authoritative sync replaces an orphaned identity with the same code without duplicate indices',async()=>{
 const old=spec(),fresh={...spec(),id:'u_fresh'};const h=registryHarness({mirror:[old],definitions:[fresh]});await h.api.sync();
 assert.equal(h.api.specs.size,1);assert.equal(h.api.specs.has('u_fresh'),true);assert.equal(h.run('ROWS.filter(r=>r.user).length'),1);
 assert.equal(h.run('[...PUKEY.values()].flat().filter(r=>r.user).length'),1);
});


const portable=()=>JSON.parse(api.exportDefinition(spec()));
const readPortable=data=>api.parseDefinition(typeof data==='string'?data:JSON.stringify(data),fixtures.schema,fixtures.context);
test('portable definitions round-trip without server identity, timestamps, results or input mutation',()=>{
 const s={...spec(),created:'today',updated:'later',results:{a:'private'}},before=JSON.stringify(s),encoded=api.exportDefinition(s),data=JSON.parse(encoded),out=readPortable(encoded);
 assert.equal(data.format,'ewr-user-definition');assert.equal(data.version,1);assert.equal(JSON.stringify(s),before);
 for(const key of ['id','ver','created','updated','results'])assert.equal(Object.hasOwn(data.spec,key),false);
 assert.equal(out.code,s.code);assert.equal(out.params.MinSpell,s.params.MinSpell);assert.equal(Object.hasOwn(out,'id'),false);
});
for(const [name,mutate,message] of [
 ['unknown format',d=>d.format='other',/version 1/],['unsupported version',d=>d.version=2,/version 1/],
 ['envelope array',d=>[],/version 1/],['missing spec',d=>delete d.spec,/unsupported fields/],
 ['unknown envelope key',d=>d.url='https://example.invalid',/version 1/],
 ['server identity',d=>d.spec.id='u_stolen',/unsupported fields/],['embedded results',d=>d.spec.results={},/unsupported fields/],
 ['missing name',d=>delete d.spec.name,/missing required field: name/],['invalid text type',d=>d.spec.name={},/name must be text/],
 ['invalid code',d=>d.spec.code='BAD-CODE',/code:/],['unknown gauge',d=>d.spec.gauge='unknown',/gauge:/],['inherited gauge name',d=>d.spec.gauge='constructor',/gauge:/],
 ['invalid planning unit',d=>d.spec.pus=['unknown'],/pus:/],['duplicate planning unit',d=>d.spec.pus.push(d.spec.pus[0]),/unique/],
 ['invalid objectives',d=>d.spec.objectives='NF1',/unique/],['duplicate objective',d=>d.spec.objectives=['NF1','NF1'],/unique/],
 ['unknown objective',d=>d.spec.objectives=['NOPE'],/objectives:/],['invalid regime type',d=>d.spec.regime={},/regime must be/],
 ['inherited kind name',d=>d.spec.kind='constructor',/not supported/],['inherited handler name',d=>d.spec.handler='toString',/not supported/],
 ['invalid params type',d=>d.spec.params=[],/params must be/],['missing duration',d=>delete d.spec.params.Duration,/missing required parameter: Duration/],
 ['missing MinSpell',d=>delete d.spec.params.MinSpell,/missing required parameter: MinSpell/],['null MinSpell',d=>d.spec.params.MinSpell=null,/missing required parameter: MinSpell/],
 ['zero MinSpell',d=>d.spec.params.MinSpell=0,/params.MinSpell/],['long MinSpell',d=>d.spec.params.MinSpell=6,/params.MinSpell/],
 ['numeric string',d=>d.spec.params.Duration='5',/finite JSON number/],['boolean number',d=>d.spec.params.Duration=true,/finite JSON number/],
 ['unknown parameter',d=>d.spec.params.FilePath='/tmp/evil',/FilePath/],['missing window',d=>delete d.spec.params.StartMonth,/missing required parameter/],
 ['non-salinity units',d=>d.spec.salinity={unit:'EC',tds_factor:.6},/only valid/],
 ['incomplete salinity',d=>{d.spec.kind='S';d.spec.salinity={unit:'EC'};},/salinity needs/],
 ['invalid salinity factor',d=>{d.spec.kind='S';d.spec.salinity={unit:'EC',tds_factor:0};},/salinity needs/],
])test('JSON import rejects '+name,()=>{
 const d=portable(),replacement=mutate(d);assert.throws(()=>readPortable(name==='envelope array'?replacement:d),message);
});
for(const [name,text,message] of [
 ['malformed JSON','{bad',/not valid JSON/],['empty JSON','',/not valid JSON/],['null root','null',/version 1/],
 ['oversize content',' '.repeat(65537),/64 KiB/],['UTF-8 byte limit','é'.repeat(32769),/64 KiB/],
 ['deep nesting','['.repeat(13)+'0'+']'.repeat(13),/nested too deeply/],
 ['prototype key','{"__proto__":{"polluted":true}}',/unsafe property/],
 ['escaped prototype key','{"\\u005f_proto__":{}}',/unsafe property/],
 ['constructor key','{"spec":{"constructor":{}}}',/unsafe property/],
 ['duplicate key','{"format":"one","format":"two"}',/duplicate property/],
 ['escaped duplicate','{"version":1,"\\u0076ersion":2}',/duplicate property/],
])test('JSON import rejects '+name,()=>assert.throws(()=>readPortable(text),message));
test('imports preserve text as text, accept UTF-8 BOM, and treat existing codes as new drafts',()=>{
 const d=portable();d.spec.name='<img src=x onerror="alert(1)">';
 const imported=api.parseDefinition('\uFEFF'+JSON.stringify(d),fixtures.schema,{...fixtures.context,specs:[spec()]});
 assert.equal(imported.name,d.spec.name);assert.equal(Object.hasOwn(imported,'id'),false);
 assert.equal(api.validateSpec(imported,fixtures.schema,{...fixtures.context,specs:[spec()]}).errors.code,'Code is already in use');
});
test('salinity sharing converts TDS once and exports canonical EC values',()=>{
 const d=portable();d.spec.kind='S';d.spec.salinity={unit:'TDS',tds_factor:.6};d.spec.params.FlowThresholdMin=1000;
 const imported=readPortable(d);assert.equal(imported.params.FlowThresholdMin,600);assert.equal(imported.salinity.unit,'EC');
 assert.equal(readPortable(api.exportDefinition(imported)).params.FlowThresholdMin,600);
});
test('overflowing JSON numbers are rejected and validator errors do not inherit unsafe keys',()=>{
 const text=JSON.stringify(portable()).replace('"Duration":5','"Duration":1e999');assert.throws(()=>readPortable(text),/finite JSON number/);
 const s=JSON.parse(JSON.stringify(spec()).replace('"code"','"__proto__":{},"code"'));
 assert.equal(api.validateSpec(s,fixtures.schema,fixtures.context).ok,false);
 assert.equal(api.validateSpec({...spec(),kind:'constructor'},fixtures.schema,fixtures.context).ok,false);
});


for(const [kind,handler,params,extra] of [
 ['L','level_handle',{LevelThresholdMin:1,LevelThresholdMax:2},{}],
 ['V','cumulative_handle',{VolumeThreshold:1000,AccumulationPeriod:30},{}],
 ['F','flow_handle_multi',{FlowThresholdMin:200},{second_gauge:'404243'}],
 ['S','lowflow_handle',{FlowThresholdMin:0,FlowThresholdMax:800,MinSpell:1},{salinity:{unit:'EC',tds_factor:.6}}],
])test('portable definition round-trip for '+kind+' / '+handler,()=>{
 const s={...spec(),...extra,kind,handler,params:{Duration:5,TargetFrequency:50,...params}},checked=api.validateSpec(s,fixtures.schema,fixtures.context);
 assert.equal(checked.ok,true,JSON.stringify(checked.errors));const out=readPortable(api.exportDefinition(checked.spec));
 assert.deepEqual(plain(out.params),plain(checked.spec.params));assert.equal(out.kind,kind);assert.equal(out.handler,handler);
});


test('salinity conversion cannot import a range that collapses to zero EC',()=>{
 const d=portable();d.spec.kind='S';d.spec.salinity={unit:'TDS',tds_factor:1e-300};d.spec.params.FlowThresholdMin=0;d.spec.params.FlowThresholdMax=1;
 assert.throws(()=>readPortable(d),/Upper limit must exceed lower limit/);
});

const formSubmit=source.slice(source.indexOf('  async function submit(run){'),source.indexOf("  dialog.addEventListener('submit'"));
for(const editing of [false,true])test(`a failed run preserves the saved form identity for validation and retry (${editing?'edit':'new'})`,async()=>{
 const initial=editing?spec():clone(fixtures.base),h=registryHarness({definitions:editing?[initial]:[]});await h.api.sync();
 h.ctx.initial=initial;h.run(`
  let original=initial.id?userClone(initial):null,draft=userClone(initial),busy=false,formEpoch=1,status='';
  const dialog={open:true,querySelectorAll:()=>[{dataset:{scenario:'a'}}],close(){this.open=false;}};
  const currentForm=token=>dialog.open&&token===formEpoch,live=()=>CUSTOM;
  const formStatus=text=>{status=text;},showNotice=()=>{},errorText=error=>error.message;
  const validate=()=>validateSpec(draft,UEWR.schema,UEWR.context());
 `+formSubmit);
 let attempts=0;h.api.run=async ids=>{assert.equal(ids[0],h.run('draft.id'));if(++attempts===1)throw Error('Evaluation unavailable');return {};};
 await h.run('submit(true)');
 const saved=[...h.server.values()][0];assert.equal(h.run('dialog.open'),true);assert.equal(h.run('status'),'Evaluation unavailable');
 assert.deepEqual(plain(h.run('original')),saved);assert.deepEqual(plain(h.run('draft')),saved);
 assert.equal(h.run('validate().ok'),true);assert.equal(h.run('busy'),false);
 await h.run('submit(true)');
 assert.equal(attempts,2);assert.equal(h.server.size,1);assert.equal(h.server.get(saved.id).ver,saved.ver+1);assert.equal(h.run('dialog.open'),false);
});
test('a save completing after another form opens leaves that form untouched',async()=>{
 const h=registryHarness();await h.api.sync();h.ctx.initial=clone(fixtures.base);h.run(`
  let original=null,draft=userClone(initial),busy=false,formEpoch=1;
  const dialog={open:true,querySelectorAll:()=>[],close(){this.open=false;}};
  const currentForm=token=>dialog.open&&token===formEpoch,live=()=>CUSTOM;
  const formStatus=()=>{},showNotice=()=>{},errorText=error=>error.message;
  const validate=()=>validateSpec(draft,UEWR.schema,UEWR.context());
 `+formSubmit);
 h.saveGate=async()=>{h.run("formEpoch++;draft={code:'U_OTHER'};");};
 await h.run('submit(false)');
 assert.deepEqual(plain(h.run('draft')),{code:'U_OTHER'});assert.equal(h.run('original'),null);assert.equal(h.run('dialog.open'),true);
});
