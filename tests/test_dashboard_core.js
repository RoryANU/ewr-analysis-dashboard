// node --test tests/test_dashboard_core.js; no browser, network, or third-party packages required.
// The headline numbers of the page: per-EWR values (val), the hierarchical mean (agg) and the selection (selRows),
// run from the template source against a small synthetic data set.
const fs = require('node:fs'), vm = require('node:vm');
const assert = require('node:assert/strict'), {test} = require('node:test');
const template = fs.readFileSync('src/dashboard_template.html','utf8');
// the "values and averages" and "selection" sections; each marker must occur once so a template edit fails here, not silently
function section(from,to) {
  for (const m of [from,to]) assert.equal(template.split(m).length,2,`marker ${JSON.stringify(m)} must occur exactly once in the template; update the marker in the test if the template section was renamed`);
  return template.slice(template.indexOf(from),template.indexOf(to));
}
const core = section('// ---- values and averages','// ---- colours');
// ROW = [planningUnit, sdl, gauge, ewr, [objectives], [regimes], fr[P*NC], etp[P*NC], {T, ny}, achievedYears[P*NC]]; two policies x two climates.
// By default a 100-year record and a 100% target, with achieved years consistent with FR (EY = FR x T x ny / 100), so a change in achieved
// years per century is 100 x the change in FR
const row = (pu,sdl,gauge,code,fr,et=fr.map(()=>0),meta={T:100,ny:100}) => [pu,sdl,gauge,code,[],[],fr,et,meta,fr.map(v=>v==null?null:v*meta.T*meta.ny/100)];
const plain = value => JSON.parse(JSON.stringify(value));
function harness(rows=[]) {
  const ctx = vm.createContext({});
  ctx.ROWS = rows;
  vm.runInContext(`
    const META={climates:['c0','c1'],policies:['Pre Basin Plan','Basin plan full implementation']};
    const NC=META.climates.length;
    const S={thr:1.0,ethr:0,fbthr:0.5,band:2,mode:'single',pol:1,cli:0,bpol:0,bcli:0,metric:'fr',sel:{type:'none'}};
  `,ctx);
  vm.runInContext(core,ctx);
  return code => vm.runInContext(code,ctx);
}
const near = (actual,expected) => assert.ok(Math.abs(actual-expected)<1e-12,`${actual} != ${expected}`);

const observedHelpers=section('// ---- observed result classification','function observedPanel(){');
test('section markers name a missing marker and explain how to update it',()=>{
  assert.throws(()=>section('// missing observed marker','function observedPanel(){'),/marker "\/\/ missing observed marker".*update the marker in the test if the template section was renamed/);
});
function observedHarness(){
  const ctx=vm.createContext({});
  vm.runInContext(observedHelpers,ctx);
  return code=>plain(vm.runInContext(code,ctx));
}
test('classifyGauge: every pair of terminal measurement statuses',()=>{
  const run=observedHarness(), statuses=['ok','empty','unsupported','error','timeout','rate_limited'];
  for(const f of statuses)for(const l of statuses){
    const states={f:{status:f},l:{status:l}}, okVars=['f','l'].filter(v=>states[v].status==='ok'), badVars=['f','l'].filter(v=>states[v].status!=='ok');
    const outcome=okVars.length===2?'complete':okVars.length?'partial':[f,l].every(s=>['empty','unsupported'].includes(s))?'nodata':'failed';
    const emptyVars=badVars.filter(v=>['empty','unsupported'].includes(states[v].status)), failedVars=badVars.filter(v=>!emptyVars.includes(v));
    const partialKind=outcome==='partial'?(okVars.includes('f')?'nolevel':'noflow'):null;
    assert.deepEqual(run(`classifyGauge(${JSON.stringify(states)})`),{outcome,okVars,emptyVars,failedVars,badVars,partialKind},`${f}/${l}`);
  }
});
test('classifyGauge: waiting and in-progress measurements cannot look complete',()=>{
  const run=observedHarness();
  for(const input of ['undefined','null','{}','{f:{},l:null}'])assert.deepEqual(run(`classifyGauge(${input})`),{outcome:'waiting',okVars:[],emptyVars:[],failedVars:[],badVars:[],partialKind:null});
  for(const pending of [undefined,'pending','running','retry_wait'])for(const terminal of ['ok','empty','unsupported','error','timeout','rate_limited'])for(const v of ['f','l']){
    const other=v==='f'?'l':'f', states={[v]:pending?{status:pending}:{},[other]:{status:terminal}};
    const empty=['empty','unsupported'].includes(terminal);
    assert.deepEqual(run(`classifyGauge(${JSON.stringify(states)})`),{outcome:'running',okVars:terminal==='ok'?[other]:[],emptyVars:empty?[other]:[],failedVars:terminal==='ok'||empty?[]:[other],badVars:terminal==='ok'?[]:[other],partialKind:null});
  }
  assert.deepEqual(run('classifyGauge({f:{status:"pending"},l:{status:"retry_wait"}})'),{outcome:'running',okVars:[],emptyVars:[],failedVars:[],badVars:[],partialKind:null});
});
test('describeResult: labels distinguish unavailable records, query failures and setup failures',()=>{
  const run=observedHarness(), cases=[
    [{status:'ok'},'Available',false,'success'],
    [{status:'empty'},'No records for this period',false,'info'],
    [{status:'unsupported'},'Not available from this source',false,'info'],
    [{status:'timeout'},'Timed out',true,'error'],
    [{status:'rate_limited'},'Data service busy',true,'error'],
    [{status:'error',message:'unexpected failure'},'Unexpected problem',true,'error'],
    // legacy payloads (no envelope): message text is never parsed, so station/busy wording stays generic
    [{status:'error',message:'FileNotFoundError: ~/bom_water/cache/stations.json'},'Unexpected problem',true,'error'],
    [{status:'error',message:'too many requests in queue'},'Unexpected problem',true,'error'],
    [{status:'error',message:'error_num 218'},'Unexpected problem',true,'error'],
    [{status:'error',message:'PermissionError: cannot read settings',retryable:false},'Unexpected problem',false,'error'], // a false flag does not name a cause
    // envelope: the code decides the words, the envelope's own retryable decides retry
    [{status:'error',message:'x',retryable:true,error:{code:'STATION_LIST_SETUP_REQUIRED',retryable:false}},'Weather-bureau station list not set up',false,'error'],
    [{status:'error',message:'x',error:{code:'CONFIGURATION_REQUIRED',retryable:false}},'Needs IT support',false,'error'],
    [{status:'error',message:'x',error:{code:'INVALID_REQUEST',retryable:false}},'Setup or request needs attention',false,'error'],
    [{status:'error',message:'x',error:{code:'SERVICE_BUSY',retryable:true,attempts:3}},'Data service busy',true,'error'],
    [{status:'error',message:'x',error:{code:'NETWORK_TIMEOUT',retryable:true,attempts:3}},'Timed out',true,'error'],
    [{status:'error',message:'x',error:{code:'UNEXPECTED_ERROR',retryable:true,attempts:3}},'Unexpected problem',true,'error'],
    [{status:'error',message:'x',error:{code:'SOMETHING_NEW',retryable:true}},'Unexpected problem',true,'error'],
    [{status:'error',message:'x',error:{code:'SOMETHING_NEW',retryable:false}},'Unexpected problem',false,'error'],
    [{status:'running',attempts:1},'Checking',false,'info'],
    [{status:'running',attempts:2,error:null},'Retrying automatically (attempt 2 of 3)…',false,'info'],
    [{status:'retry_wait',attempts:1,error:{code:'SERVICE_BUSY',attempts:1,max_attempts:3,retryable:true}},'Retrying automatically (attempt 2 of 3)…',false,'info'],
    [{status:'pending'},'Waiting',false,'info'],
    [{status:'running'},'Checking',false,'info'],
    [{},'Waiting',false,'info'],
  ];
  for(const [input,label,retryable,severity] of cases){
    const result=run(`describeResult('f',${JSON.stringify(input)})`);
    assert.deepEqual(Object.keys(result).sort(),['action','label','retryable','severity','skip','why']);
    assert.equal(result.label,label);assert.equal(result.retryable,retryable);assert.equal(result.severity,severity);
    assert.ok(result.why);assert.equal(typeof result.action,'string');
  }
  assert.match(run("describeResult('f',null)").why,/Flow/);
  assert.match(run("describeResult('l',undefined)").why,/Water level/);
  // Terminal non-failures never become retryable, even with an inconsistent flag.
  for(const status of ['ok','empty','unsupported','pending','running','retry_wait'])assert.equal(run(`describeResult('l',{status:'${status}',retryable:true})`).retryable,false);
  for(const status of ['error','timeout','rate_limited'])assert.equal(run(`describeResult('l',{status:'${status}',retryable:false})`).retryable,false);
});
test('observed retry button and selection use the same retryability decisions',()=>{
  const ctx=vm.createContext({}), listeners={};
  ctx.$=()=>({addEventListener:(event,handler)=>{listeners[event]=handler;}});
  vm.runInContext(observedHelpers,ctx);
  vm.runInContext(`
    const gauges=['setup','busy','timeout','empty','unsupported','permission','mixed'].map(id=>({id,name:id}));
    const OBSERVED_UNITS=[{sdl:'Test',gauges}], META={};
    const S={sel:{type:'none'},obs:{sdl:'Test',gauges:new Set(),ranges:{},query:'',notice:'',status:{
      setup:{f:{status:'error',message:'FileNotFoundError: ~/bom_water/cache/stations.json',error:{code:'STATION_LIST_SETUP_REQUIRED',retryable:false}}},
      busy:{f:{status:'error',message:'error_num 218',retryable:true,error:{code:'SERVICE_BUSY',retryable:true,attempts:3}}},
      timeout:{l:{status:'timeout',retryable:true}},
      empty:{f:{status:'empty'}},unsupported:{f:{status:'unsupported'}},
      permission:{l:{status:'error',retryable:false}},
      mixed:{f:{status:'error',message:'~/bom_water/cache/stations.json',error:{code:'STATION_LIST_SETUP_REQUIRED',retryable:false}},l:{status:'error',message:'too many requests in queue',error:{code:'SERVICE_BUSY',retryable:true,attempts:3}}}
    }}};
    const OBSERVED_AVAILABLE=true,esc=String;
    let requested;
    function runObserved(ids,merge){requested={ids,merge};}
  `,ctx);
  vm.runInContext(section('$("panel").addEventListener("click",event=>{const b=event.target.closest("[data-obs]")','$("panel").addEventListener("change",event=>{const t=event.target;'),ctx);
  vm.runInContext(section('function observedPanel(){','function render(){'),ctx);
  assert.match(vm.runInContext('observedPanel()',ctx),/data-obs="retry"/);
  assert.match(vm.runInContext('observedPanel()',ctx),/data-obs="retry">Retry 3 failed<\/button>/);
  listeners.click({target:{closest:()=>({dataset:{obs:'retry'}})}});
  assert.deepEqual(plain(vm.runInContext('requested',ctx)),{ids:['busy','timeout','mixed'],merge:true});
  vm.runInContext('S.obs.status={setup:S.obs.status.setup,permission:S.obs.status.permission,empty:S.obs.status.empty}',ctx);
  assert.doesNotMatch(vm.runInContext('observedPanel()',ctx),/data-obs="retry"/);
});
test('observed classification helpers preserve their inputs',()=>{
  const run=observedHarness();
  assert.equal(run(`(()=>{const states={f:{status:'ok'},l:{status:'error',message:'error_num 218',retryable:true}},before=JSON.stringify(states);classifyGauge(states);describeResult('l',states.l);return JSON.stringify(states)===before;})()`),true);
});
test('observed error codes use shared samples with the Python adapter, and message text is never parsed',()=>{
  const run=observedHarness(),cases=JSON.parse(fs.readFileSync('tests/fixtures/observed_message_cases.json','utf8'));
  for(const sample of cases){
    const byCode=run(`describeResult('f',${JSON.stringify({status:'error',retryable:sample.retryable,error:{code:sample.code,retryable:sample.retryable}})})`);
    assert.equal(byCode.retryable,sample.retryable,sample.message);
    assert.equal(byCode.label,sample.label,sample.message);
    const legacy=run(`describeResult('f',${JSON.stringify({status:'error',retryable:true,message:sample.message})})`);
    assert.equal(legacy.label,'Unexpected problem',sample.message);
  }
});
test('summarizeGauges: every outcome contributes once, with only terminal outcomes checked',()=>{
  const run=observedHarness(),status={
    complete:{f:{status:'ok'},l:{status:'ok'}},partial:{f:{status:'ok'},l:{status:'error'}},
    nodata:{f:{status:'empty'},l:{status:'unsupported'}},failed:{f:{status:'timeout'},l:{status:'error'}},
    running:{f:{status:'error'}},pending:{f:{status:'pending'},l:{status:'ok'}},waiting:{}
  },ids=[...Object.keys(status),'missing'];
  const summary=run(`summarizeGauges(${JSON.stringify(ids)},${JSON.stringify(status)})`);
  assert.deepEqual(summary,{total:8,checked:4,complete:1,partial:1,nodata:1,failed:1,running:2,waiting:2});
  assert.equal(['complete','partial','nodata','failed','running','waiting'].reduce((sum,k)=>sum+summary[k],0),summary.total);
  assert.equal(summary.checked+summary.running+summary.waiting,summary.total);
  assert.deepEqual(run('summarizeGauges([],{})'),{total:0,checked:0,complete:0,partial:0,nodata:0,failed:0,running:0,waiting:0});
  assert.equal(run('summarizeGauges(["a"]).waiting'),1);
  assert.equal(run(`(()=>{const ids=['a','b'],status={a:{f:{status:'ok'}}},before=JSON.stringify([ids,status]);summarizeGauges(ids,status);return JSON.stringify([ids,status])===before;})()`),true);
});

function observedPanelHarness(){
  const ctx=vm.createContext({});
  vm.runInContext(observedHelpers,ctx);
  vm.runInContext(`
    const gauges=Array.from({length:16},(_,i)=>({id:'G'+(i+1),name:'Gauge '+(i+1)}));
    const OBSERVED_UNITS=[{sdl:'Test',gauges}],META={};
    const S={sel:{type:'none'},obs:{sdl:'Test',gauges:new Set(gauges.map(g=>g.id)),ranges:{},query:'',notice:'',job:'job',
      runIds:gauges.map(g=>g.id),status:{},progress:{stage:'Renamed download stage',stage_code:'gauges',done:12,total:16}}};
    const OBSERVED_AVAILABLE=true,esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    for(let i=1;i<=4;i++)S.obs.status['G'+i]={f:{status:'ok'},l:{status:'ok'}};
    for(let i=5;i<=9;i++)S.obs.status['G'+i]={f:{status:'ok'},l:{status:'error',message:'problem'}};
    S.obs.status.G10={f:{status:'empty'},l:{status:'empty'}};
    S.obs.status.G11={f:{status:'empty'},l:{status:'unsupported'}};
    S.obs.status.G12={f:{status:'error'},l:{status:'timeout'}};
    S.obs.status.G13={f:{status:'error'}};S.obs.status.G14={f:{status:'running'},l:{status:'ok'}};
  `,ctx);
  vm.runInContext(section('function observedPanel(){','function render(){'),ctx);
  return {ctx,run:code=>vm.runInContext(code,ctx),html:()=>vm.runInContext('observedPanel()',ctx)};
}
const observedStatusRows=html=>[...html.matchAll(/<article class="obs-gauge-status[^"]*"[^>]*>([\s\S]*?)<\/article>/g)].map(m=>m[1]);
test('observed panel: 16 rows, honest header, exact tally, and waiting/checking labels',()=>{
  const h=observedPanelHarness(),html=h.html(),rows=observedStatusRows(html);
  const tally='12 of 16 gauges finished · 4 still loading', breakdown='Both measurements returned: 4 · One returned: 5 · No records: 2 · Failed: 1';
  const footnote='Records returned does not mean full coverage of the selected period.';
  assert.doesNotMatch(html,/Checking gauges:|Renamed download stage/);
  assert.ok(html.includes(`>${tally}</div>`));assert.ok(html.includes(`>${breakdown}</div>`));assert.ok(html.includes(`>${footnote}</div>`));
  assert.doesNotMatch(html.replace(/<[^>]*>/g,' '),/\bcomplete\b|ignore this gauge/i); // visible text only; internal outcome names stay 'complete'
  assert.doesNotMatch(html,/\baria-(?:label|description)\s*=\s*"[^"]*\bcomplete\b[^"]*"/i);
  assert.match(observedStatusRows(html)[9],/class="obs-outcome[^>]*><span aria-hidden="true">–<\/span> No records/); // G10: both empty
  assert.match(html,/<progress class="obs-progress" max="16" value="12">/);
  assert.equal(rows.length,16);
  assert.match(rows[14],/<span aria-hidden="true">·<\/span> Waiting/);assert.match(rows[14],/<b>G15<\/b> · Gauge 15/);
  assert.match(rows[15],/<span aria-hidden="true">·<\/span> Waiting/);assert.match(rows[15],/<b>G16<\/b> · Gauge 16/);
  assert.match(rows[12],/<span aria-hidden="true">⟳<\/span> Checking…/);assert.match(rows[13],/<span aria-hidden="true">⟳<\/span> Checking…/);
  assert.doesNotMatch(html,/\d+ of \d+ variables/);
  assert.match(rows[0],/data-obs-measurement="f"[\s\S]*?<b>Flow<\/b>[\s\S]*?aria-label="Available"/);
  assert.match(rows[0],/data-obs-measurement="l"[\s\S]*?<b>Water level<\/b>[\s\S]*?aria-label="Available"/);
  assert.match(rows[4],/data-obs-measurement="l"[\s\S]*?Unexpected problem/);
  assert.equal((html.match(/data-obs-measurement=/g)||[]).length,32);
  assert.equal((html.match(/aria-live="polite"/g)||[]).length,1);
  assert.doesNotMatch(html,/<(?:section|article)[^>]*aria-live/);
  h.run('S.obs.progress.done=16');assert.ok(h.html().includes(tally));
  h.run('S.obs.progress={stage:"Preparing gauges",done:0,total:1}');
  assert.match(h.html(),/>Preparing gauges</);
  assert.doesNotMatch(h.html(),/0 of 1</);
  assert.doesNotMatch(h.html(),/Checking gauges:/);
});
test('observed panel: completion shows every gauge in outcome order, keeping the tally above the notice',()=>{
  const h=observedPanelHarness();
  h.run(`for(const id of ['G13','G14','G15','G16'])S.obs.status[id]={f:{status:'empty'},l:{status:'empty'}};
    S.obs.job=null;S.obs.runIds=[];S.obs.completed=true;S.obs.progress=null;S.obs.notice='Loaded 9 EWR results for Test.';`);
  const html=h.html(),tally='16 of 16 gauges finished', breakdown='Both measurements returned: 4 · One returned: 5 · No records: 6 · Failed: 1';
  assert.ok(html.includes(`>${tally}</div>`));assert.ok(html.includes(`>${breakdown}</div>`));
  assert.ok(html.indexOf(tally)<html.indexOf('Loaded 9 EWR results for Test.'));
  assert.equal(observedStatusRows(html).length,16);
  assert.doesNotMatch(html,/<progress/);
  assert.deepEqual([...html.matchAll(/data-obs-result="([^"]+)"/g)].map(m=>m[1]),['G12','G10','G11','G13','G14','G15','G16','G5','G6','G7','G8','G9','G1','G2','G3','G4']);
  assert.doesNotMatch(html,/data-obs-complete|Show \d+ complete/); // no folding until coverage is verified (Phase 3)
});
test('observed panel: running and cancelled views keep original order and never group successful rows',()=>{
  const h=observedPanelHarness(),ids=()=>[...h.html().matchAll(/data-obs-result="([^"]+)"/g)].map(m=>m[1]);
  const original=Array.from({length:16},(_,i)=>'G'+(i+1));
  assert.deepEqual(ids(),original);
  h.run('S.obs.status.G15={f:{status:"error"},l:{status:"error"}};S.obs.status.G5={f:{status:"ok"},l:{status:"ok"}}');
  assert.deepEqual(ids(),original);assert.doesNotMatch(h.html(),/data-obs-complete/);
  h.run('S.obs.displayIds=[...S.obs.runIds];S.obs.runIds=[];S.obs.job=null;S.obs.completed=false');
  assert.deepEqual(ids(),original);assert.doesNotMatch(h.html(),/data-obs-complete/);
  assert.match(h.html(),/>13 of 16 gauges finished · 3 not finished<\/div>/);
});
test('observed panel: measurement reasons stay in their own chip and raw details replace hover titles',()=>{
  const h=observedPanelHarness();
  h.run(`S.obs.runIds=['G1'];gauges[0].name='';S.obs.status.G1={
    f:{status:'error',message:'FileNotFoundError: ~/bom_water/cache/stations.json',retryable:false,error:{code:'STATION_LIST_SETUP_REQUIRED',retryable:false,attempts:1,max_attempts:3,diagnostic_id:'diag-f'}},
    l:{status:'error',message:'error_num 218: too many requests in queue',retryable:true,error:{code:'SERVICE_BUSY',retryable:true,attempts:3,max_attempts:3,retry_after_s:30,diagnostic_id:'diag-l'}}};`);
  const html=h.html(),row=observedStatusRows(html)[0];
  const chips=[...row.matchAll(/<span class="obs-measurement[^>]*data-obs-measurement="([fl])">([\s\S]*?)<\/span><\/span>/g)];
  assert.equal(chips.length,2);
  assert.match(chips[0][2],/Flow[\s\S]*Weather-bureau station list not set up/);assert.doesNotMatch(chips[0][2],/Data service busy/);
  assert.match(chips[1][2],/Water level[\s\S]*Data service busy/);assert.doesNotMatch(chips[1][2],/Weather-bureau/);
  assert.match(row,/Unnamed gauge/);
  assert.match(row,/<details class="obs-details" data-obs-details="G1"><summary aria-label="Details for gauge G1">Details<\/summary>/);
  const details=row.slice(row.indexOf('<details'));
  assert.match(details,/Flow[\s\S]*Status: error[\s\S]*FileNotFoundError: ~\/bom_water\/cache\/stations.json/);
  assert.match(details,/Water level[\s\S]*Status: error[\s\S]*error_num 218: too many requests in queue/);
  assert.doesNotMatch(row,/ title=/);
  const visible=row.replace(/<details class="obs-details"[\s\S]*?<\/details>/g,'');
  assert.doesNotMatch(visible,/variables|cache|BoM|source busy|error_num|FileNotFoundError/);
});
test('observed panel: retry counts only requested gauges, singular waiting and escaped fallback rows',()=>{
  const h=observedPanelHarness();
  h.run('S.obs.runIds=["G13"];S.obs.progress={stage:"Fetching gauges",done:0,total:1}');
  assert.match(h.html(),/>0 of 1 gauge finished · 1 still loading<\/div>/);
  assert.equal(observedStatusRows(h.html()).length,1);
  h.run('S.obs.runIds=["<gauge>"];S.obs.status["<gauge>"]={f:{status:"error",message:"<message>"},l:{status:"empty"}}');
  let html=h.html();assert.match(html,/&lt;gauge&gt;/);assert.match(html,/&lt;message&gt;/);assert.doesNotMatch(html,/<gauge>|<message>/);
  h.run('S.obs.runIds=[]');assert.ok(h.html().includes('&lt;gauge&gt;'));
  h.run('S.obs.status={};S.obs.runIds=[];S.obs.job=null');html=h.html();
  assert.equal(observedStatusRows(html).length,0);assert.doesNotMatch(html,/of 0 checked/);
});
test('observed panel: actions are visible per non-ok measurement and explanations precede labelled technical details',()=>{
  const h=observedPanelHarness();
  h.run('S.obs.runIds=["G5","G10","G11"]');
  const rows=observedStatusRows(h.html());
  assert.match(rows[0],/data-obs-measurement="l"[\s\S]*?<p class="obs-action">Retry; if it persists, copy details for support\.<\/p>/);
  assert.equal((rows[0].match(/class="obs-action"/g)||[]).length,1); // Flow is ok.
  assert.equal((rows[1].match(/class="obs-action"/g)||[]).length,2); // Both measurements are empty.
  assert.equal((rows[2].match(/class="obs-action"/g)||[]).length,1); // Unsupported has no action.
  const details=rows[0].slice(rows[0].indexOf('<details'));
  assert.match(details,/<dl class="obs-explanations"><dt>Water level<\/dt><dd><p>Something went wrong on our side\.<\/p><p>Retry; if it persists, copy details for support\.<\/p><\/dd><\/dl><div class="obs-technical"><b>Technical details<\/b>/);
  assert.match(details.slice(details.indexOf('<div class="obs-technical">')),/<dt>Water level<\/dt><dd>Status: error<pre>problem<\/pre>/);
  assert.doesNotMatch(details,/<dt>Flow<\/dt><dd><p>/);
  assert.match(h.html(),/id="obsRefresh"[^>]*>Refresh cached data<\/label>/);
});
test('observed panel: duplicate chip/why/action copy is omitted and prose is escaped',()=>{
  const h=observedPanelHarness();
  h.run(`S.obs.runIds=['G1'];S.obs.status.G1={f:{status:'ok'},l:{status:'error'}};
    const originalDescription=describeResult;
    describeResult=(v,x)=>v==='l'?{label:'Same explanation',why:' SAME   explanation ',action:'<Next step>',severity:'error',retryable:true}:originalDescription(v,x);`);
  let row=observedStatusRows(h.html())[0];
  assert.equal((row.match(/Same explanation/g)||[]).length,1);
  assert.doesNotMatch(row,/SAME   explanation|<Next step>/);
  assert.equal((row.match(/&lt;Next step&gt;/g)||[]).length,2); // Under the chip and inside Details.
  h.run(`describeResult=(v,x)=>({label:'Same explanation',why:' Same explanation ',action:'same explanation',severity:'error',retryable:true});`);
  row=observedStatusRows(h.html())[0];assert.doesNotMatch(row,/class="obs-action"/);
});
test('observed panel: decorative icons are hidden while a stand-alone success icon is labelled',()=>{
  const h=observedPanelHarness(),rows=observedStatusRows(h.html());
  assert.match(rows[0],/class="obs-outcome[^>]*><span aria-hidden="true">✓<\/span> Records returned/);
  assert.match(rows[0],/data-obs-measurement="f"><b>Flow<\/b> <span role="img" aria-label="Available">✓<\/span>/);
  assert.match(rows[4],/data-obs-measurement="l"><b>Water level<\/b> <span aria-hidden="true">✗<\/span> <span class="obs-reason">Unexpected problem/);
});
test('observed panel: retry count is gauges, not measurements, and excludes non-retryable results',()=>{
  const h=observedPanelHarness();
  h.run(`S.obs.job=null;S.obs.status={G1:{f:{status:'timeout',retryable:true},l:{status:'rate_limited',retryable:true}},G2:{f:{status:'error',retryable:false}},G3:{f:{status:'empty'}}};`);
  assert.match(h.html(),/data-obs="retry">Retry 1 failed<\/button>/);
  h.run('S.obs.status.G1.f.retryable=false;S.obs.status.G1.l.retryable=false');
  assert.doesNotMatch(h.html(),/data-obs="retry"/);
});
test('observedDetailsText: copies only raw non-ok measurements as plain text without mutating input',()=>{
  const run=observedHarness();
  const states={f:{status:'ok'},l:{status:'error',message:'<raw>\nsecond line'}};
  assert.equal(run(`observedDetailsText('G1','Gauge <one>',${JSON.stringify(states)})`),'Gauge: G1\nName: Gauge <one>\n\nWater level\nStatus: error\nMessage: <raw>\nsecond line');
  assert.match(run(`observedDetailsText('G1','',{f:{status:'empty',message:'nothing recorded'},l:{status:'unsupported',message:'unsupported'}})`),/Name: Unnamed gauge\n\nFlow\nStatus: empty\nMessage: nothing recorded\n\nWater level\nStatus: unsupported\nMessage: unsupported/);
  assert.equal(run(`(()=>{const s={f:{status:'error',message:'raw'}},before=JSON.stringify(s);observedDetailsText('G1','Name',s);return JSON.stringify(s)===before;})()`),true);
});

function observedRunHarness(){
  const ctx=vm.createContext({setTimeout:fn=>fn()}),listeners={};
  ctx.$=()=>({addEventListener:(event,handler)=>{listeners[event]=handler;}});
  vm.runInContext(`
    const S={sel:{type:'none'},obs:{sdl:'Test',gauges:new Set(['G1','G2']),filter:false,start:'',end:'',refresh:false,job:null,
      runIds:[],displayIds:['G1','G2'],completed:true,status:{G1:{f:{status:'ok'},l:{status:'ok'}}},ranges:{G1:{start:'2000-01-01',end:'2000-01-02'}},progress:{stage:'old'},notice:''}};
    const OBSERVED_UNITS=[{sdl:'Test',gauges:[{id:'G1'},{id:'G2'}]}],CUSTOM=[],VALK=[];
    const saved=[],snapshots=[],localStorage={setItem:(key,value)=>saved.push(JSON.parse(value))};
    function render(){snapshots.push(JSON.parse(JSON.stringify({job:S.obs.job,runIds:S.obs.runIds})));}
    function refresh(){render();}function flowsChanged(){}function fillPolicyOptions(){}function renderCustomList(){}
    function observedSdlIndex(){return 0;}function addCustom(){CUSTOM.push({P:1});}
  `,ctx);
  vm.runInContext(observedHelpers,ctx);
  vm.runInContext(section('function persistObserved(){','function observedSdlIndex(){'),ctx);
  vm.runInContext(section('let observedWatch=0;','function initObserved(){'),ctx);
  vm.runInContext(section('$("panel").addEventListener("click",event=>{const b=event.target.closest("[data-obs]")','$("panel").addEventListener("change",event=>{const t=event.target;'),ctx);
  return {ctx,listeners,run:code=>vm.runInContext(code,ctx)};
}
const observedResponse=(body,status=200)=>({ok:status<400,status,json:async()=>body});
for(const mode of ['clipboard','unavailable','denied'])test(`Copy details: ${mode} uses plain text or a focused selection fallback`,async()=>{
  const h=observedRunHarness(),copied=[];
  h.run(`OBSERVED_UNITS[0].gauges[0].name='Gauge <one>';S.obs.status.G1={f:{status:'ok'},l:{status:'error',message:'raw <error>'}};`);
  h.ctx.navigator=mode==='unavailable'?{}:{clipboard:{writeText:async text=>{if(mode==='denied')throw Error('denied');copied.push(text);}}};
  const field={value:'',focused:false,selected:false,focus(){this.focused=true;},select(){this.selected=true;}};
  const fallback={hidden:true,querySelector:()=>field};
  const button={dataset:{obs:'copyDetails',obsGaugeId:'G1'},textContent:'Copy details',closest:()=>({querySelector:()=>fallback})};
  await h.listeners.click({target:{closest:()=>button}});
  const zone=Intl.DateTimeFormat().resolvedOptions().timeZone; // the copied text names the period and the viewer's time zone
  const expected=`Gauge: G1\nName: Gauge <one>\nPeriod: all available data\nTime zone: ${zone}\n\nWater level\nStatus: error\nMessage: raw <error>`;
  if(mode==='clipboard'){assert.deepEqual(copied,[expected]);assert.equal(button.textContent,'Copied');assert.equal(fallback.hidden,true);}
  else{assert.equal(field.value,expected);assert.equal(fallback.hidden,false);assert.equal(field.focused,true);assert.equal(field.selected,true);assert.equal(button.textContent,'Select details to copy');}
});
for(const merge of [false,true])test(`runObserved: requested ids are copied and cleared on completion (merge=${merge})`,async()=>{
  const h=observedRunHarness(),requests=[];let start;
  h.ctx.fetch=async(url,options)=>{
    requests.push({url,options});
    if(url==='/api/observed/run')return new Promise(resolve=>{start=resolve;});
    return observedResponse({status:'done',rows:[],stats:{gauge_status:{G2:{f:{status:'ok'},l:{status:'empty'}}}}});
  };
  const pending=h.run(`var requestedIds=['G2'];runObserved(requestedIds,${merge})`);
  assert.deepEqual(plain(h.run('S.obs.runIds')),['G2']);
  assert.deepEqual(plain(h.run('S.obs.status')),merge?{G1:{f:{status:'ok'},l:{status:'ok'}}}:{});
  assert.deepEqual(plain(h.run('S.obs.ranges')),merge?{G1:{start:'2000-01-01',end:'2000-01-02'}}:{});
  assert.equal(h.run('S.obs.progress'),null);
  assert.deepEqual(plain(h.run('S.obs.displayIds')),merge?['G1','G2']:['G2']);
  assert.equal(h.run('S.obs.completed'),false);
  h.run('requestedIds.push("G1");persistObserved()');
  assert.deepEqual(plain(h.run('S.obs.runIds')),['G2']);
  assert.equal(h.run('Object.hasOwn(saved[0],"runIds")'),false);
  assert.equal(h.run('Object.hasOwn(saved[0],"displayIds")'),false);
  assert.equal(JSON.parse(requests[0].options.body).merge,merge);
  start(observedResponse({job:'job'}));await pending;
  assert.deepEqual(plain(h.run('[S.obs.runIds,S.obs.job]')),[[],null]);
  assert.match(h.run('S.obs.notice'),/^Loaded 0 EWR results/);
  assert.deepEqual(plain(h.run('S.obs.status.G2')),{f:{status:'ok'},l:{status:'empty'}});
  assert.equal(h.run('S.obs.completed'),true);
  assert.equal(h.run('Object.hasOwn(S.obs.status,"G1")'),merge);
});
for(const duringPoll of [false,true])test(`runObserved: failure clears requested ids (duringPoll=${duringPoll})`,async()=>{
  const h=observedRunHarness();
  h.ctx.fetch=async url=>url==='/api/observed/run'&&duringPoll?observedResponse({job:'job'}):observedResponse({status:'error',message:'unavailable'},503);
  await h.run('runObserved(["G1"])');
  assert.deepEqual(plain(h.run('[S.obs.runIds,S.obs.job]')),[[],null]);
  assert.match(h.run('S.obs.notice'),/^Failed: unavailable/);
  assert.deepEqual(plain(h.run('S.obs.displayIds')),['G1']);
  assert.equal(h.run('S.obs.completed'),false);
});
test('runObserved: cancel clears requested ids and a late start response cannot restore the job',async()=>{
  const h=observedRunHarness();let start;
  h.ctx.fetch=async()=>new Promise(resolve=>{start=resolve;});
  const pending=h.run('runObserved(["G1"])');
  h.listeners.click({target:{closest:()=>({dataset:{obs:'cancel'}})}});
  assert.deepEqual(plain(h.run('[S.obs.runIds,S.obs.job]')),[[],null]);
  start(observedResponse({job:'late-job'}));await pending;
  assert.deepEqual(plain(h.run('[S.obs.runIds,S.obs.job]')),[[],null]);
  assert.deepEqual(plain(h.run('S.obs.displayIds')),['G1']);
  assert.equal(h.run('S.obs.completed'),false);
});
test('runObserved: retry keeps the full gauge set and merges live and final results and ranges',async()=>{
  const h=observedRunHarness();let polls=0;
  h.run('S.obs.displayIds=["G1","G2","G3"];S.obs.status.G2={f:{status:"error"},l:{status:"error"}};S.obs.ranges.G2={start:"old",end:"old"}');
  h.ctx.fetch=async url=>{
    if(url==='/api/observed/run')return observedResponse({job:'retry'});
    if(!polls++)return observedResponse({status:'running',progress:{stage:'Fetching gauges',done:0,total:1,gauges:{G2:{f:{status:'ok'}}}}});
    assert.deepEqual(plain(h.run('S.obs.displayIds')),['G1','G2','G3']);
    assert.deepEqual(plain(h.run('S.obs.status.G1')),{f:{status:'ok'},l:{status:'ok'}});
    assert.deepEqual(plain(h.run('S.obs.status.G2')),{f:{status:'ok'}});
    assert.equal(h.run('Object.hasOwn(S.obs.ranges,"G2")'),false);
    return observedResponse({status:'done',rows:[],stats:{gauge_status:{G2:{f:{status:'ok'},l:{status:'empty'}}},observed_ranges:{G2:{start:'new',end:'new'}}}});
  };
  await h.run('runObserved(["G2"],true)');
  assert.deepEqual(plain(h.run('S.obs.displayIds')),['G1','G2','G3']);
  assert.deepEqual(plain(h.run('S.obs.status')),{G1:{f:{status:'ok'},l:{status:'ok'}},G2:{f:{status:'ok'},l:{status:'empty'}}});
  assert.deepEqual(plain(h.run('S.obs.ranges')),{G1:{start:'2000-01-01',end:'2000-01-02'},G2:{start:'new',end:'new'}});
});
test('runObserved: retry preserves scenario results for gauges outside the retry',async()=>{
  const h=observedRunHarness();
  h.run(`const META={sdls:['Test']},PUKEY=new Map([['PU|G1|F1',[[0,0]]],['PU|G2|F1',[[0,0]]]]);
    let performanceRevision=0;function reindexRows(){}function putVals(){}function persistCustom(){}
    VALK.push('pu','gauge','ewr','fr');
    CUSTOM.push({P:1,source:'observed',observedSdl:'Test',vals:[['PU','G1','F1',.8],['PU','G2','F1',.2]]});`);
  h.ctx.fetch=async url=>url==='/api/observed/run'?observedResponse({job:'retry'}):observedResponse({status:'done',rows:[{pu:'PU',gauge:'G2',ewr:'F1',fr:.9}],stats:{gauge_status:{G2:{f:{status:'ok'},l:{status:'empty'}}}}});
  await h.run('runObserved(["G2"],true)');
  assert.deepEqual(plain(h.run('CUSTOM[0].vals')),[['PU','G1','F1',.8],['PU','G2','F1',.9]]);
  assert.equal(h.run('S.obs.completed'),true);
});
test('runObserved: cancel keeps checked and waiting gauges and ignores late poll results',async()=>{
  const h=observedRunHarness();let polls=0,release,ready;
  const polling=new Promise(resolve=>{ready=resolve;});
  h.ctx.fetch=async url=>{
    if(url==='/api/observed/run')return observedResponse({job:'job'});
    if(!polls++)return observedResponse({status:'running',progress:{gauges:{G1:{f:{status:'ok'},l:{status:'ok'}}}}});
    return new Promise(resolve=>{release=resolve;ready();});
  };
  const pending=h.run('runObserved(["G1","G2","G3"])');await polling;
  h.listeners.click({target:{closest:()=>({dataset:{obs:'cancel'}})}});
  assert.deepEqual(plain(h.run('S.obs.displayIds')),['G1','G2','G3']);
  assert.deepEqual(plain(h.run('summarizeGauges(S.obs.displayIds,S.obs.status)')),{total:3,checked:1,complete:1,partial:0,nodata:0,failed:0,running:0,waiting:2});
  assert.equal(h.run('S.obs.completed'),false);
  release(observedResponse({status:'done',stats:{gauge_status:{G2:{f:{status:'ok'},l:{status:'ok'}}}}}));await pending;
  assert.deepEqual(plain(h.run('Object.keys(S.obs.status)')),['G1']);
  assert.deepEqual(plain(h.run('[S.obs.runIds,S.obs.job,S.obs.completed]')),[[],null,false]);
});
test('runObserved: a terminal error preserves its final gauge statuses and the full list',async()=>{
  const h=observedRunHarness();
  h.ctx.fetch=async url=>url==='/api/observed/run'?observedResponse({job:'job'}):observedResponse({status:'error',message:'No data could be fetched',gauge_status:{G1:{f:{status:'error'},l:{status:'error'}}}});
  await h.run('runObserved(["G1","G2"])');
  assert.deepEqual(plain(h.run('S.obs.displayIds')),['G1','G2']);
  assert.deepEqual(plain(h.run('S.obs.status.G1')),{f:{status:'error'},l:{status:'error'}});
  assert.equal(h.run('S.obs.completed'),false);
});

test('val: frequency ratio and ETP pass through, missing values stay missing',()=>{
  const run=harness([row(0,0,0,'A',[.8,null,.6,.7],[.2,.3,null,.4])]);
  assert.equal(run("val(ROWS[0],'fr',0)"),.8);
  assert.equal(run("val(ROWS[0],'fr',1)"),null);
  assert.equal(run("val(ROWS[0],'et',0)"),.2);
  assert.equal(run("val(ROWS[0],'et',2)"),null);
});
test('val: ETP above 1 is capped for et and mz, FR above 1 is not',()=>{
  const run=harness([row(0,0,0,'A',[1.4,0,0,0],[1.3,0,0,0])]);
  assert.equal(run("val(ROWS[0],'et',0)"),1);
  assert.equal(run("val(ROWS[0],'fr',0)"),1.4);
  assert.equal(run("S.ethr=1;val(ROWS[0],'mz',0)"),1);
});
test('val: binary metrics at and around their thresholds',()=>{
  const run=harness([row(0,0,0,'A',[1,1-1e-10,1-1e-8,null],[0,1e-10,1e-8,null])]);
  // ach: FR >= thr, with a 1e-9 tolerance for values that round just under it
  assert.deepEqual([0,1,2,3].map(i=>run(`val(ROWS[0],'ach',${i})`)),[1,1,0,null]);
  run('S.thr=.9'); assert.equal(run("val(ROWS[0],'ach',2)"),1);
  // mz: ETP <= ethr (0 = the maximum inter-event period is never exceeded)
  assert.deepEqual([0,1,2,3].map(i=>run(`val(ROWS[0],'mz',${i})`)),[1,1,0,null]);
  // fb: FR < fbthr is a bad failure; exactly at the threshold is not
  const fb=harness([row(0,0,0,'A',[.5,.5-1e-8,.5-1e-10,null])]);
  assert.deepEqual([0,1,2,3].map(i=>fb(`val(ROWS[0],'fb',${i})`)),[0,1,0,null]);
});
test('val: differential mode reports improvement from base to comparison for every metric',()=>{
  // index = policy*NC + climate: base (0,0) -> 0, comparison (1,0) -> 2
  const run=harness([row(0,0,0,'A',[.6,0,.9,0],[.4,0,.1,0])]);
  run("S.mode='diff'");
  near(run("val(ROWS[0],'fr')"),30);   // higher FR is better: comparison - base, in achieved years per century (.3 x a 100% target)
  near(run("val(ROWS[0],'et')"),.3);   // lower ETP is better: base - comparison
  run('S.thr=.8'); assert.equal(run("val(ROWS[0],'ach')"),1);   // 0 -> 1
  run('S.fbthr=.7'); assert.equal(run("val(ROWS[0],'fb')"),1);   // failed badly -> not: base 1 - comparison 0
  run('S.ethr=.2'); assert.equal(run("val(ROWS[0],'mz')"),1);   // 0 -> 1
  // the base and comparison come from S, not the index argument
  run('S.pol=0;S.cli=1;S.bpol=1;S.bcli=1'); assert.equal(run("val(ROWS[0],'fr',2)"),0);
});
test('val: differential mode is missing when either scenario is missing',()=>{
  const run=harness([row(0,0,0,'A',[.6,0,null,0],[null,0,.1,0])]);
  run("S.mode='diff'");
  for(const m of ['fr','et','ach','mz','fb']) assert.equal(run(`val(ROWS[0],'${m}')`),null,m);
});
test('agg: flat mean over EWRs ignores missing values and counts what it used',()=>{
  const run=harness([row(0,0,0,'A',[.2,0,0,0]),row(0,0,0,'B',[null,0,0,0]),row(0,0,0,'C',[.6,0,0,0])]);
  const a=plain(run("agg(ROWS,[],'fr',0)")); assert.equal(a.n,2); near(a.mean,.4);
  assert.deepEqual(plain(run("agg(ROWS.slice(1,2),[],'fr',0)")),{n:0,mean:null});
  assert.deepEqual(plain(run("agg([],['pu','gauge'],'fr',0)")),{n:0,mean:null});
});
test('agg: SDL mean weights planning units, then gauges, equally (not a flat mean of EWRs)',()=>{
  const rows=[
    row(0,0,0,'A',[1,0,0,0]),row(0,0,0,'B',[1,0,0,0]),row(0,0,0,'C',[1,0,0,0]),   // PU 0, gauge 0: mean 1
    row(0,0,1,'A',[0,0,0,0]),                                                     // PU 0, gauge 1: mean 0  -> PU 0: .5
    row(1,0,2,'A',[.9,0,0,0]),                                                    // PU 1, gauge 2: .9      -> PU 1: .9
  ];
  const run=harness(rows);
  const sdl=plain(run("agg(ROWS,['pu','gauge'],'fr',0)")); near(sdl.mean,.7); assert.equal(sdl.n,5);
  near(run("agg(ROWS,[],'fr',0)").mean,.78);   // the flat mean differs, which is the point
  near(run("agg(ROWS.filter(r=>r[0]===0),['gauge'],'fr',0)").mean,.5);
  // the result does not depend on row order
  near(run("agg([...ROWS].reverse(),['pu','gauge'],'fr',0)").mean,.7);
});
test('agg: a group with no values is left out of its parent mean rather than counted as zero',()=>{
  const run=harness([row(0,0,0,'A',[.8,0,0,0]),row(1,0,1,'A',[null,0,0,0]),row(1,0,2,'B',[null,0,0,0])]);
  const a=plain(run("agg(ROWS,['pu','gauge'],'fr',0)")); near(a.mean,.8); assert.equal(a.n,1);
});
test('agg: binary and differential metrics aggregate through the same hierarchy',()=>{
  const rows=[row(0,0,0,'A',[.5,0,1,0]),row(0,0,0,'B',[1,0,1,0]),row(1,0,1,'A',[1,0,.5,0])];
  const run=harness(rows);
  // ach at thr 1 (comparison, index 2): PU 0 (gauge 0) -> 1, PU 1 (gauge 1) -> 0; SDL .5
  near(run("agg(ROWS,['pu','gauge'],'ach',2)").mean,.5);
  // differential FR (achieved years per century): PU 0 = +50 (B meets the target in both, so it is left out), PU 1 = -50 -> SDL 0
  run("S.mode='diff'"); near(run("agg(ROWS,['pu','gauge'],'fr')").mean,0);
  assert.equal(run("agg(ROWS,['pu','gauge'],'fr')").n,2);
});
test('scopeLevels: each selection type averages over the levels beneath it',()=>{
  const run=harness();
  const levels=type=>plain(run(`S.sel={type:'${type}'};scopeLevels()`));
  assert.deepEqual(levels('sdl'),['pu','gauge']); assert.deepEqual(levels('pu'),['gauge']);
  assert.deepEqual(levels('gauge'),[]); assert.deepEqual(levels('none'),[]);
});
test('selRows: SDL, planning unit and gauge selections, including shared units',()=>{
  const rows=[
    row(0,0,0,'A',[1,0,0,0]),row(0,0,1,'B',[1,0,0,0]),
    row(0,1,1,'B',[1,0,0,0]),   // planning unit 0 and gauge 1 also sit in SDL 1
    row(1,1,2,'C',[1,0,0,0]),
  ];
  const run=harness(rows);
  const pick=sel=>plain(run(`S.sel=${sel};selRows().map(r=>ROWS.indexOf(r))`));
  assert.deepEqual(pick("{type:'none'}"),[]);
  assert.deepEqual(pick("{type:'sdl',i:0}"),[0,1]);
  assert.deepEqual(pick("{type:'sdl',i:1}"),[2,3]);
  assert.deepEqual(pick("{type:'pu',i:0,sdl:1}"),[2]);   // a planning unit is scoped to its SDL unit
  assert.deepEqual(pick("{type:'gauge',i:1}"),[1,2]);   // a gauge on its own: every SDL unit and planning unit
  assert.deepEqual(pick("{type:'gauge',i:1,sdl:null,pu:null}"),[1,2]);
  assert.deepEqual(pick("{type:'gauge',i:1,sdl:1}"),[2]);
  assert.deepEqual(pick("{type:'gauge',i:1,sdl:0,pu:0}"),[1]);
  assert.deepEqual(pick("{type:'gauge',i:1,sdl:0,pu:1}"),[]);
  assert.deepEqual(pick("{type:'sdl',i:5}"),[]);
});
test('cnts: differential changes under one point are unchanged',()=>{
  const run=harness([row(0,0,0,'A',[.5,0,.52,0]),row(0,0,0,'B',[.5,0,.505,0]),row(0,0,0,'C',[.5,0,.4,0]),row(0,0,0,'D',[null,0,.4,0])]);
  run("S.mode='diff'");
  assert.deepEqual(plain(run("cnts(ROWS,'fr')")),{up:1,dn:1,same:1,met:0,gain:0,loss:0,n:3});
});
test('diffClass: FR above target in both is met, crossings are capped at the target',()=>{
  // base (index 0) -> comparison (index 2)
  const run=harness([row(0,0,0,'A',[3,0,1.5,0]),row(0,0,0,'B',[1.2,0,.8,0]),row(0,0,0,'C',[.6,0,1.4,0]),row(0,0,0,'D',[.3,0,.5,0])]);
  run("S.mode='diff'");
  const k=i=>plain(run(`diffClass(ROWS[${i}],'fr')`));
  assert.equal(k(0).cls,'met'); assert.equal(k(0).d,null); near(k(0).sur,-1.5);   // the surplus is kept for tooltips only
  assert.equal(k(1).cls,'loss'); near(k(1).d,-.2);
  assert.equal(k(2).cls,'gain'); near(k(2).d,.4);
  assert.equal(k(3).cls,'below'); near(k(3).d,.2);
  assert.equal(run("diffClass([0,0,0,'E',[],[],[null,0,1,0],[0,0,0,0]],'fr')"),null);
  assert.equal(run("diffClass(ROWS[1],'ach').cls"),null);   // binary metrics keep their plain difference
});
test('diffClass: ETP is met at or below the MRIP threshold in both; lower is better',()=>{
  const run=harness([row(0,0,0,'A',[1,0,1,0],[0,0,0,0]),row(0,0,0,'B',[1,0,1,0],[.3,0,0,0]),row(0,0,0,'C',[1,0,1,0],[0,0,.2,0]),row(0,0,0,'D',[1,0,1,0],[.5,0,.2,0])]);
  run("S.mode='diff'");
  assert.deepEqual([0,1,2,3].map(i=>run(`diffClass(ROWS[${i}],'et').cls`)),['met','gain','loss','below']);
  near(run("diffClass(ROWS[1],'et').d"),.3); near(run("diffClass(ROWS[3],'et').d"),.3);
  run('S.ethr=.25'); assert.equal(run("diffClass(ROWS[2],'et').cls"),'met');
});
test('agg and cnts: met in both is out of the mean and of n; crossings count as improved / declined',()=>{
  const run=harness([row(0,0,0,'A',[3,0,1.5,0]),row(0,0,0,'B',[1.2,0,.8,0]),row(0,0,0,'C',[.6,0,1.4,0]),row(0,0,0,'D',[.3,0,.5,0])]);
  run("S.mode='diff'");
  const a=plain(run("agg(ROWS,[],'fr')")); assert.equal(a.n,3); near(a.mean,(-20+40+20)/3);
  near(run("agg(ROWS,[],'fr',0,dFR).mean"),(-.2+.4+.2)/3);   // the same changes in FR units (tooltips)
  assert.deepEqual(plain(run("agg(ROWS.slice(0,1),[],'fr')")),{n:0,mean:null});
  assert.deepEqual(plain(run("cnts(ROWS,'fr')")),{up:2,dn:1,same:0,met:1,gain:1,loss:1,n:3});
  // a crossing smaller than the 1-point band is still a gain
  const g=harness([row(0,0,0,'A',[.995,0,1.3,0])]); g("S.mode='diff'");
  assert.deepEqual(plain(g("cnts(ROWS,'fr')")),{up:1,dn:0,same:0,met:0,gain:1,loss:0,n:1});
  assert.equal(run("allMet(cnts(ROWS.slice(0,1),'fr'))"),true); assert.equal(run("allMet(cnts(ROWS,'fr'))"),false);
});
test('event-year band: a 1-year change is unchanged at K = 2 whatever the target',()=>{
  // T = 5%: 2 -> 3 achieved years of 100 (FR .4 -> .6); T = 100%: 50 -> 51 (FR .5 -> .51); neither crosses the target
  const run=harness([row(0,0,0,'A',[.4,0,.6,0],undefined,{T:5,ny:100}),row(0,0,0,'B',[.5,0,.51,0],undefined,{T:100,ny:100})]);
  run("S.mode='diff'");
  near(run("dYears(ROWS[0])"),1); near(run("dYears(ROWS[1])"),1);
  assert.deepEqual(plain(run("cnts(ROWS,'fr')")),{up:0,dn:0,same:2,met:0,gain:0,loss:0,n:2});
  run('S.band=1'); assert.equal(run("cnts(ROWS,'fr').up"),2);
  // the colour and mean: years per century of record (here 1 year of 100)
  near(run("val(ROWS[0],'fr')"),1); near(run("val(ROWS[1],'fr')"),1);
});
test('event-year band: crossings are capped at T % of the record, and ny scales to years per century',()=>{
  // T 50%, 129-year record: target = 64.5 years. 60 -> 80 achieved years crosses it: capped change 4.5 years, 3.49 per century
  const run=harness([row(0,0,0,'A',[60/64.5,0,80/64.5,0],undefined,{T:50,ny:129})]);
  run("S.mode='diff'");
  const k=plain(run("diffClass(ROWS[0],'fr')")); assert.equal(k.cls,'gain'); near(k.dyr,4.5); near(k.dy,4.5*100/129);
  // no achieved-years data: the change per century is ΔFR x T
  const legacy=harness([[0,0,0,'L',[],[],[.5,0,.6,0],[0,0,0,0],{T:40,ny:null}]]); legacy("S.mode='diff'");
  near(legacy("val(ROWS[0],'fr')"),4); assert.equal(legacy("cnts(ROWS,'fr').up"),1);   // no years to band on: more than 0.01 FR counts
});
test('ETP band: a change counts when it moves at least one year of record beyond MRIP',()=>{
  const run=harness([row(0,0,0,'A',[1,0,1,0],[.30,0,.295,0]),row(0,0,0,'B',[1,0,1,0],[.30,0,.28,0])]);   // 100 years: 0.5 and 2 years
  run("S.mode='diff'");
  assert.deepEqual(plain(run("cnts(ROWS,'et')")),{up:1,dn:0,same:1,met:0,gain:0,loss:0,n:2});
});
test('worst gap: the maximum, not the mean, and the EWR that sets it',()=>{
  const sev=(r,a)=>{ r[8]={T:100,ny:100,mrip:2}; r.push(a); return r; };
  const run=harness([sev(row(0,0,0,'A',[1,0,1,0]),[1.5,0,3.2,0]),sev(row(0,0,1,'B',[1,0,1,0]),[2.5,0,0.8,0]),sev(row(1,0,2,'C',[1,0,1,0]),[0.5,0,0.9,0]),row(1,0,3,'D',[1,0,1,0])]);
  const a=run("aggMax(ROWS,0)"); near(a.mean,2.5); assert.equal(a.who[3],'B'); assert.equal(a.n,3);   // D has no severity
  near(run("agg(ROWS,['pu','gauge'],'sev',2).mean"),3.2);   // the hierarchy does not average it
  run("S.mode='diff'");
  const d=run("aggMax(ROWS)"); near(d.mean,2.5-3.2); assert.equal(d.who[3],'A');   // the worst gap got longer: negative = worse
  assert.equal(run("diffClass(ROWS[2],'sev').cls"),'met');   // within the MRIP in both
  assert.equal(run("diffClass(ROWS[0],'sev').cls"),'below'); assert.equal(run("diffClass(ROWS[1],'sev').cls"),'gain'); near(run("diffClass(ROWS[1],'sev').d"),1.7);
  // changed when the worst gap moves at least a year: |Δ| x MRIP (2 years) >= 1
  assert.deepEqual(plain(run("cnts(ROWS,'sev')")),{up:1,dn:1,same:0,met:1,gain:1,loss:0,n:2});
  assert.deepEqual(plain(run("aggMax(ROWS.slice(2,3))")),{n:0,mean:null,who:null});   // every EWR within the MRIP in both: met
});
// a drought window for scenario index 0 and 2: {fr, et, sev, dr, n}
const withWin=(r,w)=>{ while(r.length<12) r.push(null); r[12]={mil:w}; return r; };
test('drought period: values come from the window; review results have none',()=>{
  const run=harness([withWin(row(0,0,0,'A',[.9,0,.8,0],[.1,0,.2,0]),{fr:[.5,0,1.2,0],et:[.4,0,0,0],sev:[3,0,.9,0],dr:[.7,0,1.1,0],n:[14,0,14,0]}),row(0,0,0,'B',[.9,0,.8,0])]);
  assert.equal(run("val(ROWS[0],'fr',0)"),.9);
  run("S.period='mil'");
  assert.equal(run("val(ROWS[0],'fr',0)"),.5); assert.equal(run("val(ROWS[0],'et',2)"),0); assert.equal(run("val(ROWS[0],'sev',0)"),3); assert.equal(run("val(ROWS[0],'dr',2)"),1.1);
  assert.equal(run("val(ROWS[1],'fr',0)"),null);   // no window results ("–")
  assert.equal(run("val(ROWS[0],'ach',2)"),1);
  // achieved years in the window: FR x T / 100 x n (T = 100% here), the window's years stand in for the record's
  near(run("eyOf(ROWS[0],0)"),7); assert.equal(run("nyOf(ROWS[0],0)"),14); assert.equal(run("nyFull(ROWS[0],0)"),100);
});
test('drought period: a window shorter than 100 / T years is out of the means; the band scales to the window',()=>{
  // T 5%: one achieved year expected per 20 years; a 14-year window is too short
  const run=harness([withWin(row(0,0,0,'A',[.9,0,.8,0],undefined,{T:5,ny:100}),{fr:[1.4,0,0,0],et:[0,0,0,0],sev:[1,0,1,0],dr:[1,0,1,0],n:[14,0,14,0]}),
    withWin(row(0,0,0,'B',[.9,0,.8,0],undefined,{T:50,ny:100}),{fr:[.5,0,.6,0],et:[0,0,0,0],sev:[1,0,1,0],dr:[1,0,1,0],n:[20,0,20,0]})]);
  run("S.period='mil'");
  assert.equal(run("shortWin(ROWS[0],0)"),true); assert.equal(run("val(ROWS[0],'fr',0)"),null); assert.equal(run("val(ROWS[0],'ach',0)"),null);
  assert.equal(run("agg(ROWS,[],'fr',0).n"),1);
  // B: 5 -> 6 achieved years of 20 (FR .5 -> .6 at 50%); band max(1, round(2 x 20 / 100)) = 1 year, so it counts as improved
  run("S.mode='diff'"); near(run("diffClass(ROWS[1],'fr').dyr"),1);
  assert.deepEqual(plain(run("cnts(ROWS.slice(1),'fr')")),{up:1,dn:0,same:0,met:0,gain:0,loss:0,n:1});
  run("S.period=''"); assert.equal(run("cnts(ROWS.slice(1),'fr').dn"),1);   // the full record tells another story: 45 -> 40 achieved years
});
test('duration ratio: higher is better, met at 1 in both, changed at 0.1',()=>{
  const dr=(r,a)=>{ r.push(null,a); return r; };
  const run=harness([dr(row(0,0,0,'A',[1,0,1,0]),[1.2,0,1.6,0]),dr(row(0,0,0,'B',[1,0,1,0]),[.5,0,.55,0]),dr(row(0,0,0,'C',[1,0,1,0]),[.6,0,1.1,0]),dr(row(0,0,0,'D',[1,0,1,0]),[.9,0,.7,0])]);
  near(run("agg(ROWS,[],'dr',0).mean"),(1.2+.5+.6+.9)/4);   // not capped like FR
  run("S.mode='diff'");
  assert.deepEqual([0,1,2,3].map(i=>run(`diffClass(ROWS[${i}],'dr').cls`)),['met','below','gain','below']);
  assert.deepEqual(plain(run("cnts(ROWS,'dr')")),{up:1,dn:1,same:1,met:1,gain:1,loss:0,n:3});
  assert.equal(run("fs2(.25,'dr')"),'+0.25');
});
test('agg: one scenario caps FR at the target in the mean, the per-EWR value is uncapped',()=>{
  const run=harness([row(0,0,0,'A',[.2,0,0,0]),row(0,0,0,'B',[1.8,0,0,0])]);
  near(run("agg(ROWS,[],'fr',0).mean"),.6);
  assert.equal(run("val(ROWS[1],'fr',0)"),1.8);
  near(run("agg(ROWS,[],'et',0).mean"),0);   // ETP is not capped beyond its own 0-1 range
});
test('differential labels: FR in achieved years per century, a share of EWRs in points',()=>{
  const run=harness();
  run("S.mode='diff'");
  assert.equal(run("f3(3.14,'fr')"),'+3.1');   // achieved years per century of record
  assert.equal(run("fs2(-3.14,'fr')"),'−3.1');
  assert.equal(run("fs2(.04,'fr')"),'0.0');   // a change that rounds to zero carries no sign
  assert.equal(run("fs2(.25,'ach')"),'+25 pts');
  assert.equal(run("fs2(-.1,'fb')"),'−10 pts');
  assert.equal(run("fs2(.03,'et')"),'+3.0 pts');   // % of record beyond MRIP: a difference of shares, with one decimal
  assert.equal(run("fs2(.032,'et')"),'+3.2 pts');
  assert.equal(run("fs2(-.5,'sev')"),'−0.5×');
  run("S.mode='single'");
  assert.equal(run("fs2(.9,'fr')"),'0.90'); assert.equal(run("fs2(.5,'ach')"),'50%'); assert.equal(run("f3(null,'fr')"),'–');
  assert.equal(run("f3(.237,'et')"),'23.7%'); assert.equal(run("fs2(1,'et')"),'100.0%');   // ETP as a % of the record, with one decimal
  assert.equal(run("f3(3.288,'sev')"),'3.29×');
});
test('textOnColour picks the label colour with the higher contrast on its fill',()=>{
  const ctx=vm.createContext({});
  vm.runInContext(section('// text drawn on a coloured fill','// ---- map'),ctx);
  const run=code=>vm.runInContext(code,ctx);
  assert.equal(run("textOnColour('#ffffff')"),'#111111');
  assert.equal(run("textOnColour('rgb(33,102,172)')"),'#ffffff');
  assert.equal(run("textOnColour('#f28e2b')"),'#111111');   // waterbird orange: white text fails here
  assert.equal(run("textOnColour('#888')"),'#111111');
  assert.ok(run("contrast('#6b7280','#ffffff')")>=4.5);   // the failed-year grey keeps white text and symbols legible
});

test('row indices rebuild in place for U_ codes, shared SDL rows and deletions',()=>{
  const ctx=vm.createContext({});
  vm.runInContext(`const META={pus:['PU'],gauges:['404216'],objTheme:[0],themes:['Fish','Unclassified']};
    const ROWS=[[0,0,0,'F1',[0],[],[null],[null],{},[null]]];`,ctx);
  vm.runInContext(section('// ---- row indices:','// ---- end row indices'),ctx);
  const run=code=>vm.runInContext(code,ctx);
  run('const refs=[sdlRows,gaugeRows,PUKEY]; const user=[0,0,0,"U_FRESH",[],[],[null],[null],{},[null]]; ROWS.push(user,[...user.slice(0,1),1,...user.slice(2)]);reindexRows();');
  assert.equal(run('refs[0]===sdlRows&&refs[1]===gaugeRows&&refs[2]===PUKEY'),true);
  assert.equal(run('PUKEY.get("PU|404216|U_FRESH").length'),2);
  assert.equal(run('gaugeRows[0].length'),3); assert.equal(run('sdlRows[1].length'),1);
  assert.deepEqual(plain(run('user.t')),[1]);
  vm.runInContext(section('function putVals(','function addCustom('),ctx);
  run('const NC=1; PUKEY.get("PU|404216|U_FRESH").forEach(r=>putVals(r,0,["PU","404216","U_FRESH",1.2,.1,3,4]));');
  assert.equal(run('user[6][0]'),1.2);
  run('ROWS.splice(1);reindexRows();reindexRows();');
  assert.equal(run('PUKEY.has("PU|404216|U_FRESH")'),false);
  assert.equal(run('gaugeRows[0].length'),1); assert.equal(run('Object.keys(sdlRows).length'),1);
});

test('Demo coverage and means exclude U_ rows; user mode includes current versions and removal restores aggregates',()=>{
  const normal=row(0,0,0,'F1',[.4,0,.4,0]);
  const user=row(0,0,0,'U_ONE',[1,0,1,0]);user.user='u_one';user.userVer=2;user[8].uewrVerAt={0:2,2:2};
  const run=harness([normal,user]);run("S.src='demo';S.sel={type:'gauge',i:0}");
  assert.equal(run('selRows().length'),1);near(run("agg(selRows(),[],'fr',0).mean"),.4);
  run("S.src='user'");assert.equal(run('selRows().length'),2);near(run("agg(selRows(),[],'fr',0).mean"),.7);
  run('ROWS[1].userVer=3');assert.equal(run("val(ROWS[1],'fr',0)"),null);near(run("agg(selRows(),[],'fr',0).mean"),.4);
  run('ROWS.pop()');assert.equal(run('selRows().length'),1);near(run("agg(selRows(),[],'fr',0).mean"),.4);
});

test('stale user results cannot leak achieved years, drought years or near-miss counts',()=>{
 const r=row(0,0,0,'U_STALE',[.8,.8,.8,.8]);r.user='u_stale';r.userVer=2;r[8].uewrVerAt={2:1};r[8].nyAt={2:100};r[8].nmAt={2:[3,5]};r[12]={dry:{fr:[.8,.8,.8,.8],n:[10,10,10,10]}};
 const run=harness([r]);assert.equal(run('eyOf(ROWS[0],2)'),null);assert.equal(run('nyOf(ROWS[0],2)'),null);assert.equal(run('nyFull(ROWS[0],2)'),null);
 run("S.period='dry'");assert.equal(run('eyOf(ROWS[0],2)'),null);assert.equal(run('nyOf(ROWS[0],2)'),null);
});

test('phase 1: empty wording, empty/failed separation and partial kinds (exact literals)',()=>{
  const run=observedHarness();
  const e=run(`describeResult('f',{status:'empty'})`);
  assert.equal(e.why,'No observations returned — river conditions for this period are unknown.');
  assert.equal(e.action,'Widen the date range to investigate, or record why this gauge is excluded.');
  assert.ok(Object.values(e).every(v=>!/ignore this gauge/i.test(String(v)))); // tripwire; supplements the exact checks above
  const mixed=run(`classifyGauge({f:{status:'empty'},l:{status:'error'}})`);
  assert.equal(mixed.outcome,'failed');assert.deepEqual(mixed.emptyVars,['f']);assert.deepEqual(mixed.failedVars,['l']);
  const noFlow=run(`classifyGauge({f:{status:'empty'},l:{status:'ok'}})`),noLevel=run(`classifyGauge({f:{status:'ok'},l:{status:'empty'}})`);
  assert.deepEqual([noFlow.outcome,noFlow.partialKind,noLevel.outcome,noLevel.partialKind],['partial','noflow','partial','nolevel']);
  assert.equal(run(`classifyGauge({f:{status:'running'},l:{status:'ok'}})`).outcome,'running');
  assert.equal(run('COMPLETE_LABEL'),'Records returned');
});
test('phase 1: partial badges name the missing measurement and no-flow sorts above no-level; Failed: 0 stays visible',()=>{
  const h=observedPanelHarness();
  h.run(`S.obs.job=null;S.obs.completed=true;S.obs.progress=null;S.obs.runIds=['NO_LEVEL','NO_FLOW','OK'];
    for(const id of ['NO_LEVEL','NO_FLOW','OK'])gauges.push({id,name:id});
    OBSERVED_UNITS[0].gauges=gauges;
    S.obs.status={NO_LEVEL:{f:{status:'ok'},l:{status:'empty'}},NO_FLOW:{f:{status:'empty'},l:{status:'ok'}},OK:{f:{status:'ok'},l:{status:'ok'}}};`);
  const html=h.html(),rows=observedStatusRows(html);
  assert.deepEqual([...html.matchAll(/data-obs-result="([^"]+)"/g)].map(m=>m[1]),['NO_FLOW','NO_LEVEL','OK']);
  assert.match(rows[0],/Partial — no flow/);assert.match(rows[1],/Partial — no water level/);assert.match(rows[2],/Records returned/);
  assert.ok(html.includes('>3 of 3 gauges finished</div>'));
  assert.ok(html.includes('>Both measurements returned: 1 · One returned: 2 · No records: 0 · Failed: 0</div>'));
});

// ---- Phase 2: error envelope, retry presentation, legacy errors -----------------------------------------------------
const busyEnvelope=(extra={})=>({status:'error',message:'busy',retryable:true,error:{code:'SERVICE_BUSY',retryable:true,retry_after_s:30,attempts:3,max_attempts:3,diagnostic_id:'d1',...extra}});
test('phase 2: SERVICE_BUSY cooldown starts once per failure, counts down, honours retry_after_s=0 and restarts on a new failure',()=>{
  const h=observedRunHarness(),ticks=[];
  h.ctx.setInterval=fn=>{ticks.push(fn);return 7;};h.ctx.clearInterval=()=>{ticks.length=0;};
  h.run('var T=1000;observedNow=()=>T;');
  const update=(id,x)=>h.run(`updateObservedStatus({G2:{f:${JSON.stringify(x)},l:{status:'ok'}}})`);
  update('G2',busyEnvelope());
  assert.equal(h.run('S.obs.cooldown["G2|f"].until'),31000);
  h.run('T=11000');update('G2',busyEnvelope()); // the same failure arrives again on the next poll
  assert.equal(h.run('S.obs.cooldown["G2|f"].until'),31000);
  assert.deepEqual(plain(h.run('retryPlan(["G2"],S.obs.status,S.obs.cooldown)')),{ready:[],waiting:1,waitSeconds:20});
  assert.equal(h.run('retryWaitLabel(20)'),'Data service busy — retry in 20s');assert.equal(h.run('retryWaitLabel(60)'),'Data service busy — retry in a minute');
  h.run('T=31000');assert.deepEqual(plain(h.run('retryPlan(["G2"],S.obs.status,S.obs.cooldown)')),{ready:['G2'],waiting:0,waitSeconds:0});
  update('G2',busyEnvelope({diagnostic_id:'d2',retry_after_s:0}));assert.equal(h.run('S.obs.cooldown["G2|f"].until'),31000); // 0 means no wait, not the 60 s default
  update('G2',busyEnvelope({diagnostic_id:'d3',retry_after_s:null}));assert.equal(h.run('S.obs.cooldown["G2|f"].until'),91000); // no hint: 60 s
  assert.equal(ticks.length,1); // one timer, not one per poll
});
test('phase 2: panel shows retry attempts, one rollup line per gauge, never answered, skip note and a disabled countdown Retry',()=>{
  const h=observedPanelHarness();
  h.run(`observedNow=()=>1000;S.obs.job=null;S.obs.completed=true;S.obs.progress=null;S.obs.runIds=['A','B','C','D'];
    for(const id of ['A','B','C','D'])gauges.push({id,name:id});
    (()=>{const to={status:'error',message:'t',retryable:true,error:{code:'NETWORK_TIMEOUT',retryable:true,attempts:3,max_attempts:3,diagnostic_id:'t'}};
      const setup={status:'error',message:'s',retryable:false,error:{code:'STATION_LIST_SETUP_REQUIRED',retryable:false,attempts:1,max_attempts:3,diagnostic_id:'s'}};
      const busy={status:'error',message:'b',retryable:true,error:{code:'SERVICE_BUSY',retryable:true,retry_after_s:30,attempts:3,max_attempts:3,diagnostic_id:'b'}};
      S.obs.status={A:{f:to,l:to},B:{f:setup,l:setup},C:{f:to,l:{status:'ok'}},D:{f:busy,l:busy}};})();`);
  let html=h.html(),rows=observedStatusRows(html);
  assert.ok(html.includes('>4 of 4 gauges finished · 1 never answered</div>')); // only A: every measurement timed out; setup/busy/partial are excluded
  assert.match(html,/data-obs="retry">Retry 3 failed<\/button>/);
  assert.equal((rows[rows.length-1].match(/Not retried — needs setup/g)||[]).length,0);
  const setupRow=rows.find(r=>/data-obs-result="B"/.test(r)||/<b>B<\/b>/.test(r));
  assert.equal((setupRow.match(/Not retried — needs setup/g)||[]).length,2);
  assert.match(setupRow,/Your IT support needs to run a one-time setup\. Copy details → send to IT\./);
  assert.match(rows.find(r=>/<b>A<\/b>/.test(r)),/We already retried 2 times\. Tried 3 times\./);
  assert.doesNotMatch(html,/\[contact\]/);
  h.run(`S.obs.status={D:S.obs.status.D};S.obs.runIds=['D'];S.obs.cooldown={'D|f':{diag:'b',until:31000},'D|l':{diag:'b',until:31000}};`);
  assert.match(h.html(),/<button[^>]*data-obs="retry" data-obs-wait disabled>Data service busy — retry in 30s<\/button>/);
  h.run('observedNow=()=>31000');assert.match(h.html(),/data-obs="retry">Retry 1 failed<\/button>/);
  // in flight: the retry label shows the attempt that is coming, one rollup line, and the gauge still counts as loading
  h.run(`S.obs.job='job';S.obs.completed=false;S.obs.runIds=['A'];S.obs.cooldown={};S.obs.status={A:{f:{status:'retry_wait',message:'t',retryable:true,attempts:1,max_attempts:3,error:{code:'NETWORK_TIMEOUT',retryable:true,attempts:1,max_attempts:3}},l:{status:'ok'}}};`);
  html=h.html();
  assert.match(html,/Flow: retrying \(2 of 3\) · Water level ✓/);
  assert.match(html,/Retrying automatically \(attempt 2 of 3\)…/);
  assert.ok(html.includes('>0 of 1 gauge finished · 1 still loading</div>'));
  assert.equal(observedStatusRows(html).length,1);
  assert.doesNotMatch(html,/never answered/);
});
test('phase 2: configuration errors name the configured contact, or "your IT support" when none is set',()=>{
  const run=observedHarness(),x={status:'error',error:{code:'CONFIGURATION_REQUIRED',retryable:false}};
  assert.equal(run(`describeResult('f',${JSON.stringify(x)})`).action,'This needs IT support. Use Copy details and send it to your IT support.');
  assert.equal(run(`describeResult('f',${JSON.stringify(x)}).skip`),'Not retried — needs setup');
  assert.equal(run(`describeResult('f',${JSON.stringify({status:'error',error:{code:'NETWORK_TIMEOUT',retryable:true,attempts:1}})}).why`),"The data service didn't answer in time.");
});
test('phase 2: Copy details carries code, attempts, diagnostic id, next step, period and time zone; legacy errors copy their message verbatim',()=>{
  const run=observedHarness(),states={f:{status:'error',message:'m',error:{code:'SERVICE_BUSY',retryable:true,attempts:3,max_attempts:3,diagnostic_id:'abc'}},l:{status:'ok'}};
  assert.equal(run(`observedDetailsText('G1','Name',${JSON.stringify(states)},{period:'2000-01-01 to 2000-02-01',timezone:'Australia/Sydney'})`),
    'Gauge: G1\nName: Name\nPeriod: 2000-01-01 to 2000-02-01\nTime zone: Australia/Sydney\n\nFlow\nStatus: error\nMessage: m\nCode: SERVICE_BUSY\nAttempts: 3 of 3\nDiagnostic ID: abc\nNext step: Retry when available.');
  assert.equal(run(`observedDetailsText('G1','Name',{f:{status:'error',message:'FileNotFoundError: ~/bom_water/cache/stations.json'},l:{status:'ok'}})`),'Gauge: G1\nName: Name\n\nFlow\nStatus: error\nMessage: FileNotFoundError: ~/bom_water/cache/stations.json');
});

test('phase 2: a gauge stays blocked until every measurement is out of cooldown, and the timer re-renders when the first gauge becomes ready',()=>{
  const run=observedHarness();
  const status={A:{f:busyEnvelope(),l:{status:'error',message:'t',retryable:true,error:{code:'NETWORK_TIMEOUT',retryable:true,attempts:3}}},B:{f:busyEnvelope(),l:{status:'ok'}}};
  const cooldown={'A|f':{diag:'a',until:30000},'B|f':{diag:'b',until:60000}};
  const plan=now=>plan_(now);function plan_(now){return run(`retryPlan(["A","B"],${JSON.stringify(status)},${JSON.stringify(cooldown)},${now})`);}
  assert.deepEqual(plan(1000),{ready:[],waiting:2,waitSeconds:29}); // A's ready Level timeout does not release its cooling Flow
  assert.deepEqual(plan(30000),{ready:['A'],waiting:1,waitSeconds:30});
  assert.equal(run('retryWaitLabel(300)'),'Data service busy — retry in 5 minutes');
  const h=observedRunHarness(),ticks=[];let renders=0;
  h.ctx.setInterval=fn=>{ticks.push(fn);return 7;};h.ctx.clearInterval=()=>{ticks.length=0;};
  const button={textContent:''};h.ctx.$=()=>({addEventListener(){},querySelector:()=>button});
  h.run(`var T=1000;observedNow=()=>T;OBSERVED_UNITS[0].gauges=[{id:'A'},{id:'B'}];
    S.obs.status={A:${JSON.stringify(status.A)},B:${JSON.stringify(status.B)}};S.obs.cooldown=${JSON.stringify(cooldown)};
    var renderCount=0;render=()=>{renderCount++;};ensureCooldownTick();`);
  assert.equal(ticks.length,1);
  h.run('T=2000');ticks[0]();assert.equal(h.run('renderCount'),0);assert.equal(button.textContent,'Data service busy — retry in 28s');
  h.run('T=30000');ticks[0]();assert.equal(h.run('renderCount'),1); // A became ready while B still waits: the button must be rebuilt
  h.run('T=61000');ticks[0]();assert.equal(h.run('renderCount'),2);assert.equal(ticks.length,0); // all expired: timer stops
});
test('phase 2: the "Not retried" note is withheld when the gauge is included in Retry, and detail beats message',()=>{
  const h=observedPanelHarness();
  h.run(`observedNow=()=>1000;S.obs.job=null;S.obs.completed=true;S.obs.progress=null;S.obs.runIds=['M'];gauges.push({id:'M',name:'M'});
    S.obs.status={M:{f:{status:'error',message:'legacy text',retryable:false,error:{code:'STATION_LIST_SETUP_REQUIRED',retryable:false,attempts:1,max_attempts:3,detail:'DETAIL WINS'}},l:{status:'error',message:'t',retryable:true,error:{code:'NETWORK_TIMEOUT',retryable:true,attempts:3,max_attempts:3}}}};`);
  const html=h.html();
  assert.match(html,/data-obs="retry">Retry 1 failed<\/button>/);
  assert.doesNotMatch(html,/Not retried — needs setup/); // Retry re-requests the whole gauge, so the claim would be false
  assert.match(html,/<pre>DETAIL WINS<\/pre>/);assert.doesNotMatch(html,/legacy text/);
});

// ---- Phase 3: coverage lines, the earned "Complete" label, folding and coverage sorting ---------------------------------
const coverageBlock=(over={})=>({units:'ML/d',coverage_status:'unassessed_quality_policy',coverage_pct:98,coverage_complete:null,expected_count:50,valid_count:49,
  last_reading:{timestamp:'2024-02-19',value:12,quality_code:10},gaps:[{start:'2024-01-25',end_exclusive:'2024-01-26',missing_count:1}],gaps_truncated:false,
  zero_count:0,zero_days_count:0,all_zero:false,excluded_quality_count:0,unknown_quality_count:49,period:{start:'2024-01-01',end_exclusive:'2024-02-20',timezone:null},...over});
const okWith=over=>({status:'ok',message:'',retryable:false,error:null,attempts:1,max_attempts:3,schema_version:2,coverage:coverageBlock(over)});
const completeCoverage={coverage_status:'assessed',coverage_pct:100,coverage_complete:true,valid_count:50,gaps:[],unknown_quality_count:0,policy_version:'example-only-v1'};
const completeFor=v=>okWith({...completeCoverage,units:v==='l'?'m':'ML/d'});
test('phase 3: coverage lines state facts only; 98 % and an unapproved 100 % are never ticked, and dates ignore the time zone',()=>{
  const run=observedHarness(), lines=(v,x)=>run(`coverageLines('${v}',${JSON.stringify(x)})`).map(l=>l.text);
  assert.deepEqual(lines('f',okWith()),['Flow · 98% of days have a usable reading · quality policy pending · last usable reading 19 Feb 2024','⚠ 1 gap (1 day missing)']);
  assert.deepEqual(lines('f',okWith({coverage_pct:100,gaps:[],valid_count:50})),['Flow · 100% of days have a usable reading · quality policy pending · last usable reading 19 Feb 2024']); // no policy => no tick
  assert.deepEqual(lines('l',okWith({...completeCoverage,units:'m'})),['Water level ✓ 100% · last usable reading 19 Feb 2024']);
  assert.deepEqual(lines('f',okWith({zero_count:12,all_zero:false,gaps:[]})).slice(1),['⚠ 0 ML/d recorded on 12 days — check quality codes']);
  assert.deepEqual(lines('f',okWith({zero_count:3,all_zero:true,gaps:[]})).slice(1),['⚠ Every reading was 0 ML/d — check quality codes']);
  assert.deepEqual(lines('f',okWith({coverage_status:'unassessed_open_period',coverage_pct:null,expected_count:null,gaps:null})),['Flow · coverage not assessed for all available data · last usable reading 19 Feb 2024']);
  for(const x of [{status:'error',coverage:coverageBlock({coverage_status:'unavailable_error'})},{status:'running',coverage:null},{status:'retry_wait',coverage:coverageBlock()},{status:'ok'},{status:'ok',coverage:[]},undefined])assert.deepEqual(lines('f',x),[]);
  // an empty measurement really is 0 % available: show it rather than hide the distinction from "not assessed"
  assert.deepEqual(lines('f',{status:'empty',coverage:coverageBlock({coverage_pct:0,valid_count:0,last_reading:null,gaps:[{start:'2024-01-01',end_exclusive:'2024-01-04',missing_count:3}]})}),['Flow · 0% of days have a usable reading · quality policy pending','⚠ 1 gap (3 days missing)']);
  assert.deepEqual([99.6,100,0,null,NaN,'98'].map(p=>run(`pctText(${JSON.stringify(p)===undefined?'undefined':p===null?'null':Number.isNaN(p)?'NaN':JSON.stringify(p)})`)),['99%','100%','0%','','','']);
  assert.equal(run("formatDay('2024-02-19')"),'19 Feb 2024');assert.equal(run("formatDay('2024-1-9')"),'');assert.equal(run("dayBefore('2024-03-01')"),'2024-02-29');
});
test('phase 3: Complete needs BOTH measurements fully backed by coverage; folding additionally needs no warnings',()=>{
  const run=observedHarness(), call=(fn,states)=>run(`${fn}(${JSON.stringify(states)})`), f=completeFor('f'), l=completeFor('l');
  assert.equal(call('gaugeComplete',{f,l}),true);assert.equal(call('gaugeFoldable',{f,l}),true);
  for(const states of [{},{f},{f,l:{status:'ok'}},{f,l:okWith({...completeCoverage,units:'m',coverage_complete:null})},{f,l:{status:'empty'}},{f,l:completeFor('f')},  // wrong units
    {f,l:okWith({...completeCoverage,units:'m',coverage_status:'unassessed_quality_policy'})},{f,l:okWith({...completeCoverage,units:'m',policy_version:''})},{f,l:okWith({...completeCoverage,units:'m',expected_count:0,valid_count:0})},
    {f,l:okWith({...completeCoverage,units:'m',coverage_pct:99.9})},{f,l:okWith({...completeCoverage,units:'m',gaps:null})},{f,l:okWith({...completeCoverage,units:'m',gaps:[{start:'2024-01-02',end_exclusive:'2024-01-03',missing_count:1}]})}])assert.equal(call('gaugeComplete',states),false,JSON.stringify(states).slice(0,120));
  // legitimate zeros keep "Complete" but stop the fold, so the warning stays visible
  const zeros={f:okWith({...completeCoverage,zero_count:2}),l};
  assert.equal(call('gaugeComplete',zeros),true);assert.equal(call('gaugeFoldable',zeros),false);
  assert.equal(run(`coverageSortKey(${JSON.stringify({f:okWith({coverage_pct:80}),l:okWith({coverage_pct:60})})})`),60);
  assert.equal(run(`coverageSortKey(${JSON.stringify({f:okWith({coverage_pct:80}),l:{status:'ok'}})})`),-1); // unknown first, null never coerced to 0
});
test('phase 3: Complete is earned, verified gauges fold, and the rest sort unknown-first then lowest coverage then name',()=>{
  const h=observedPanelHarness(), doneF=completeFor('f'), doneL=completeFor('l');
  h.run(`S.obs.job=null;S.obs.completed=true;S.obs.progress=null;S.obs.runIds=['P98','V','P50','U'];
    for(const [id,name] of [['P98','Zed'],['V','V'],['P50','Alpha'],['U','Bravo']])gauges.push({id,name});
    S.obs.status={P98:{f:${JSON.stringify(okWith())},l:${JSON.stringify(okWith({units:'m'}))}},V:{f:${JSON.stringify(doneF)},l:${JSON.stringify(doneL)}},
      P50:{f:${JSON.stringify(okWith({coverage_pct:50,gaps:[],valid_count:25}))},l:${JSON.stringify(okWith({coverage_pct:90,gaps:[]}))}},U:{f:{status:'ok'},l:{status:'ok'}}};`);
  const html=h.html(), rows=observedStatusRows(html), ids=[...html.matchAll(/data-obs-result="([^"]+)"/g)].map(m=>m[1]);
  assert.deepEqual(ids,['U','P50','P98','V']); // unknown coverage, then 50 %, then 98 %, then the folded verified gauge
  assert.match(html,/<details class="obs-complete" data-obs-complete><summary>1 gauge fully covered ▸<\/summary>/);
  assert.match(rows[3],/<span aria-hidden="true">✓<\/span> Complete/);
  for(const i of [0,1,2])assert.match(rows[i],/<span aria-hidden="true">✓<\/span> Records returned/);
  assert.match(html,/Water level · 98% of days have a usable reading · quality policy pending/);
  assert.ok(html.includes('>Records returned does not mean full coverage of the selected period.</div>'));
  assert.ok(html.includes('>Assessed period: 1 Jan 2024 to 19 Feb 2024</div>'));
  // a retry that mixed assessment periods must not present one undifferentiated fully-covered bucket
  h.run("S.obs.status.V.f.coverage.period={start:'2024-01-01',end_exclusive:'2024-03-01',timezone:null}");
  const mixed=h.html();assert.doesNotMatch(mixed,/data-obs-complete/);assert.match(mixed,/Gauges were assessed over different periods/);
  assert.match(observedStatusRows(mixed).find(r=>/<b>V<\/b>/.test(r)),/<\/span> Complete/); // its own badge is still true for its own coverage
  h.run('S.obs.completed=false;S.obs.job="job"'); // while loading: no fold and no reordering (V still earned its own badge)
  assert.doesNotMatch(h.html(),/data-obs-complete/);
  assert.deepEqual([...h.html().matchAll(/data-obs-result="([^"]+)"/g)].map(m=>m[1]),['P98','V','P50','U']);
});
test('phase 3: Details list the first 10 gaps while Copy details lists them all, with the assessed period and no invented time zone',()=>{
  const gaps=Array.from({length:12},(_,i)=>({start:`2024-01-${String(2*i+1).padStart(2,'0')}`,end_exclusive:`2024-01-${String(2*i+2).padStart(2,'0')}`,missing_count:1}));
  const run=observedHarness(), x=okWith({gaps,valid_count:38,coverage_pct:76,expected_count:50,zero_count:2});
  const panel=run(`coverageDetailText('Flow',${JSON.stringify(x)},10)`), copy=run(`observedDetailsText('G1','Name',{f:${JSON.stringify(x)},l:{status:'ok'}})`);
  assert.match(panel,/Gaps \(12\):/);assert.equal((panel.match(/\(1 day\)/g)||[]).length,10);assert.match(panel,/…and 2 more/);
  assert.equal((copy.match(/\(1 day\)/g)||[]).length,12);assert.doesNotMatch(copy,/…and/);
  assert.match(run(`coverageDetailText('Flow',${JSON.stringify(okWith({period:{start:'2024-01-02',end_exclusive:'2024-01-02',timezone:null},expected_count:0,valid_count:0,coverage_pct:null,gaps:[]}))})`),/No reporting days fall within this assessment period[\s\S]*No reporting days assessed/);
  assert.match(run(`coverageDetailText('Flow',${JSON.stringify(okWith({gaps:null,excluded_quality_count:null}))})`),/Gaps not assessed[\s\S]*Unavailable excluded/);
  assert.match(run(`coverageDetailText('Flow',${JSON.stringify(okWith({policy_version:'p1',unknown_quality_count:3}))})`),/3 with missing or unrecognised quality codes/);
  for(const text of [panel,copy]){assert.match(text,/Usable days: 38 of 50 \(76%\)/);assert.match(text,/Period: 1 Jan 2024 to 19 Feb 2024/);assert.match(text,/Time zone: not recorded \(daily values\)/);assert.match(text,/Zero readings: 2/);}
});
