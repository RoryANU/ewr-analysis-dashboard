// node --test tests/test_performance.js; no browser, network, or third-party packages required.
const fs = require('node:fs'), vm = require('node:vm');
const assert = require('node:assert/strict'), {test} = require('node:test');
const html = fs.readFileSync('ewr_analysis_dashboard.html','utf8');
const template = fs.readFileSync('src/ewr_analysis/dashboard_template.html','utf8');
const source = fs.readFileSync('src/ewr_analysis/performance.js','utf8');
const embedded = html.match(/const DATA=([\s\S]*?);\s*const /)[0].replace(/\s*const $/,'');
const plain = value => JSON.parse(JSON.stringify(value));
const row = (code,fr=[1],et=fr.map(()=>0)) => [0,0,0,code,[0],[],fr,et];
const deferred = () => { let resolve,reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
const flush = () => new Promise(resolve => setImmediate(resolve));
function harness() {
  const ctx = vm.createContext({console,URLSearchParams,AbortController,DOMException,TextEncoder,performance});
  vm.runInContext(embedded,ctx);
  vm.runInContext(`
    const S={ethr:0,fbthr:.5,thr:1,pol:0,cli:0,bpol:1,bcli:0,src:'demo',mode:'single',sel:{type:'gauge',i:0}};
    const NC=META.climates.length,CUSTOM=[],PCFG={},FLOWGEN=0;
    const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const scnIdx=(p=S.pol,c=S.cli)=>p*NC+c,isDiff=()=>S.mode==='diff';
    const flowCtx=()=>({ok:true,base:'test',gauge:META.gauges[0],bn:'Test record'});
    const ayName=()=> 'July–June';
    var nodes=new Map();
    const makeNode=id=>({id,innerHTML:'',textContent:'',disabled:false,hidden:false,children:[],querySelectorAll:()=>[],replaceChildren(...items){this.children=items;}});
    const document={createElement:()=>makeNode('chart')};
    const $=id=>{if(!nodes.has(id))nodes.set(id,makeNode(id));return nodes.get(id);};
    var plotCalls=[];
    var Plotly={newPlot:async (...args)=>{plotCalls.push(args);},purge:()=>{}};
  `,ctx);
  vm.runInContext(source,ctx);
  const run = code => vm.runInContext(code,ctx);
  return {ctx,run,view:(rows=[row('F1')]) => { ctx.fixtureRows=rows; return run('preparePerformance(fixtureRows)'); }};
}
const response = data => ({ok:true,status:200,json:async()=>data});
const annual = {ystart:7,mrip_days:730,scenarios:{base:{years:{wy:[2000,2001],achieved:[1,0],complete:[1,1]},coverage:{start:'2000-07-01',end:'2002-06-30',missing_days:0,complete_years:2,years:2}}}};
function invFor(h,codes=['F1']) { return {ewrs:codes.map(code=>({code,pu:h.run('META.pus[0]'),pus:[h.run('META.pus[0]')]})),inventory:[]}; }

test('classification boundaries, invalid values and raw denominator',()=>{
  const {run}=harness();
  for(const [f,e,want] of [[1,0,0],[.99,0,2],[1,.1,1],[.8,.1,3],[1-1e-10,0,0],[null,0,null],[1,1.01,null],[-1,0,null],[1,-.1,null]])assert.equal(run(`jointClass(${f},${e})`),want);
  assert.equal(run('jointClass(1,NaN)'),null);
  assert.equal(run('jointSummary([[0,0,0,0,0,0,[1],[0]],[0,0,0,0,0,0,[null],[0]]],0).n'),1);
  assert.equal(run('jointSummary([],0).score'),null);
  assert.equal(run('percentile([0,10],.1)'),1);
});
test('plot bounds check BOTH endpoints, ETP above 1 capped, negatives and unlocked FR',()=>{
  const h=harness(); h.ctx.boundRows=[row('above',[1,3]),row('below',[3,1]),row('etp',[1,1],[1.1,0]),row('negative',[-1,1]),row('ok',[1,1.5])];
  assert.equal(h.run('ruleETP(boundRows[2],0)'),1); assert.equal(h.run('ruleETP(boundRows[2],1)'),0);
  const locked=plain(h.run('jointGeometry(boundRows,0,1,true)'));
  assert.equal(locked.omittedCurrent,2); assert.equal(locked.omittedBase,1); assert.equal(locked.arrows.length,2);
  const unlocked=plain(h.run('jointGeometry(boundRows,0,1,false)'));
  assert.equal(unlocked.xmax,3);assert.equal(unlocked.arrows.length,4); assert.equal(unlocked.omittedCurrent,1);
  h.ctx.v=h.view(h.ctx.boundRows);h.run('v.base=1; drawJointPlot(v)');
  assert.equal(h.run('plotCalls.length'),1);
  assert.match(h.run('plotCalls[0][1][0].hovertemplate'),/Maximum gap exceeded/);
  assert.equal(h.run("plotCalls[0][2].yaxis.tickformat"),'.0%');
  // the joint plot is always drawn unlocked: the axis reaches the largest FR and every baseline -> comparison arrow is shown
  assert.deepEqual(plain(h.run('plotCalls[0][2].xaxis.range')),[0,unlocked.xmax]);
  assert.equal(h.run('plotCalls[0][2].annotations.length'),unlocked.arrows.length);
});
test('known climate outcomes require complete nonempty climate sets',()=>{
  const h=harness();h.ctx.climateRows=[row('resilient',[1,1],[0,0]),row('crossing',[1,.5],[0,0]),row('incomplete',[1,null],[0,0]),row('fails',[.5,.5],[0,0])];
  const q=h.run('climateSummary(climateRows,[0,1])');
  assert.equal(q.resilient.length,1);assert.equal(q.crossings.length,1);assert.equal(q.incomplete.length,1);
  assert.deepEqual(plain(q.assessed),[2,2,1,2]);assert.equal(q.expected,2);
  assert.equal(h.run('climateSummary(climateRows,[]).resilient.length'),0);
});
test('matrix includes an added policy, labels incompleteness and omits single-mode baseline',()=>{
  const h=harness();h.ctx.v=h.view();
  h.run("META.policies.push('Fifth policy'); S.bpol=-999;");
  const table=h.run('robustnessTable(v)');
  assert.match(table,/Fifth policy/);assert.match(table,/climates assessed per EWR/);assert.doesNotMatch(table,/Base:/);
  h.run("S.src='user'; CUSTOM.push({name:'Uploaded',P:5});");
  assert.match(h.run('robustnessTable(v)'),/climate unspecified/);
});
test('details are lazy and memoised, routine renders retain context, scale changes do not cancel tasks',()=>{
  const h=harness(),v=h.view();h.ctx.v=v;
  h.run('performancePanels(v); robustnessTable(v)');assert.equal(v.details.size,0);
  const a=h.run('detailText(v,0)');assert.equal(v.details.size,1);assert.equal(h.run('detailText(v,0)'),a);
  assert.equal(h.run("S.sort='high'; preparePerformance(fixtureRows)"),v);
  assert.equal(h.run('S.thr=.9; preparePerformance(fixtureRows)'),v);
  v.sequence=new AbortController();v.conditions=new AbortController();
  h.run('drawJointPlot(v)');assert.equal(v.sequence.signal.aborted,false);assert.equal(v.conditions.signal.aborted,false);
  h.run('S.cli=1; preparePerformance(fixtureRows)');assert.equal(v.sequence.signal.aborted,true);assert.equal(v.conditions.signal.aborted,true);
});
test('sequence uncertainty across missing years is preserved',()=>{
  const {run}=harness();
  assert.deepEqual(plain(run('sequenceStats({wy:[2000,2001,2002,2003,2004,2005,2006],complete:[1,1,1,1,1,0,1],achieved:[0,1,0,0,0,0,0]},5)')),{since:[null,0,1,2,3,null,null],rolling:[null,null,null,null,.2,null,null]});
  assert.deepEqual(plain(run('sequenceStats({wy:[1,2,3,4,5,6,7,8,9,10],complete:Array(10).fill(1),achieved:[1,0,0,1,0,0,0,0,0,1]}).rolling')),[null,null,null,null,null,null,null,null,null,.3]);
  assert.equal(run('sequenceStats({wy:[2000,2002],complete:[1,1],achieved:[1,0]}).since[1]'),null);
});
test('CSV dialect, numeric validation, size/row bounds, missing values and bounded gaps',()=>{
  const {ctx,run}=harness();
  const parse=text=>{ctx.csv=text;return run('parseConditions(csv)');};
  assert.equal(parse('\ufeff"date","stage_m"\r\n"2000-01-01","1"\r\n2000-01-02,.5')[1].rate,-.5);
  assert.equal(parse('date,stage_m\n2000-01-01,\n2000-01-02,2')[1].rate,null);
  for(const value of [' ','0x10','1e3','Infinity','NaN','1,2'])assert.throws(()=>parse('date,stage_m\n2000-01-01,"'+value+'"'));
  for(const text of ['', 'date,stage_m', 'date\n2000-01-01', 'date,stage_m\n2000-02-30,1', 'date,stage_m\n2000-01-01,1\n2000-01-01,2','date,stage_m\n"2000-01-01,1','date,stage_m\n2000-01-01,"1"junk'])assert.throws(()=>parse(text));
  assert.throws(()=>parse('x'.repeat(2*1024*1024+1)),/2 MiB/);
  assert.throws(()=>parse('date,stage_m\n'+'2000-01-01,1\n'.repeat(20001)),/20,000/);
  ctx.csv='date,stage_m\n0001-01-01,1\n9999-12-31,2';assert.equal(run('breakConditionGaps(parseConditions(csv)).length'),3);
});
test('legacy links retain .9; versioned/new links use 1 and always encode thr',()=>{
  const h=harness();
  // applyHash restores observed filters for every nonempty hash; mirror the real S.obs state.
  h.run("S.obs={}; var location={hash:''}; const METRICS=['fr','ach']; const fillPolicyOptions=()=>{},syncUI=()=>{};");
  h.run(template.slice(template.indexOf('function encodeState(){'),template.indexOf('function syncUI(){')));
  h.run('applyHash()');assert.equal(h.run('S.thr'),1);
  h.run("location.hash='#metric=ach';applyHash()");assert.equal(h.run('S.thr'),.9);
  h.run("location.hash='#v=2&metric=ach';applyHash()");assert.equal(h.run('S.thr'),1);
  h.run("location.hash='#metric=ach&thr=0.95';applyHash()");assert.equal(h.run('S.thr'),.95);
  h.run('S.thr=1');const encoded=new URLSearchParams(h.run('encodeState()'));assert.equal(encoded.get('v'),'2');assert.equal(encoded.get('thr'),'1');
});
test('inventory shares concurrent requests and evicts failures for retry',async()=>{
  const h=harness();let calls=0;const d=deferred();h.ctx.fetch=()=>{calls++;return d.promise;};
  const a=h.run("inventoryFor('123')"),b=h.run("inventoryFor('123')");assert.equal(a,b);assert.equal(calls,1);
  d.reject(Error('offline'));await assert.rejects(a,/offline/);
  h.ctx.fetch=async()=>{calls++;return response({ewrs:[],inventory:[]});};
  await h.run("inventoryFor('123')");await h.run("inventoryFor('123')");assert.equal(calls,2);
});
test('annual failures and missing Plotly always restore controls',async()=>{
  const h=harness();h.ctx.v=h.view();h.ctx.fetch=async()=>{throw Error('network failure');};
  await h.run('loadSequences(v)');assert.equal(h.run("$('loadSequence').disabled"),false);assert.match(h.run("$('sequenceNote').textContent"),/network failure/);
  h.run('Plotly=undefined');await h.run('loadSequences(v)');assert.equal(h.run("$('loadSequence').disabled"),false);assert.match(h.run("$('sequenceNote').textContent"),/Plotly/);
});
test('cancel while inventory is pending restores button immediately',async()=>{
  const h=harness(),d=deferred();h.ctx.v=h.view();h.ctx.fetch=()=>d.promise;
  const work=h.run('loadSequences(v)');h.run('v.sequence.abort()');await work;
  assert.equal(h.run("$('loadSequence').disabled"),false);assert.match(h.run("$('sequenceNote').textContent"),/cancelled/);
  d.resolve(response({ewrs:[],inventory:[]}));await flush();
});
test('annual evaluation is bounded to four requests, cancellation aborts them and does not launch queued rules',async()=>{
  const h=harness(),codes=Array.from({length:7},(_,i)=>'F'+i);h.ctx.v=h.view(codes.map(code=>row(code)));
  let starts=0,aborts=0;h.ctx.fetch=async(url,options)=>{
    if(url.startsWith('/api/ewrs?'))return response(invFor(h,codes));
    starts++;return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{aborts++;reject(new DOMException('Cancelled','AbortError'));},{once:true}));
  };
  const work=h.run('loadSequences(v)');await flush();assert.equal(starts,4);
  h.run('v.sequence.abort()');await work;assert.equal(aborts,4);assert.equal(starts,4);assert.equal(h.run("$('loadSequence').disabled"),false);
});
test('successful annual data renders; rejected chart promises recover controls',async()=>{
  const h=harness();h.ctx.v=h.view();h.ctx.fetch=async url=>response(url.startsWith('/api/ewrs?')?invFor(h):annual);
  await h.run('loadSequences(v)');assert.equal(h.run('plotCalls.length'),3);assert.match(h.run("$('performanceCoverage').textContent"),/2\/2 complete/);
  h.run("Plotly.newPlot=async()=>{throw Error('render rejected');}");await h.run('loadSequences(v)');
  assert.equal(h.run("$('loadSequence').disabled"),false);assert.match(h.run("$('sequenceNote').textContent"),/render rejected/);
});
test('late annual response cannot update a changed selection',async()=>{
  const h=harness(),d=deferred();h.ctx.v=h.view();h.ctx.fetch=async url=>url.startsWith('/api/ewrs?')?response(invFor(h)):d.promise;
  const work=h.run('loadSequences(v)');await flush();h.run('S.cli=1;preparePerformance(fixtureRows);nodes=new Map();');
  d.resolve(response(annual));await work;assert.equal(h.run('plotCalls.length'),0);assert.equal(h.run("$('sequenceNote').textContent"),'');
});
test('CSV read survives scale toggles and routine renders; newer file wins',async()=>{
  const h=harness(),d=deferred();h.ctx.v=h.view();h.ctx.file={name:'old.csv',size:100,text:()=>d.promise};
  const old=h.run('loadConditions(file,v)');h.run('drawJointPlot(v);preparePerformance(fixtureRows)');
  h.ctx.newFile={name:'new.csv',size:100,text:async()=> 'date,stage_m\n2000-01-01,1\n2000-01-02,2'};
  await h.run('loadConditions(newFile,v)');d.resolve('date,stage_m\n2000-01-01,9');await old;
  assert.match(h.run("$('conditionNote').textContent"),/new.csv/);assert.equal(h.run('plotCalls.filter(c=>c[0].id!=="jointPlot").length'),1);
});
test('oversized files are rejected before reading; stale CSV after selection change is ignored',async()=>{
  const h=harness(),d=deferred();h.ctx.v=h.view();let read=false;
  h.ctx.file={size:3*1024*1024,text:()=>{read=true;return d.promise;}};await h.run('loadConditions(file,v)');assert.equal(read,false);
  h.ctx.file={size:20,text:()=>d.promise};const work=h.run('loadConditions(file,v)');h.run('S.cli=1;preparePerformance(fixtureRows);nodes=new Map();');
  d.resolve('date,stage_m\n2000-01-01,1');await work;assert.equal(h.run('plotCalls.length'),0);
});
test('generated script syntax and real-data largest-gauge timings',()=>{
  for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
  const h=harness();const result=h.run(`(()=>{
    const groups=new Map();for(const r of ROWS){if(!groups.has(r[2]))groups.set(r[2],[]);groups.get(r[2]).push(r);}
    const rows=[...groups.values()].reduce((a,b)=>a.length>b.length?a:b,[]);
    const view=preparePerformance(rows),start=performance.now();performancePanels(view);drawJointPlot(view);
    const initial=performance.now()-start,open=performance.now();robustnessTable(view);
    const matrix=performance.now()-open,reuse=performance.now();for(let i=0;i<100;i++)preparePerformance(rows);
    return {rows:rows.length,initialMs:initial,matrixMs:matrix,reuse100Ms:performance.now()-reuse,detailEntries:view.details.size};
  })()`);
  assert.equal(result.detailEntries,0);console.log('Largest gauge benchmark:',plain(result));
});
test('deferred CSV alone completes after scale change and a routine render',async()=>{
  const h=harness(),d=deferred();h.ctx.v=h.view();h.ctx.file={name:'kept.csv',size:80,text:()=>d.promise};
  const work=h.run('loadConditions(file,v)');h.run("S.sort='low';preparePerformance(fixtureRows);drawJointPlot(v)");
  d.resolve('date,stage_m\n2000-01-01,1');await work;
  assert.match(h.run("$('conditionNote').textContent"),/kept.csv/);assert.equal(h.run('plotCalls.filter(c=>c[0].id!=="jointPlot").length'),1);
});
test('explicit custom registration does not hide a fifth built-in policy',()=>{
  const h=harness();h.run(template.match(/const isCustom=.*?;/)[0]);
  h.run("META.policies.push('Fifth policy')");assert.equal(h.run('isCustom(4)'),false);
  h.run("CUSTOM.push({P:5,name:'uploaded',removed:true})");assert.equal(h.run('isCustom(5)'),true);
});
test('annual requests use selected planning units, not a grouped canonical unit',async()=>{
  const h=harness(),a=row('F1'),b=row('F1');b[0]=1;h.ctx.v=h.view([a,b]);
  const selected=[h.run('META.pus[0]'),h.run('META.pus[1]')],requests=[];
  h.ctx.fetch=async url=>{
    if(url.startsWith('/api/ewrs?'))return response({ewrs:[{code:'F1',pu:'Unselected canonical asset',pus:['Unselected canonical asset',...selected]}],inventory:[]});
    requests.push(new URL('http://test'+url).searchParams.get('pu'));return response(annual);
  };
  await h.run('loadSequences(v)');assert.deepEqual(requests.sort(),selected.sort());
  for(const pu of selected)assert.ok(h.run("$('performanceCoverage').textContent").includes(pu));
});
test('a late Plotly completion cannot overwrite a newer CSV chart in the same view',async()=>{
  const h=harness(),d=deferred();h.ctx.v=h.view();
  h.ctx.firstPlot=d.promise;
  h.run('Plotly.newPlot=async (node,traces)=>{ if(!plotCalls.length){plotCalls.push(node);await firstPlot;} else plotCalls.push(node);node.textContent=String(traces[0].y[0]);}');
  h.ctx.file={name:'old.csv',size:100,text:async()=> 'date,stage_m\n2000-01-01,1'};
  const old=h.run('loadConditions(file,v)');await flush();
  h.ctx.file={name:'new.csv',size:100,text:async()=> 'date,stage_m\n2000-01-01,9'};
  await h.run('loadConditions(file,v)');d.resolve();await old;
  assert.equal(h.run("$('conditionPlot').children[0].textContent"),'9');
  assert.match(h.run("$('conditionNote').textContent"),/new.csv/);
  assert.notEqual(h.run('plotCalls[0]'),h.run('plotCalls[1]'));
});
test('a late annual plot cannot overwrite a retry in the same view',async()=>{
  const h=harness(),d=deferred();h.ctx.v=h.view();h.ctx.fetch=async url=>response(url.startsWith('/api/ewrs?')?invFor(h):annual);
  h.ctx.firstPlot=d.promise;
  h.run("Plotly.newPlot=async node=>{const old=plotCalls.length===0;plotCalls.push(node);if(old)await firstPlot;node.textContent=old?'old':'new';}");
  const first=h.run('loadSequences(v)');await flush();h.run('v.sequence.abort()');
  await h.run('loadSequences(v)');d.resolve();await first;
  assert.equal(h.run("$('sequenceRaster').children[0].textContent"),'new');
  assert.equal(h.run("$('loadSequence').disabled"),false);
});
test('existing metric tiles cap ETP from legacy saved uploads consistently',()=>{
  const h=harness();
  h.run(template.slice(template.indexOf('const METRICS='),template.indexOf('const KEYF=')));
  h.ctx.legacy=row('F1',[1,1],[1.2,.5]);
  assert.equal(h.run("val(legacy,'et',0)"),1);
  assert.equal(h.run("S.ethr=1;val(legacy,'mz',0)"),1);
  h.run("S.ethr=0; S.mode='diff'; S.bpol=0; S.bcli=1");   // threshold back to 0: at 1 both scenarios would meet the MRIP target and leave the means
  assert.equal(h.run("val(legacy,'et')"),-.5);   // capped ETP rises from .5 (base) to 1: a decline, as lower is better
});

test('performance hides user rows in Demo and omits stale version values in Your data',()=>{
 const h=harness(),builtin=row('F1'),user=row('U_ONE');user.user='u_one';user.userVer=2;user[8]={uewrVerAt:{0:1}};
 h.ctx.userRows=[builtin,user];assert.equal(h.run('preparePerformance(userRows).rows.length'),1);
 h.run("S.src='user'");assert.equal(h.run('preparePerformance(userRows).rows.length'),2);
 assert.equal(h.run('ruleFR(userRows[1],0)'),null);assert.equal(h.run('jointSummary(userRows,0).n'),1);
 h.run('userRows[1][8].uewrVerAt[0]=2');assert.equal(h.run('jointSummary(userRows,0).n'),2);
});

test('inventory invalidation fetches newly registered U_ codes instead of reusing old responses',async()=>{
 const h=harness();let count=0;h.ctx.fetch=async()=>{count++;return response({ewrs:[{code:count===1?'F1':'U_ONE'}],inventory:[]});};
 assert.equal((await h.run("inventoryFor('404216')")).ewrs[0].code,'F1');
 h.run("invalidateInventory('404216')");assert.equal((await h.run("inventoryFor('404216')")).ewrs[0].code,'U_ONE');assert.equal(count,2);
});

test('an invalidated inventory rejection cannot evict a newer in-flight request',async()=>{
 const h=harness(),old=deferred(),newer=deferred();let calls=0;h.ctx.fetch=()=>++calls===1?old.promise:newer.promise;
 const a=h.run("inventoryFor('123')");h.run("invalidateInventory('123')");const b=h.run("inventoryFor('123')");
 old.reject(Error('old failure'));await assert.rejects(a,/old failure/);assert.equal(h.run("inventoryFor('123')"),b);assert.equal(calls,2);
 newer.resolve(response({ewrs:[],inventory:[]}));await b;
});
test('annual inventory reconciles every kind from the live registry and hides deleted or Demo definitions',()=>{
 const h=harness();h.ctx.UEWR={atGauge:()=>[{code:'U_LEVEL',pu:'PU',pus:['PU'],kind:'L',custom:true}]};
 h.ctx.cached={ewrs:[{code:'F1'},{code:'U_DELETED',custom:true}],inventory:[{code:'F1'},{code:'U_DELETED',custom:true}]};
 const current=plain(h.run("currentInventory('123',cached)"));assert.deepEqual(current.ewrs.map(e=>e.code),['F1','U_LEVEL']);assert.equal(current.inventory[1].detailed,true);
 h.ctx.UEWR.atGauge=()=>[];assert.deepEqual(plain(h.run("currentInventory('123',cached).ewrs")),[{code:'F1'}]);
});
