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
// ROW = [planningUnit, sdl, gauge, ewr, [objectives], [regimes], fr[P*NC], etp[P*NC]]; two policies x two climates
const row = (pu,sdl,gauge,code,fr,et=fr.map(()=>0)) => [pu,sdl,gauge,code,[],[],fr,et];
const plain = value => JSON.parse(JSON.stringify(value));
function harness(rows=[]) {
  const ctx = vm.createContext({});
  ctx.ROWS = rows;
  vm.runInContext(`
    const META={climates:['c0','c1'],policies:['Pre Basin Plan','Basin plan full implementation']};
    const NC=META.climates.length;
    const S={thr:1.0,ethr:0,fbthr:0.5,mode:'single',pol:1,cli:0,bpol:0,bcli:0,metric:'fr',sel:{type:'none'}};
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
  near(run("val(ROWS[0],'fr')"),.3);   // higher FR is better: comparison - base
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
  // differential FR: PU 0 = mean(+.5, 0) = .25, PU 1 = -.5 -> SDL -.125
  run("S.mode='diff'"); near(run("agg(ROWS,['pu','gauge'],'fr')").mean,-.125);
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
  assert.deepEqual(plain(run("cnts(ROWS,'fr')")),{up:1,dn:1,same:1,n:3});
});
