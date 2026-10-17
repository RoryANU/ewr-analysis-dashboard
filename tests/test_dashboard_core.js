// node --test tests/test_dashboard_core.js; no browser, network, or third-party packages required.
// The headline numbers of the page: per-EWR values (val), the hierarchical mean (agg) and the selection (selRows),
// run from the template source against a small synthetic data set.
const fs = require('node:fs'), vm = require('node:vm');
const assert = require('node:assert/strict'), {test} = require('node:test');
const template = fs.readFileSync('src/dashboard_template.html','utf8');
// the "values and averages" and "selection" sections; each marker must occur once so a template edit fails here, not silently
function section(from,to) {
  for (const m of [from,to]) assert.equal(template.split(m).length,2,`marker ${JSON.stringify(m)} must occur exactly once in the template`);
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
  assert.equal(run("fs2(.03,'et')"),'+3 pts');   // % of record beyond MRIP: a difference of shares
  assert.equal(run("fs2(-.5,'sev')"),'−0.5×');
  run("S.mode='single'");
  assert.equal(run("fs2(.9,'fr')"),'0.90'); assert.equal(run("fs2(.5,'ach')"),'50%'); assert.equal(run("f3(null,'fr')"),'–');
  assert.equal(run("f3(.237,'et')"),'24%'); assert.equal(run("fs2(1,'et')"),'100%');   // ETP as a % of the record, no decimals
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
