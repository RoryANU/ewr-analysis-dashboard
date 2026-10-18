// node --test tests/test_ewr_guide.js; no browser, network, or third-party packages required.
// The EWR guide (left panel): search, the 5-EWR cap, EWR window dates, and the illustrative tutorial record, for every row of the parameter sheet.
const fs = require('node:fs'), vm = require('node:vm');
const assert = require('node:assert/strict'), {test} = require('node:test');
const html = fs.readFileSync('ewr_analysis_dashboard.html','utf8');
const source = fs.readFileSync('src/ewr_guide.js','utf8');
const idx = JSON.parse(html.match(/^const EWRIDX=(\{.*\});/m)[1]);
const window_ = {};
const ctx = vm.createContext({EWRIDX:idx, console, window:window_, document:{getElementById:() => null},
  esc: s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])), matchMedia: () => ({matches:false}), performance});
vm.runInContext(source + ';this.api={ewrRows,ewrSpec,buildTutorial,searchEwrs,toggleSelection,wyDay,ewrTitle,regimeName,ewrSupported,GUIDE_MAX};', ctx);
const api = ctx.api, guide = window_.ewrGuideApi;
const rows = guide.rows;
const plain = v => JSON.parse(JSON.stringify(v));

test('the page embeds one row per parameter-sheet row, with unique keys', () => {
  assert.equal(rows.length, idx.n);
  assert.equal(new Set(rows.map(r => r.key)).size, rows.length);
  const r = rows.find(x => x.key === '425008|BF1_a|Tilpa to Wilcannia');
  assert.equal(r.st, 'NSW'); assert.equal(r.tmin, 350); assert.equal(r.k, 'F');
});
test('the dropdown title follows the "Flow analysis · gauge · name" style', () => {
  const r = rows.find(x => x.key === '425008|BF1_a|Tilpa to Wilcannia');
  assert.match(api.ewrTitle(r), /^EWR · BF1_a · 425008 · Darling River at Wilcannia/);
});
test('search filters by state, planning unit, gauge, code, kind and free text', () => {
  const all = api.searchEwrs(rows, {});
  assert.equal(all.length, rows.length);
  const nsw = api.searchEwrs(rows, {state:'NSW'});
  assert.ok(nsw.length > 0 && nsw.every(r => r.st === 'NSW'));
  assert.ok(api.searchEwrs(rows, {gauge:'425008'}).every(r => r.g === '425008'));
  assert.ok(api.searchEwrs(rows, {gauge:'wilcannia'}).length > 0);
  assert.ok(api.searchEwrs(rows, {code:'bf1_a'}).every(r => r.c.toLowerCase().includes('bf1_a')));
  const pu = rows[0].pu;
  assert.ok(api.searchEwrs(rows, {pu}).every(r => r.pu === pu));
  assert.ok(api.searchEwrs(rows, {kind:'V'}).every(r => r.k === 'V'));
  const both = api.searchEwrs(rows, {text:'wilcannia base flow'});
  assert.ok(both.length > 0 && both.every(r => /^BF/.test(r.c)));
  assert.equal(api.searchEwrs(rows, {text:'zzzz-no-such-ewr'}).length, 0);
});
test('at most 5 EWRs can be selected; a 6th is refused; removing frees a slot', () => {
  let sel = [];
  for (let i = 0; i < 5; i++) { const r = api.toggleSelection(sel, 'k' + i); assert.ok(r.added); sel = r.sel; }
  const sixth = api.toggleSelection(sel, 'k5');
  assert.ok(sixth.full && !sixth.added); assert.deepEqual(plain(sixth.sel), plain(sel));
  const removed = api.toggleSelection(sel, 'k2');
  assert.equal(removed.sel.length, 4);
  assert.ok(api.toggleSelection(removed.sel, 'k5').added);
});
test('EWR windows: month.day text, whole months, wrapping the July–June year', () => {
  assert.equal(api.wyDay(7, 1), 0); assert.equal(api.wyDay(6, 30), 364); assert.equal(api.wyDay(1, 1), 184);
  const spec = key => api.ewrSpec(rows.find(r => r.key === key));
  const whole = spec('425008|BF1_a|Tilpa to Wilcannia');
  assert.deepEqual(plain(whole.segs), [[0, 364]]); assert.ok(whole.whole);
  const nest = spec('410001|NestS1_mc|Murrumbidgee River_Tumut River Junction to Berembed Weir');
  assert.deepEqual(plain(nest.segs), [[api.wyDay(10, 1), api.wyDay(11, 15)]]);
  const wrap = api.ewrSpec({...rows[0], sm: 6, sd: 1, em: 8, ed: null});
  assert.equal(wrap.segs.length, 2);
});
test('only flow_handle and lowflow_handle EWRs (py_ewr calc config) get a tutorial', () => {
  const by = {};
  for (const r of rows) by[r.h] = (by[r.h] || 0) + 1;
  assert.ok(by.flow_handle > 1000 && by.lowflow_handle > 400 && by.cumulative_handle > 0 && by.ctf_handle > 0, JSON.stringify(by));
  for (const r of rows) assert.equal(api.ewrSupported(r), r.h === 'flow_handle' || r.h === 'lowflow_handle', r.key);
  assert.ok(!api.ewrSupported({h: null}) && !api.ewrSupported({h: 'unmapped'}));
});
test('every supported EWR: its met years meet the rule, its missed years do not, and nothing is NaN', () => {
  const modes = {};
  for (const r of rows.filter(api.ewrSupported)) {
    const spec = api.ewrSpec(r); modes[spec.mode] = (modes[spec.mode] || 0) + 1;
    const t = api.buildTutorial(spec);
    if (!t.drawable) continue;
    for (const y of t.record) {
      assert.equal(y.met, y.type === 'met', `${r.key}: year type ${y.type} evaluated as met=${y.met}`);
      assert.ok(y.flow.every(Number.isFinite), `${r.key}: non-finite flow`);
    }
    assert.ok(t.metEx && t.missEx, r.key);
    assert.equal(t.n, 6); assert.equal(t.pct, 60); assert.equal(t.gap, 3);
  }
  assert.ok(modes.flow > 1500, JSON.stringify(modes));
});
test('EWRs (every 4th row) render all their tutorial steps at several points without NaN / undefined / null', () => {
  for (const r of rows.filter(api.ewrSupported).filter((_, i) => i % 4 === 0)) {
    const m = {...api.buildTutorial(api.ewrSpec(r)), row: r};
    const steps = guide.stages(m);
    assert.ok(steps.length >= 4, r.key);
    for (const s of steps) {
      assert.ok(!/NaN|undefined|null/.test(s.cap), `${r.key} ${s.title} caption: ${s.cap}`);
      for (const p of [0, 0.4, 1]) assert.ok(!/NaN|undefined|null|Infinity/.test(s.draw(p)), `${r.key} ${s.title} p=${p}`);
    }
  }
});
test('the template wires the guide in: slot, panel, three collapse handles', () => {
  const template = fs.readFileSync('src/dashboard_template.html','utf8');
  for (const m of ['__EWRIDX__', '__EWR_GUIDE_JS__', 'id="lpanel"', 'id="barHide"', 'id="gripL"', 'id="gripR"', 'id="tabT"', 'id="guideBtn"'])
    assert.equal(template.split(m).length, 2, `${m} must occur once in the template`);
});

test('user EWR guide rows are searchable by name and salinity keeps EC units in rule text',()=>{
 const custom={...rows[0],key:'404216|U_SAL|PU',g:'404216',c:'U_SAL',pu:'PU',name:'Morgan salinity cap',k:'S',h:'lowflow_handle',u:true,tmin:0,tmax:800,dur:30,ms:1};
 assert.equal(api.searchEwrs([custom],{text:'morgan salinity',kind:'S'}).length,1);
 const sp=api.ewrSpec(custom);assert.equal(sp.unit,'EC');assert.match(guide.ruleText(sp),/salinity.*EC/);
});


test('salinity tutorial captions and chart axes describe EC rather than low flows',()=>{
 const row={...rows[0],key:'404216|U_EC|PU',c:'U_EC',g:'404216',pu:'PU',k:'S',h:'flow_handle',tmin:0,tmax:800,dur:5,ms:5,sm:7,em:6,eo:''};
 const model={...api.buildTutorial(api.ewrSpec(row)),row},steps=guide.stages(model);
 assert.match(steps[0].cap,/salinity requirement/);assert.match(steps[1].title,/salinity threshold/);assert.match(steps[1].draw(1),/Salinity \(EC\)/);
 assert.doesNotMatch(steps[1].cap,/river nearly stops flowing/);
});


test('zero MRIP means no maximum gap in the tutorial, and broad limits are not labelled cease-to-flow',()=>{
 const row={...rows[0],c:'U_WIDE',k:'F',h:'flow_handle',tmin:0,tmax:1000000,mi:0,dur:5,ms:5,sm:7,em:6},spec=api.ewrSpec(row);
 assert.equal(spec.mi,null);const steps=guide.stages({...api.buildTutorial(spec),row});
 assert.doesNotMatch(steps[0].cap,/at most/);assert.doesNotMatch(steps[1].cap,/river nearly stops flowing/);
});


test('narrow high thresholds still produce valid successful illustrative events',()=>{
 for(const kind of ['F','S']){
  const row={...rows[0],c:'U_TIGHT',k:kind,h:'flow_handle',tmin:1000000,tmax:1000001,dur:5,ms:5,sm:7,em:6};
  const model={...api.buildTutorial(api.ewrSpec(row)),row};assert.ok(model.metEx);assert.ok(model.missEx);
  for(const step of guide.stages(model))assert.doesNotMatch(step.draw(1),/NaN|Infinity/);
 }
});
