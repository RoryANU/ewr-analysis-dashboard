const fs = require('node:fs'), vm = require('node:vm');
const assert = require('node:assert/strict'), {test} = require('node:test');
const source = fs.readFileSync('src/ewr_analysis/dashboard_template.html', 'utf8');
const part = (a,b) => source.slice(source.indexOf(a), source.indexOf(b,source.indexOf(a)));
const plain = x => JSON.parse(JSON.stringify(x));
function harness(diff=false) {
  const ewrs=[
    {code:'A',pu:'One',thr:75,dur:3,sm:7,em:6,consecutive:true,target:50},
    {code:'B',pu:'Two',thr:25,dur:5,sm:7,em:6,consecutive:false,target:80},
    {code:'C',pu:'One',thr:10,dur:2,sm:10,em:3,consecutive:true},
  ];
  const nodes={};
  for(const id of ['heatEwrs','heatEwrS','heatEwrClear','heatEwrD','flowPlot','flowPlot2','flowMsg','flowVolNote']) nodes[id]={style:{},textContent:'',innerHTML:'',on(event,fn){this[event]=fn;},removeAllListeners(){},insertAdjacentHTML(_,html){this.innerHTML+=html;},querySelectorAll(){return [];}};
  const ctx=vm.createContext({ewrs,nodes,console,encodeURIComponent,setTimeout,clearTimeout});
  vm.runInContext(`
    const S={sel:{i:0},flow:{chart:'heat',open:true,gaugeFor:'G',heatEwrs:['A|One','B|Two'],ewr:'A|One',thr:[],sm:7,em:6,days:'consec',dmax:5,xlim:'100',show:'diff'}};
    const META={gauges:['G']}, MONTHS=Array.from({length:12},(_,i)=>String(i+1)), EWRLIST=new Map([['G',ewrs]]);
    const $=id=>nodes[id], flowEwrsFor=()=>ewrs, thrKey=e=>e.code+'|'+e.pu, esc=String;
    const byThrE=(a,b)=>a.thr-b.thr, niceUp=Math.ceil, render=()=>{}, famOf=()=> 'flow', isDiff=()=>${diff};
    const flowCtx=()=>({ok:true,gauge:'G',base:'base',comp:${diff ? "'comp'" : 'null'},bn:'Base',cn:'Comparison'});
    const document={querySelector:()=>null}, window={}, heatCols=()=>60, BLUES=[], scale=x=>x, divScale=()=>[], TCOLS=['red','blue'];
    const r1=x=>x.toFixed(1), sgn=x=>(x>=0?'+':'')+x;
    let FLOWTOKEN=0, requested='', plotted;
    const flowGet=async url=>{requested=url;return {thresholds:[0,50,100],durations:[1,2,3,4,5],xlim:100,xmin:0,spacing:50,years:2,dates:['2000-07-01','2002-06-30'],consecutive:true,
      base:Array.from({length:5},()=>[100,50,0]),comp:Array.from({length:5},()=>[100,0,0]),diff:Array.from({length:5},()=>[0,-50,0]),
      marks:[{thr:75,dur:3,values:[50,0]},{thr:25,dur:5,values:[100,50]}]};};
    const Plotly={newPlot:async(id,traces,layout)=>{plotted={traces,layout};nodes[id].layout=layout;},purge:()=>{}};
  `,ctx);
  vm.runInContext(part('const sameWin=', 'const curThr='),ctx);
  vm.runInContext(part('function contourLevels(', 'const niceUp='),ctx);
  vm.runInContext(part('async function fillEwrSelect(', '// ---- annual volumes'),ctx);
  vm.runInContext(part('async function drawFlow(', '// ---- user scenarios:'),ctx);
  return {ctx,nodes,run:code=>vm.runInContext(code,ctx)};
}
test('heatmap selection drops missing and incompatible windows; manual timing changes clear stale selections',()=>{
  const {run}=harness();
  run("S.flow.heatEwrs=['A|One','C|One','missing','B|Two']");
  assert.deepEqual(plain(run('curHeatEwrs().map(e=>e.code)')),['A','B']);
  run('S.flow.sm=10; S.flow.em=3');
  assert.deepEqual(plain(run('curHeatEwrs()')),[]);
});
test('picker disables other timing windows and clearing unlocks them',async()=>{
  const {run,nodes}=harness();
  await run('fillEwrSelect()');
  assert.match(nodes.heatEwrs.innerHTML,/value="C\|One" disabled/);
  assert.match(nodes.heatEwrs.innerHTML,/value="B\|Two" checked/);
  nodes.heatEwrClear.onclick();
  await run('fillEwrSelect()');
  assert.doesNotMatch(nodes.heatEwrs.innerHTML,/ disabled/);
  assert.equal(nodes.heatEwrS.textContent,'none');
});
for(const diff of [false,true]) test(`heatmap draws all selected EWR points and exact result rows (${diff?'difference':'single'})`,async()=>{
  const {run,nodes}=harness(diff);
  await run('drawFlow()');
  assert.match(run('requested'),/mthr=75,25&mdur=3,5/);
  assert.equal(run('plotted.layout.annotations.length'),2);
  assert.equal(run('plotted.layout.shapes.length'),4);
  assert.equal(run('plotted.traces.filter(t=>t.type==="scatter").length'),2);
  assert.match(nodes.flowMsg.innerHTML,/<b>A<\/b>/);
  assert.match(nodes.flowMsg.innerHTML,/<b>B<\/b>/);
  assert.match(nodes.flowMsg.innerHTML,/50%/);
  assert.match(nodes.flowMsg.innerHTML,/100%/);
  run('nodes.flowPlot.layout.annotations[1].ax=80;nodes.flowPlot.layout.annotations[1].ay=-50');
  nodes.flowPlot.plotly_relayout({'annotations[1].ax':80});
  assert.deepEqual(plain(run("S.flow.heatLabelOffsets['B|Two']")),[80,-50]);
});
