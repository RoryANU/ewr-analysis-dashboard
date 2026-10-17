// Joint views use an explicit ecological target, independent of the exploratory header threshold.
const JOINT_TARGET = 1, JOINT_EPS = 1e-9;
const ruleFR = (row, scenario) => scenario == null ? null : row[6][scenario];
// ETP is a time proportion: values above 1 (in source data or older saved uploads) mean exceeded for the whole record.
const ruleETP = (row, scenario) => {
  const et = scenario == null ? null : row[7][scenario];
  return Number.isFinite(et) && et > 1 ? 1 : et;
};
const ruleCode = row => row[3];
const rulePU = row => META.pus[row[0]];
const ruleGauge = row => META.gauges[row[2]];
const gaugeNames = new Map(GAUGES.map(g => [g.id, g.name]));
const ruleLabel = row => `${ruleGauge(row)} (${gaugeNames.get(ruleGauge(row)) || 'station name unavailable'}) · ${rulePU(row)} · ${ruleCode(row)}`;
const meetsTarget = fr => fr >= JOINT_TARGET - JOINT_EPS;
const jointNames = ['Meeting both', 'Frequency only', 'MRIP only', 'Failing both'];
const jointColours = ['#207a65', '#c38315', '#527db3', '#bb4052'];
function jointClass(fr, et, threshold = S.ethr) {
  if (!Number.isFinite(fr) || !Number.isFinite(et) || fr < 0 || et < 0 || et > 1) return null;
  return (meetsTarget(fr) ? 0 : 2) + (et <= threshold + JOINT_EPS ? 0 : 1);
}
function jointSummary(rows, index) {
  const counts = [0, 0, 0, 0]; let severe = 0;
  for (const row of rows) {
    const k = jointClass(ruleFR(row, index), ruleETP(row, index));
    if (k !== null) { counts[k]++; if (ruleFR(row, index) < S.fbthr) severe++; }
  }
  const n = counts.reduce((a, b) => a + b, 0);
  return {counts, n, severe, score: n ? counts[0] / n : null};
}
function percentile(sorted, p) {
  if (!sorted.length) return null;
  const at = (sorted.length - 1) * p, lo = Math.floor(at), hi = Math.ceil(at);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (at - lo);
}
function climateSummary(rows, indices) {
  const resilient = [], crossings = [], incomplete = [], assessed = [];
  for (const row of rows) {
    const classes = indices.map(i => jointClass(ruleFR(row, i), ruleETP(row, i)));
    const n = classes.filter(k => k !== null).length;
    assessed.push(n);
    if (!indices.length || n !== indices.length) incomplete.push(row);
    else {
      if (classes.every(k => k === 0)) resilient.push(row);
      if (classes.includes(0) && classes.some(k => k !== 0)) crossings.push(row);
    }
  }
  return {resilient, crossings, incomplete, assessed, expected: indices.length};
}
const ROLLING_YEARS = 10;
function sequenceStats(years, window = ROLLING_YEARS) {
  let last = null, prev = null, run = []; const since = [], rolling = [];
  years.wy.forEach((w, i) => {
    if (prev !== null && w !== prev + 1) { last = null; run = []; }
    prev = w;
    // A missing year could contain an unobserved success. Do not report an exact interval across it.
    if (!years.complete[i]) { last = null; run = []; since.push(null); rolling.push(null); return; }
    const a = years.achieved[i] ? 1 : 0;
    if (a) last = w;
    since.push(last === null ? null : w - last);
    run.push(a); if (run.length > window) run.shift();
    rolling.push(run.length === window ? run.reduce((x, y) => x + y, 0) / window : null);
  });
  return {since, rolling};
}

// One retained view per analytical selection. Sorting, expanding existing matrices and changing
// scatter scales preserve this node, its charts, in-flight work and lazy caches.
let performanceView = null, performanceRevision = 0, lockJointScale = true;
function performanceKey() {
  return JSON.stringify([S.src, S.sel, S.pol, S.cli, S.mode, isDiff() ? [S.bpol, S.bcli] : null, S.ethr, S.fbthr, performanceRevision, FLOWGEN]);
}
function disposePerformance() {
  if (!performanceView) return;
  performanceView.sequence?.abort(); performanceView.conditions?.abort();
  if (typeof Plotly !== 'undefined') performanceView.node?.querySelectorAll('.js-plotly-plot').forEach(el => Plotly.purge(el));
  performanceView = null;
}
function preparePerformance(rows) {
  const key = performanceKey();
  if (performanceView?.key === key) return performanceView;
  disposePerformance();
  performanceView = {key, rows, node: null, i: scnIdx(), base: isDiff() ? scnIdx(S.bpol, S.bcli) : null,
    ctx: flowCtx(), gauge: S.sel.type === 'gauge' ? META.gauges[S.sel.i] : null,
    summaries: new Map(), details: new Map(), labels: new Map()};
  return performanceView;
}
const currentPerformance = view => view === performanceView;
function summaryFor(view, index) {
  if (!view.summaries.has(index)) view.summaries.set(index, jointSummary(view.rows, index));
  return view.summaries.get(index);
}
function detailText(view, index) {
  if (!view.details.has(index)) view.details.set(index, view.rows.map(row => {
    if (!view.labels.has(row)) view.labels.set(row, ruleLabel(row));
    return `${view.labels.get(row)}: FR ${ruleFR(row, index) ?? 'missing'}, ETP ${ruleETP(row, index) ?? 'missing'}` +
      (view.base == null ? '' : `; base FR ${ruleFR(row, view.base) ?? 'missing'}, ETP ${ruleETP(row, view.base) ?? 'missing'}`);
  }).join('\n'));
  return view.details.get(index);
}
function performancePanels(view) {
  const q = summaryFor(view, view.i), vals = view.rows.map(r => ruleFR(r, view.i)).filter(v => Number.isFinite(v) && v >= 0).sort((a,b) => a-b);
  const mean = vals.length ? vals.reduce((a,b) => a+b, 0) / vals.length : null;
  const fmt = v => v == null ? 'Unavailable' : v.toFixed(3), raw = vals.length ? vals.filter(meetsTarget).length / vals.length : null;
  const tolerance = vals.filter(v => v >= .9 && !meetsTarget(v)).length;
  return `<section id="performancePanel" class="performance">
    <h2>Joint frequency & inter-event performance · target FR 1.0</h2>
    <p class="note">Joint counts and plots always use FR ≥ 1.0, independent of the header frequency threshold. Within Tolerance: 0.9 ≤ FR &lt; 1.0 (${tolerance} EWRs); tolerance does not establish ecological sufficiency. MRIP compliance: exceedance time proportion ≤ ${S.ethr.toFixed(3)}.</p>
    <div class="tiles">${q.counts.map((n,k) => `<div class="tile"><b style="color:${jointColours[k]}">${n}</b><span>${jointNames[k]} · FR target 1.0</span></div>`).join('')}</div>
    <p>${q.n}/${view.rows.length} valid paired EWRs · ${view.rows.length-q.n} missing or invalid pairs (FR and ETP must be nonnegative; ETP above 1 is treated as 1).</p>
    <label><input id="lockJointScale" type="checkbox" ${lockJointScale ? 'checked' : ''}> Lock joint axes (FR 0–2; ETP 0–1)</label><div id="jointPlot"></div>
    <p class="note">MRIP exceedance is a time proportion; ×10 is equivalent years per ten years, not a calendar-year count. Daily-record event-gap diagnostics retain longest gaps.</p>
    <p><b>Unweighted EWR statistics:</b> mean FR ${fmt(mean)} · interpolated 10th percentile FR ${fmt(percentile(vals,.1))} · minimum FR ${fmt(vals[0] ?? null)} · raw meeting target 1.0 ${raw == null ? 'Unavailable' : (100*raw).toFixed(1)+'%'} (${vals.length} observed).</p>
    <p class="note">Existing hierarchy summaries weight gauges and planning units equally; their percentages are not raw percentages of EWRs.</p>
    <details id="jointDetails"><summary>Exact rule values${view.base == null ? '' : ' and baseline comparisons'}</summary><pre style="white-space:pre-wrap"></pre></details>
    <details id="robustnessDetails"><summary>Policy × climate robustness · target FR 1.0</summary>
      <p class="note">Joint score uses FR target 1.0 and the MRIP threshold above. Severe failure uses FR &lt; ${S.fbthr}. Coverage = valid paired / selected EWRs. Colours are fixed at 0–100%. Resilience and threshold crossings require all expected climates; incomplete rules are shown separately. Hover or focus a cell for exact rule values; activate it to read the full details.</p>
      <div id="robustnessTable"></div><pre id="robustnessValues" style="white-space:pre-wrap"></pre></details>
    <h2>Hydrological opportunities for recruitment</h2>
    <p class="note">Annual achievement, time since success and ${ROLLING_YEARS}-year rolling frequency are hydrological opportunities, not modelled recruitment outcomes.</p>
    <button id="loadSequence" ${view.ctx?.ok ? '' : 'disabled'}>Load annual raster</button> <button id="cancelSequence" hidden>Cancel annual loading</button>
    <div id="sequenceNote">${view.ctx?.ok ? 'Load supported rules on demand.' : 'Select an uploaded flow scenario and a gauge. Bundled summaries do not contain annual records.'}</div>
    <div id="sequenceRaster"></div><div id="sequenceSince"></div><div id="sequenceRolling"></div>
    <details id="inventoryDetails"><summary>Detailed diagnostic rule inventory</summary><button id="retryInventory">Load / retry inventory</button><div id="diagnosticInventory">Open to load supported and unsupported rules for the selected gauge.</div></details>
    <details><summary>Stage rates, temperature & water-quality observations</summary>
      <p class="note">CSV up to 2 MiB / 20,000 rows. Headers: date (YYYY-MM-DD), stage_m, temperature_c, dissolved_oxygen_mg_l. Quoted fields are supported; measurements must be plain decimals, or empty for missing. Stage rates use adjacent observed days only. No ecological safety thresholds are inferred.</p>
      <input id="conditionFile" type="file" accept=".csv"><div id="conditionNote"></div><div id="conditionPlot"></div></details>
    <footer id="performanceCoverage" class="note">Provenance: paired FR and ETP summary inputs, or selected uploaded results. Summary inputs have no record dates, missing-day counts or complete-year ratios. Annual diagnostic metadata appears here when loaded.</footer></section>`;
}
function mountPerformance(view) {
  if (view.node) { $('panel').appendChild(view.node); return; }
  $('panel').insertAdjacentHTML('beforeend', performancePanels(view)); view.node = $('performancePanel');
  drawJointPlot(view);
  $('lockJointScale').onchange = e => { lockJointScale = e.target.checked; drawJointPlot(view); };
  $('jointDetails').ontoggle = e => { if (e.target.open) e.target.querySelector('pre').textContent = detailText(view, view.i); };
  $('robustnessDetails').ontoggle = e => {
    if (!e.target.open || view.matrixBuilt) return;
    $('robustnessTable').innerHTML = robustnessTable(view); view.matrixBuilt = true;
    $('robustnessTable').querySelectorAll('[data-scenario]').forEach(cell => {
      const show = () => { cell.title = detailText(view, +cell.dataset.scenario); };
      cell.onmouseenter = show; cell.onfocus = show;
      cell.onclick = () => { show(); $('robustnessValues').textContent = cell.title; };
      cell.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); cell.click(); } };
    });
  };
  $('inventoryDetails').ontoggle = e => { if (e.target.open) loadInventoryView(view); };
  $('retryInventory').onclick = () => loadInventoryView(view);
  $('loadSequence').onclick = () => loadSequences(view);
  $('cancelSequence').onclick = () => view.sequence?.abort();
  $('conditionFile').onchange = e => loadConditions(e.target.files[0], view);
}
function robustnessTable(view) {
  const cell = index => {
    const q = summaryFor(view, index), base = view.base == null ? null : summaryFor(view, view.base);
    return `<td tabindex="0" role="button" data-scenario="${index}" aria-label="Inspect exact rule values, target FR 1.0" style="background:${q.score == null ? '#eee' : `hsl(${q.score*130},40%,85%)`}"><b>${q.score == null ? 'Unavailable' : (100*q.score).toFixed(1)+'%'}</b><br>${q.severe} severe<br>${q.n}/${view.rows.length} coverage${base ? '<br>Base: '+(base.score == null ? '—' : (100*base.score).toFixed(1)+'%') : ''}</td>`;
  };
  if (S.src === 'user') return `<p class="note">Uploaded records have no policy × climate metadata. Climate robustness is not inferred from names.</p><table><tr><th>Uploaded record (climate unspecified)</th><th>Joint score · target FR 1.0</th></tr>${CUSTOM.filter(c => !c.removed).map(c => `<tr><th>${esc(c.name)}</th>${cell(scnIdx(c.P,0))}</tr>`).join('')}</table>`;
  // Custom records are registered explicitly, not identified by a fixed number of built-in policies.
  const customPolicies = new Set(CUSTOM.map(c => c.P));
  const policies = META.policies.map((name,p) => ({name,p})).filter(({p}) => !customPolicies.has(p));
  return `<div style="overflow:auto"><table><thead><tr><th>Policy</th>${META.climates.map(c => `<th>${esc(c)}</th>`).join('')}<th>Across all expected climates</th></tr></thead><tbody>${policies.map(({name,p}) => {
    const indices = META.climates.map((_,c) => scnIdx(p,c)), q = climateSummary(view.rows,indices);
    const range = q.assessed.reduce((a,n) => [Math.min(a[0],n),Math.max(a[1],n)], [indices.length,0]);
    return `<tr><th>${esc(name)}</th>${indices.map(cell).join('')}<td>${q.resilient.length} resilient<br>${q.crossings.length} threshold crossings<br>${q.incomplete.length} incomplete<br>${view.rows.length ? range.join('–') : 0}/${q.expected} climates assessed per EWR</td></tr>`;
  }).join('')}</tbody></table></div>`;
}

// FR may exceed the locked range; ETP arrives capped at 1 and negative ETP is invalid even in unlocked mode.
// Omit and count both out-of-range endpoints, and never draw a partial comparison arrow.
function jointGeometry(rows, index, base, locked) {
  let xmax = locked ? 2 : 1.2;
  if (!locked) for (const row of rows) for (const i of base == null ? [index] : [index,base]) {
    const fr = ruleFR(row,i); if (Number.isFinite(fr)) xmax = Math.max(xmax,fr);
  }
  const inside = (fr,et) => Number.isFinite(fr) && Number.isFinite(et) && fr >= 0 && fr <= xmax && et >= 0 && et <= 1;
  const points = [], arrows = []; let omittedCurrent = 0, omittedBase = 0;
  for (const row of rows) {
    const fr = ruleFR(row,index), et = ruleETP(row,index), valid = inside(fr,et);
    if (valid) points.push({row,fr,et}); else if (Number.isFinite(fr) && Number.isFinite(et)) omittedCurrent++;
    if (base != null) {
      const bf = ruleFR(row,base), be = ruleETP(row,base), bv = inside(bf,be);
      if (!bv && Number.isFinite(bf) && Number.isFinite(be)) omittedBase++;
      if (valid && bv) arrows.push({fr,et,bf,be});
    }
  }
  return {xmax,points,arrows,omittedCurrent,omittedBase};
}
function drawJointPlot(view) {
  const g = jointGeometry(view.rows,view.i,view.base,lockJointScale), x = v => 55+v/g.xmax*540, y = v => 270-v*240;
  let svg = `<svg role="img" aria-label="Joint target FR 1.0 versus MRIP exceedance; exact values available in disclosure" viewBox="0 0 650 320" style="width:100%;max-height:420px"><defs><clipPath id="jointClip"><rect x="55" y="30" width="540" height="240"/></clipPath><marker id="jointArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10z" fill="#64748b"/></marker></defs><path d="M55 30V270H595" fill="none" stroke="#777"/><text x="270" y="310">Frequency ratio (target 1.0)</text><text transform="translate(15 245) rotate(-90)">MRIP exceedance time proportion</text>`;
  for (const v of [0,.25,.5,.75,1]) svg += `<text x="22" y="${y(v)+4}" font-size="10">${v}</text>`;
  for (const v of [0,1,g.xmax]) svg += `<text x="${x(v)}" y="290" font-size="10">${v.toFixed(2)}</text>`;
  svg += `<g clip-path="url(#jointClip)"><path d="M${x(1)} 30V270 M55 ${y(S.ethr)}H595" stroke="#888" stroke-dasharray="4 4"/>`;
  for (const a of g.arrows) svg += `<path d="M${x(a.bf)} ${y(a.be)}L${x(a.fr)} ${y(a.et)}" stroke="#64748b" opacity=".5" marker-end="url(#jointArrow)"/><circle cx="${x(a.bf)}" cy="${y(a.be)}" r="3" fill="white" stroke="#64748b"/>`;
  for (const {row,fr,et} of g.points) svg += `<circle cx="${x(fr)}" cy="${y(et)}" r="4" fill="${jointColours[jointClass(fr,et)]}" opacity=".8"><title>${esc(ruleLabel(row))}\n${jointNames[jointClass(fr,et)]} · target FR 1.0 · FR ${fr} · ETP ${et}${view.base == null ? '' : '\nBase FR '+(ruleFR(row,view.base) ?? 'missing')+' · ETP '+(ruleETP(row,view.base) ?? 'missing')}</title></circle>`;
  $('jointPlot').innerHTML = svg+'</g></svg>'+`<p class="note">Outside plot bounds: ${g.omittedCurrent} current endpoints${view.base == null ? '' : ', '+g.omittedBase+' baseline endpoints'}. Arrows require both endpoints inside. Missing metrics are not plotted. Unlock to expand FR; ETP remains 0–1. Exact values are available above. ${view.base == null ? '' : 'Hollow points = base; arrows point to comparison.'}</p>`;
}

async function performanceJSON(url, signal) {
  const response = await fetch(url, {signal});
  const data = await response.json();
  if (!response.ok || data.error) throw Error(data.error || `Request failed (${response.status})`);
  return data;
}
// Share pending and resolved inventory requests with the existing event selector. Evict failures for retry.
const inventoryRequests = new Map();
function inventoryFor(gauge) {
  if (!inventoryRequests.has(gauge)) {
    const pending = performanceJSON('/api/ewrs?gauge='+encodeURIComponent(gauge)).catch(error => {
      inventoryRequests.delete(gauge); throw error;
    });
    inventoryRequests.set(gauge,pending);
  }
  return inventoryRequests.get(gauge);
}
async function loadInventoryView(view) {
  if (!view.gauge) { $('diagnosticInventory').textContent = 'Select a gauge to inspect its inventory.'; return; }
  try {
    const inv = await inventoryFor(view.gauge); if (!currentPerformance(view)) return;
    $('diagnosticInventory').innerHTML = '<table><tr><th>Rule / planning unit</th><th>Type / handler</th><th>Annual diagnostics</th></tr>'+inv.inventory.map(r => `<tr><td>${esc(r.code+' · '+r.pu)}</td><td>${esc(r.kind+' / '+r.handler)}</td><td>${r.detailed ? 'Available' : 'Not supported'}</td></tr>`).join('')+'</table>';
  } catch (error) { if (currentPerformance(view)) $('diagnosticInventory').textContent = 'Inventory unavailable: '+error.message; }
}
function abortable(promise, signal) {
  return new Promise((resolve,reject) => {
    const abort = () => reject(new DOMException('Cancelled','AbortError'));
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort',abort,{once:true});
    promise.then(resolve,reject).finally(() => signal.removeEventListener('abort',abort));
  });
}
async function boundedMap(items, limit, signal, work) {
  const results = new Array(items.length); let next = 0;
  async function worker() {
    while (!signal.aborted) {
      const i = next++; if (i >= items.length) return;
      try { results[i] = {value: await work(items[i],i)}; }
      catch (error) { results[i] = {error}; }
    }
  }
  await Promise.all(Array.from({length:Math.min(limit,items.length)},worker));
  return results;
}
async function loadSequences(view) {
  if (!currentPerformance(view) || !view.ctx?.ok) return;
  view.sequence?.abort();
  const controller = new AbortController(); view.sequence = controller;
  const button = $('loadSequence'), cancel = $('cancelSequence'), note = $('sequenceNote');
  const active = () => currentPerformance(view) && view.sequence === controller && !controller.signal.aborted;
  button.disabled = true; cancel.hidden = false; note.textContent = 'Loading supported rule inventory…';
  try {
    if (typeof Plotly === 'undefined') throw Error('Plotly is unavailable; annual charts require the chart library.');
    const inv = await abortable(inventoryFor(view.gauge),controller.signal); if (!active()) return;
    const list = inv.ewrs.filter(e => view.rows.some(r => ruleCode(r) === e.code && e.pus.includes(rulePU(r))));
    if (!list.length) { note.textContent = 'No supported annual rules in this selection.'; return; }
    const c = view.ctx; let completed = 0;
    const results = await boundedMap(list,4,controller.signal,async e => {
      const q = new URLSearchParams({base:c.base,gauge:c.gauge,ewr:e.code,pu:e.pu}); if (c.comp) q.set('comp',c.comp);
      try { return {e,data:await performanceJSON('/api/ewryears?'+q,controller.signal)}; }
      finally { completed++; if (active()) note.textContent = `Evaluating annual rules: ${completed}/${list.length}…`; }
    });
    if (!active()) return;
    const series = [], coverage = [], errors = [];
    results.forEach((result,i) => {
      if (result.error) { errors.push(list[i].code+': '+result.error.message); return; }
      const {e,data:d} = result.value;
      const r = view.rows.find(r => ruleCode(r) === e.code && e.pus.includes(rulePU(r)));
      const group = r ? [...new Set(r[4].map(o => META.themes[META.objTheme[o]]))].sort().join(' / ') : 'Unclassified';
      for (const [key,s] of Object.entries(d.scenarios)) {
        const name = `${group} · ${e.code} · ${e.pu} · ${key === 'base' ? c.bn : c.cn} · ${ayName(d.ystart)}`;
        series.push({name,years:s.years,stats:sequenceStats(s.years)});
        const v = s.coverage;
        coverage.push(`${name}: ${v.start ?? 'unknown'} – ${v.end ?? 'unknown'}; ${v.missing_days} missing days; ${v.complete_years}/${v.years} complete assessment years; MRIP ${d.mrip_days ?? 'unavailable'} days`);
      }
    });
    series.sort((a,b) => a.name.localeCompare(b.name));
    if (!series.length) throw Error('No annual results. '+errors.join('; '));
    await plotSequences(series,active);
    if (!active()) return;
    note.textContent = `${series.length} rule/scenario rows. Grey = failed, green = achieved, blank = incomplete/unavailable. Assessment windows differ; aligned start years do not imply simultaneous opportunities. Time since success is unknown before the first observed success or across missing years; rolling values require ${ROLLING_YEARS} consecutive complete years. ${errors.length ? 'Failures: '+errors.join('; ') : ''}`;
    $('performanceCoverage').textContent = 'Provenance: py_ewr evaluation of uploaded daily flow records. '+coverage.join(' | ');
  } catch (error) { if (active()) note.textContent = 'Could not load annual diagnostics: '+error.message; }
  finally {
    // Captured elements cannot mutate replacement controls. A superseded request must not enable a newer one.
    if (view.sequence === controller) {
      button.disabled = false; cancel.hidden = true;
      if (currentPerformance(view) && controller.signal.aborted) note.textContent = 'Annual loading cancelled. You can retry.';
    }
  }
}
async function plotSequences(series, active) {
  const years = [...new Set(series.flatMap(s => s.years.wy))].sort((a,b) => a-b);
  const aligned = (s,v) => { const m = new Map(s.years.wy.map((w,i) => [w,v[i]])); return years.map(w => m.get(w) ?? null); };
  // Capture DOM elements rather than string IDs: a late Plotly promise cannot target a replacement view.
  const raster = $('sequenceRaster'), since = $('sequenceSince'), rolling = $('sequenceRolling');
  await Plotly.newPlot(raster,[{type:'heatmap',x:years,y:series.map(s => s.name),z:series.map(s => aligned(s,s.years.achieved.map((a,i) => s.years.complete[i] ? (a ? 1 : 0) : null))),zmin:0,zmax:1,colorscale:[[0,'#eee'],[1,'#207a65']],hoverongaps:false,xgap:1,ygap:2,colorbar:{tickvals:[0,1],ticktext:['Failed','Achieved']}}],{height:Math.max(250,series.length*30),margin:{l:230,r:90,t:20,b:40},xaxis:{title:'Assessment start year (windows shown in row labels)',dtick:1}},PCFG);
  if (!active()) return;
  for (const [el,key,title] of [[since,'since','Years since last successful assessment year'],[rolling,'rolling',`${ROLLING_YEARS}-year rolling achievement proportion`]]) {
    await Plotly.newPlot(el,series.map(s => ({type:'scatter',mode:'lines+markers',name:s.name,x:years,y:aligned(s,s.stats[key]),connectgaps:false})),{height:280,margin:{l:70,r:20,t:20,b:45},showlegend:false,xaxis:{title:'Assessment start year'},yaxis:{title,...(key === 'rolling' ? {range:[0,1]} : {})}},PCFG);
    if (!active()) return;
  }
}

const CONDITION_MAX_BYTES = 2*1024*1024, CONDITION_MAX_ROWS = 20000;
// Small RFC-style CSV state machine: quoted commas/newlines and doubled quotes are parsed;
// structural errors are rejected rather than silently shifting measurements between columns.
function conditionCSV(text) {
  if (text.length > CONDITION_MAX_BYTES || new TextEncoder().encode(text).length > CONDITION_MAX_BYTES) throw Error('CSV exceeds 2 MiB.');
  text = text.replace(/^\uFEFF/,'');
  const rows = []; let row = [], field = '', quoted = false, closed = false;
  const endField = () => { row.push(field); field = ''; closed = false; };
  const endRow = () => { endField(); if (row.some(s => s !== '')) rows.push(row); row = []; if (rows.length > CONDITION_MAX_ROWS+1) throw Error('CSV exceeds 20,000 observation rows.'); };
  for (let i=0;i<text.length;i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') { if (text[i+1] === '"') { field += '"'; i++; } else { quoted = false; closed = true; } }
      else field += c;
    } else if (c === ',' ) endField();
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i+1] === '\n') i++; endRow(); }
    else if (c === '"' && field === '' && !closed) quoted = true;
    else { if (closed || c === '"') throw Error('Malformed quoted CSV field.'); field += c; }
  }
  if (quoted) throw Error('Unterminated quoted CSV field.');
  if (field !== '' || row.length || closed) endRow();
  return rows;
}
function parseConditions(text) {
  const rows = conditionCSV(text); if (!rows.length) throw Error('CSV is empty.');
  const headers = rows.shift(), allowed = ['date','stage_m','temperature_c','dissolved_oxygen_mg_l'];
  if (headers[0] !== 'date' || headers.length < 2 || headers.some(h => !allowed.includes(h)) || new Set(headers).size !== headers.length) throw Error('Use date and at least one of stage_m,temperature_c,dissolved_oxygen_mg_l as headers.');
  const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;
  const data = rows.map(a => {
    if (a.length !== headers.length) throw Error('CSV column count does not match its header.');
    const date = a[0], time = Date.parse(date+'T00:00:00Z');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(time) || new Date(time).toISOString().slice(0,10) !== date) throw Error('Invalid date: '+date);
    const r = {date,time};
    headers.slice(1).forEach((h,i) => {
      const value = a[i+1];
      if (value !== '' && (!decimal.test(value) || !Number.isFinite(Number(value)))) throw Error('Use plain decimals or empty measurements on '+date);
      r[h] = value === '' ? null : Number(value);
    });
    return r;
  }).sort((a,b) => a.time-b.time);
  if (!data.length) throw Error('No observation rows.');
  if (new Set(data.map(r => r.date)).size !== data.length) throw Error('Duplicate dates are not allowed.');
  data.forEach((r,i) => { const p = data[i-1]; r.rate = p && r.time-p.time === 86400000 && Number.isFinite(r.stage_m) && Number.isFinite(p.stage_m) ? r.stage_m-p.stage_m : null; });
  return data;
}
function breakConditionGaps(data) {
  // One separator per gap, never one row per missing day. Output is at most 2n-1.
  const out = [];
  data.forEach((r,i) => { if (i && r.time-data[i-1].time > 86400000) out.push({date:new Date(data[i-1].time+86400000).toISOString().slice(0,10)}); out.push(r); });
  return out;
}
async function loadConditions(file, view) {
  if (!file || !currentPerformance(view)) return;
  view.conditions?.abort(); const controller = new AbortController(); view.conditions = controller;
  const active = () => currentPerformance(view) && view.conditions === controller && !controller.signal.aborted;
  const note = $('conditionNote'), plot = $('conditionPlot');
  try {
    if (!view.gauge) throw Error('Select a gauge before loading observations.');
    if (file.size > CONDITION_MAX_BYTES) throw Error('CSV exceeds 2 MiB.');
    note.textContent = 'Reading observations…';
    const text = await file.text(); if (!active()) return;
    const data = parseConditions(text), expanded = breakConditionGaps(data);
    const rates = data.reduce((s,r) => { if (Number.isFinite(r.rate)) { s.n++; s.rise = Math.max(s.rise,r.rate); s.fall = Math.max(s.fall,-r.rate); } return s; }, {n:0,rise:0,fall:0});
    if (typeof Plotly === 'undefined') throw Error('Plotly is unavailable; observation charts require the chart library.');
    const columns = [['stage_m','Stage (m)'],['rate','Stage change (m/day)'],['temperature_c','Temperature (°C)'],['dissolved_oxygen_mg_l','Dissolved oxygen (mg/L)']];
    const traces = columns.map(([k,name],i) => ({type:'scatter',mode:'lines',name,x:expanded.map(r => r.date),y:expanded.map(r => r[k] ?? null),yaxis:i ? 'y'+(i+1) : 'y',connectgaps:false}));
    const layout = {height:640,margin:{l:80,r:20,t:25,b:45},showlegend:false,xaxis:{type:'date',title:'Date'}};
    columns.forEach(([k,name],i) => { layout[i ? 'yaxis'+(i+1) : 'yaxis'] = {title:name,domain:[1-(i+1)*.25+.04,1-i*.25-.02]}; });
    await Plotly.newPlot(plot,traces,layout,PCFG); if (!active()) return;
    note.textContent = `${file.name} · gauge ${view.gauge} · ${data[0].date} – ${data.at(-1).date}. ${rates.n} adjacent daily stage pairs. Maximum rise ${rates.n ? rates.rise.toFixed(3) : 'unavailable'} m/day; maximum recession ${rates.n ? rates.fall.toFixed(3) : 'unavailable'} m/day. User-supplied observations for this selection.`;
  } catch (error) { if (active()) { note.textContent = error.message; plot.innerHTML = ''; } }
}
