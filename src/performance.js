// Joint views use an explicit ecological target, independent of the exploratory header threshold.
const JOINT_TARGET = 1, JOINT_EPS = 1e-9;
const ruleCurrent = (row, scenario) => !row.user || row[8]?.uewrVerAt?.[scenario] === row.userVer;
const ruleFR = (row, scenario) => scenario == null || !ruleCurrent(row, scenario) ? null : row[6][scenario];
// ETP is a time proportion: values above 1 (in source data or older saved uploads) mean exceeded for the whole record.
const ruleETP = (row, scenario) => {
  const et = scenario == null || !ruleCurrent(row, scenario) ? null : row[7][scenario];
  return Number.isFinite(et) && et > 1 ? 1 : et;
};
const ruleCode = row => row[3];
const rulePU = row => META.pus[row[0]];
const ruleGauge = row => META.gauges[row[2]];
const gaugeNames = new Map(GAUGES.map(g => [g.id, g.name]));
/** Return an unescaped rule label with gauge ID/name, planning unit, and code; normalize the name when niceGaugeName is available. */
const ruleLabel = row => `${ruleGauge(row)} (${typeof niceGaugeName === 'function' ? niceGaugeName(gaugeNames.get(ruleGauge(row))) : gaugeNames.get(ruleGauge(row)) || 'station name unavailable'}) · ${rulePU(row)} · ${ruleCode(row)}`;
// The joint plot's own fixed targets (dashed lines and green region).
const PLOT_FR = 0.95, PLOT_ET = 0.05;
const jointNames = ['Meeting both', 'Frequency only', 'MRIP only', 'Failing both'];  // by jointClass
/**
 * Classify frequency ratio fr and MRIP exceedance time proportion et (0–1).
 * Return 0 for both targets met, 1 for frequency only, 2 for MRIP only, or 3 for neither.
 * Return null for non-finite metrics, negative fr, or et outside [0, 1].
 * threshold is the maximum allowed et (default S.ethr); target is the minimum fr (default 1), both with 1e-9 tolerance.
 */
function jointClass(fr, et, threshold = S.ethr, target = JOINT_TARGET) {
  if (!Number.isFinite(fr) || !Number.isFinite(et) || fr < 0 || et < 0 || et > 1) return null;
  return (fr >= target - JOINT_EPS ? 0 : 2) + (et <= threshold + JOINT_EPS ? 0 : 1);
}
/** Count rules per joint class at one scenario: {counts, n (classified), severe (FR below S.fbthr), score (share meeting both)}. */
function jointSummary(rows, index) {
  const counts = [0, 0, 0, 0]; let severe = 0;
  for (const row of rows) {
    const cls = jointClass(ruleFR(row, index), ruleETP(row, index));
    if (cls !== null) { counts[cls]++; if (ruleFR(row, index) < S.fbthr) severe++; }
  }
  const n = counts.reduce((a, b) => a + b, 0);
  return {counts, n, severe, score: n ? counts[0] / n : null};
}
/** Linearly interpolated percentile (p in 0–1) of an ascending array; null when it is empty. */
function percentile(sorted, p) {
  if (!sorted.length) return null;
  const at = (sorted.length - 1) * p, lo = Math.floor(at), hi = Math.ceil(at);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (at - lo);
}
/**
 * Sort rules by how they fare across the scenarios at `indices` (one policy's climates): resilient = meeting both everywhere,
 * crossings = meeting both in some but not all, incomplete = missing a class anywhere. `assessed` counts classified scenarios per rule.
 */
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
/**
 * Per assessment year: years since the last achieved year (`since`) and the achieved share over the last `window`
 * consecutive complete years (`rolling`); null wherever a gap in the record makes the value unknown.
 */
function sequenceStats(years, window = ROLLING_YEARS) {
  let last = null, prev = null, run = []; const since = [], rolling = [];
  years.wy.forEach((w, i) => {
    if (prev !== null && w !== prev + 1) { last = null; run = []; }
    prev = w;
    // A missing year could contain an unobserved success. Do not report an exact interval across it.
    if (!years.complete[i]) { last = null; run = []; since.push(null); rolling.push(null); return; }
    const achieved = years.achieved[i] ? 1 : 0;
    if (achieved) last = w;
    since.push(last === null ? null : w - last);
    run.push(achieved); if (run.length > window) run.shift();
    rolling.push(run.length === window ? run.reduce((x, y) => x + y, 0) / window : null);
  });
  return {since, rolling};
}

// One retained view per analytical selection. Sorting, expanding existing matrices and changing
// scatter scales preserve this node, its charts, in-flight work and lazy caches.
let performanceView = null, performanceRevision = 0;
/** Everything that changes the analysis shown: a view is reused only while this key is unchanged. */
function performanceKey() {
  return JSON.stringify([S.src, S.sel, S.pol, S.cli, S.mode, isDiff() ? [S.bpol, S.bcli] : null, S.ethr, S.fbthr, performanceRevision, FLOWGEN]);
}
/** Cancel the current view's pending work and free its charts. */
function disposePerformance() {
  if (!performanceView) return;
  performanceView.sequence?.abort(); performanceView.conditions?.abort();
  if (typeof Plotly !== 'undefined') [performanceView.node, performanceView.jointNode].forEach(node => node?.querySelectorAll('.js-plotly-plot').forEach(el => Plotly.purge(el)));
  performanceView = null;
}
/** The view for the current selection (user EWR rows only in Your data mode), reused while performanceKey is unchanged. */
function preparePerformance(rows) {
  rows = rows.filter(row => !row.user || S.src === 'user');
  const key = performanceKey();
  if (performanceView?.key === key) return performanceView;
  disposePerformance();
  performanceView = {key, rows, node: null, i: scnIdx(), base: isDiff() ? scnIdx(S.bpol, S.bcli) : null,
    ctx: flowCtx(), gauge: S.sel.type === 'gauge' ? META.gauges[S.sel.i] : null,
    summaries: new Map(), details: new Map(), labels: new Map()};
  return performanceView;
}
const currentPerformance = view => view === performanceView;
/** jointSummary for a scenario index, cached on the view. */
function summaryFor(view, index) {
  if (!view.summaries.has(index)) view.summaries.set(index, jointSummary(view.rows, index));
  return view.summaries.get(index);
}
/** Plain-text FR / ETP values of every rule at a scenario (with the baseline in Differential mode), cached on the view. */
function detailText(view, index) {
  if (!view.details.has(index)) view.details.set(index, view.rows.map(row => {
    if (!view.labels.has(row)) view.labels.set(row, ruleLabel(row));
    return `${view.labels.get(row)}: FR ${ruleFR(row, index) ?? 'missing'}, ETP ${ruleETP(row, index) ?? 'missing'}` +
      (view.base == null ? '' : `; base FR ${ruleFR(row, view.base) ?? 'missing'}, ETP ${ruleETP(row, view.base) ?? 'missing'}`);
  }).join('\n'));
  return view.details.get(index);
}
/**
 * Return an HTML table for view.rows at the flattened policy/climate scenario index.
 * Show FR to three decimals and ETP as a percentage to one decimal; non-finite values appear as 'missing'.
 * Include baseline columns when view.base is set; finite ETP values above 1 are displayed as 100%.
 */
function detailTable(view, index) {
  const fmtFR = v => Number.isFinite(v) ? String(+v.toPrecision(6)) : 'missing', fmtET = v => Number.isFinite(v) ? +(100*v).toFixed(3)+'%' : 'missing', hasBase = view.base != null;
  return `<div style="overflow:auto;max-height:320px"><table><thead><tr><th>EWR</th><th>Gauge</th><th>Planning unit</th><th>FR</th><th>Time beyond maximum gap</th>${hasBase ? '<th>Base FR</th><th>Base time beyond maximum gap</th>' : ''}</tr></thead><tbody>${view.rows.map(row =>
    `<tr><td>${esc(ruleCode(row))}</td><td>${esc(ruleGauge(row))}</td><td>${esc(rulePU(row))}</td><td>${fmtFR(ruleFR(row, index))}</td><td>${fmtET(ruleETP(row, index))}</td>${hasBase ? `<td>${fmtFR(ruleFR(row, view.base))}</td><td>${fmtET(ruleETP(row, view.base))}</td>` : ''}</tr>`).join('')}</tbody></table></div>`;
}
/** Return panel markup for a performance view, with lazy rule details and robustness results. */
function performancePanels(view) {
  const q = summaryFor(view, view.i);
  return `<section id="performancePanel">
    <div id="jointContent">
    <p class="note">Each dot is an EWR. Further right = the target frequency is met more often; lower = less time spent beyond the maximum recommended interval between events (MRIP). The shaded bottom-right zone is where both are met.</p>
    <div id="jointPlot"></div><p id="jointNote" class="note"></p>
    <p class="note">${q.n} of ${view.rows.length} EWRs have both metrics.</p>
    </div>
    <details id="otherDetails" class="flowp"><summary>Extra data</summary>
    <details id="jointDetails"><summary>Exact rule values and baseline comparisons</summary><div class="jointTable"></div></details>
    <details id="robustnessDetails"><summary>Policy × climate robustness · target FR 1.0</summary>
      <p class="note">Joint score counts an EWR as meeting both when its frequency ratio is at least 1.0 and its time beyond the maximum gap is within the allowed limit. Severe failure uses FR &lt; ${S.fbthr}. Coverage = valid paired / selected EWRs. Colours are fixed at 0–100%. Resilience and threshold crossings require all expected climates; incomplete rules are shown separately. Cells with no data are greyed out. Hover or focus a cell for exact rule values; activate it to read the full details.</p>
      <div id="robustnessTable"></div><div id="robustnessValues"></div></details>
    ${view.gauge && view.ctx?.ok && S.src === 'user' ? `<details id="annualDetails"><summary>Annual EWR achievement and gaps</summary>
      <p class="note">Evaluate the selected rules against uploaded daily series. Stale user EWRs must be re-run first.</p>
      <button id="loadSequence">Load annual diagnostics</button> <button id="cancelSequence" class="ghost" hidden>Cancel</button>
      <p id="sequenceNote" role="status"></p><p id="performanceCoverage" class="note"></p>
      <div id="sequenceRaster"></div><div id="sequenceSeries"></div><div id="sequenceSince"></div><div id="sequenceRolling"></div>
      <details id="inventoryDetails"><summary>Rule inventory</summary><div id="diagnosticInventory"></div></details>
    </details>` : ''}
    </details></section>`;
}
/** Attach the retained view to the panel and bind detail handlers when first mounted. */
function mountPerformance(view) {
  if (view.node) { $('panel').appendChild(view.node); placeJoint(view); return; }
  $('panel').insertAdjacentHTML('beforeend', performancePanels(view)); view.node = $('performancePanel');
  view.jointNode = $('jointContent'); placeJoint(view);
  if ($('loadSequence')) {
    $('loadSequence').onclick = () => loadSequences(view);
    $('cancelSequence').onclick = () => view.sequence?.abort();
    $('inventoryDetails').ontoggle = e => { if (e.target.open) loadInventoryView(view); };
  }
  $('jointDetails').ontoggle = e => { if (e.target.open) e.target.querySelector('.jointTable').innerHTML = detailTable(view, view.i); };
  $('robustnessDetails').ontoggle = e => {
    if (!e.target.open || view.matrixBuilt) return;
    $('robustnessTable').innerHTML = robustnessTable(view); view.matrixBuilt = true;
    $('robustnessTable').querySelectorAll('[data-scenario]').forEach(cell => {
      const show = () => { cell.title = detailText(view, +cell.dataset.scenario); };
      cell.onmouseenter = show; cell.onfocus = show;
      cell.onclick = () => { show(); $('robustnessValues').innerHTML = detailTable(view, +cell.dataset.scenario); };
      cell.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); cell.click(); } };
    });
  };
}
/** Move the joint plot into its slot in the current panel layout, hiding it when the layout has no slot. */
function placeJoint(view) {
  const slot = $('jointSlot');
  (slot || view.node).appendChild(view.jointNode);
  view.jointNode.hidden = !slot;
}

/**
 * Return joint-score, severe-failure, and coverage table HTML for the view's rows, using FR 1 and S.ethr.
 * Built-in policies include outcomes across all expected climates; uploads are listed separately without inferred climates.
 * Scenarios with no valid FR/ETP pairs show 'No data'; baseline scores appear when view.base is set and data is available.
 */
function robustnessTable(view) {
  const cell = index => {
    const q = summaryFor(view, index), base = view.base == null ? null : summaryFor(view, view.base);
    if (q.n === 0) return `<td class="nodata" style="background:#f3f4f6;color:#9ca3af"><span>No data</span></td>`;
    return `<td tabindex="0" role="button" data-scenario="${index}" aria-label="Inspect exact rule values, target FR 1.0" style="background:${q.score == null ? '#eee' : `hsl(${q.score*130},40%,85%)`}"><b>${q.score == null ? 'No data' : (100*q.score).toFixed(1)+'%'}</b><br>${q.severe} severe<br>${q.n}/${view.rows.length} coverage${base ? '<br>Base: '+(base.n === 0 || base.score == null ? '—' : (100*base.score).toFixed(1)+'%') : ''}</td>`;
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
      if (valid && bv) arrows.push({row,fr,et,bf,be});
    }
  }
  return {xmax,points,arrows,omittedCurrent,omittedBase};
}
// Keep each rule as one point, including rules assigned to multiple regimes.
const jointRegime = row => [...new Set(row[5].map(i => META.regimes[i]).filter(Boolean))].sort().join(' / ') || 'Unmapped';
const jointRegimeColour = (index, count) => {
  // Ordered multi-hue ramp: gold → green → teal → blue → purple.
  const stops = [[196,154,0],[77,156,56],[0,150,143],[48,104,190],[112,48,150]];
  const at = (count < 2 ? .5 : index / (count - 1)) * (stops.length - 1);
  const lo = Math.min(Math.floor(at), stops.length - 2), t = at - lo;
  return `rgb(${stops[lo].map((v,i) => Math.round(v+(stops[lo+1][i]-v)*t)).join(',')})`;
};
/** Comparator for regime names: lowest flow threshold among their rules first, then the page's regime order, then by name. */
function jointRegimeOrder(rows) {
  const thresholds = new Map();
  for (const row of rows) {
    const rules = typeof EWRLIST === 'undefined' ? [] : EWRLIST.get(ruleGauge(row)) || [];
    for (const rule of rules) {
      if (rule.code !== ruleCode(row) || !(rule.pus || [rule.pu]).includes(rulePU(row)) || !Number.isFinite(rule.thr)) continue;
      const name = jointRegime(row);
      thresholds.set(name, Math.min(thresholds.get(name) ?? Infinity, rule.thr));
    }
  }
  const rank = name => typeof REG_ORDER === 'undefined' || !REG_ORDER.includes(name) ? Infinity : REG_ORDER.indexOf(name);
  return (a,b) => (thresholds.get(a) ?? Infinity) - (thresholds.get(b) ?? Infinity) || rank(a) - rank(b) || a.localeCompare(b);
}
/**
 * Draw FR versus MRIP exceedance for the view, with baseline arrows and an automatic FR range.
 * Use fixed plot targets of FR 0.95 and 5% exceedance for reference lines, shading, and hover classification.
 * Omit missing or invalid metric pairs; draw baseline arrows only when both endpoints are valid.
 * If Plotly is unavailable, direct readers to the tabulated values; synchronous chart errors propagate to the caller.
 */
function drawJointPlot(view) {
  const g = jointGeometry(view.rows,view.i,view.base,false);
  if (typeof Plotly === 'undefined') { $('jointPlot').textContent = 'Chart library unavailable. Exact values are in Extra data.'; return; }
  const regimes = [...new Set(view.rows.map(jointRegime))].sort(jointRegimeOrder(view.rows));
  const colours = new Map(regimes.map((name,i) => [name, name === 'Unmapped' ? '#6b7280' : jointRegimeColour(i,regimes.length)]));
  const traces = regimes.map(name => {
    const points = g.points.filter(p => jointRegime(p.row) === name);
    return {type:'scatter',mode:'markers',name,x:points.map(p=>p.fr),y:points.map(p=>p.et),
      legendgroup:name,marker:{size:9,color:colours.get(name),line:{color:'white',width:1}},
      customdata:points.map(({row,fr,et})=>[esc(ruleCode(row)),esc(ruleGauge(row)),esc(rulePU(row)),(fr*100).toFixed(0),(et*100).toFixed(1),
        view.base == null ? '' : `<br>Baseline: FR ${Number.isFinite(ruleFR(row,view.base)) ? ruleFR(row,view.base).toFixed(2) : 'missing'} · ${Number.isFinite(ruleETP(row,view.base)) ? (100*ruleETP(row,view.base)).toFixed(1)+'% of time beyond gap' : 'gap data missing'}`,jointNames[jointClass(fr,et,PLOT_ET,PLOT_FR)]]),
      hovertemplate:`<b>%{customdata[0]}</b> · %{customdata[6]}<br>Flow regime: ${esc(name)}<br>Gauge %{customdata[1]} · %{customdata[2]}<br>Frequency: %{x:.2f} × target (%{customdata[3]}%)<br>Maximum gap exceeded: %{customdata[4]}% of time%{customdata[5]}<extra></extra>`};
  });
  if(view.base != null) for (const name of regimes) {
    const arrows = g.arrows.filter(a => jointRegime(a.row) === name);
    traces.push({type:'scatter',mode:'markers',name:'Baseline · '+name,legendgroup:name,showlegend:false,
      x:arrows.map(a=>a.bf),y:arrows.map(a=>a.be),marker:{size:9,color:colours.get(name),symbol:'circle-open',line:{width:2}},
      customdata:arrows.map(a=>[esc(ruleCode(a.row)),esc(ruleGauge(a.row)),esc(rulePU(a.row))]),
      hovertemplate:`<b>%{customdata[0]}</b> · baseline<br>Flow regime: ${esc(name)}<br>Gauge %{customdata[1]} · %{customdata[2]}<br>Frequency: %{x:.2f} × target<br>Maximum gap exceeded: %{y:.1%} of time<extra></extra>`});
  }
  const plot = $('jointPlot');
  Plotly.newPlot(plot,traces,{
    height:420,margin:{t:20,r:20,b:90,l:75},plot_bgcolor:'white',paper_bgcolor:'white',font:{size:11},hovermode:'closest',
    hoverlabel:{bgcolor:'white',bordercolor:'#9aa3b0',font:{size:12,color:'#1f2937'},align:'left'},
    legend:{orientation:'h',y:-.24,groupclick:'togglegroup'},
    xaxis:{title:{text:'Frequency ratio (target 0.95)'},range:[0,g.xmax],showline:true,mirror:true,ticks:'outside'},
    yaxis:{title:{text:'Time exceeding maximum gap'},range:[0,1],tickformat:'.0%',showline:true,mirror:true,ticks:'outside'},
    shapes:[{type:'rect',x0:PLOT_FR,x1:g.xmax,y0:0,y1:PLOT_ET,layer:'below',fillcolor:'rgba(32,122,101,0.16)',line:{width:0}},
      {type:'line',x0:PLOT_FR,x1:PLOT_FR,y0:0,y1:1,line:{color:'#888',dash:'dash'}},{type:'line',x0:0,x1:g.xmax,y0:PLOT_ET,y1:PLOT_ET,line:{color:'#888',dash:'dash'}}],
    annotations:[]
      .concat(g.arrows.map(a=>({x:a.fr,y:a.et,ax:a.bf,ay:a.be,xref:'x',yref:'y',axref:'x',ayref:'y',showarrow:true,text:'',arrowhead:3,arrowsize:1.6,arrowwidth:2.5,arrowcolor:colours.get(jointRegime(a.row))})))
  },PCFG).then(() => {
    if (!plot.on) return;
    plot.removeAllListeners('plotly_restyle');
    plot.on('plotly_restyle', () => {
      const updates = {};
      g.arrows.forEach((a,i) => {
        const trace = plot.data.find(t => t.legendgroup === jointRegime(a.row) && t.showlegend !== false);
        updates[`annotations[${i}].visible`] = trace?.visible !== 'legendonly' && trace?.visible !== false;
      });
      if (g.arrows.length) Plotly.relayout(plot, updates);
    });
  });
  $('jointNote').textContent = `${g.omittedCurrent} current points outside the axes${view.base == null ? '' : `; ${g.omittedBase} baseline points outside`}. Missing metrics are not plotted.${view.base == null ? '' : ' Hollow dots show the baseline; arrows point to the comparison.'}`;
}

/** Fetch JSON from the local server, throwing its error message on failure. */
async function performanceJSON(url, signal) {
  const response = await fetch(url, {signal});
  const data = await response.json();
  if (!response.ok || data.error) throw Error(data.error || `Request failed (${response.status})`);
  return data;
}
// Share pending and resolved inventory requests with the existing event selector. Evict failures for retry.
const inventoryRequests = new Map();
/** Forget a gauge's cached inventory (all gauges when none is given), e.g. after user EWRs change. */
function invalidateInventory(gauge) { if (gauge) inventoryRequests.delete(gauge); else inventoryRequests.clear(); }
/** The gauge's /api/ewrs inventory, shared by concurrent callers; a failed request is evicted so it can be retried. */
function inventoryFor(gauge) {
  if (!inventoryRequests.has(gauge)) {
    const pending = performanceJSON('/api/ewrs?gauge='+encodeURIComponent(gauge)).catch(error => {
      if (inventoryRequests.get(gauge) === pending) inventoryRequests.delete(gauge); throw error;
    });
    inventoryRequests.set(gauge,pending);
  }
  return inventoryRequests.get(gauge);
}
/** A server inventory with its user EWRs replaced by the browser registry's current definitions. */
function currentInventory(gauge,data) {
  if (typeof UEWR === 'undefined') return data;
  const user = UEWR.atGauge(gauge);
  return {ewrs:data.ewrs.filter(e => !e.custom).concat(user), inventory:data.inventory.filter(e => !e.custom).concat(user.map(e => ({...e,detailed:true})))};
}
/** Show the selected gauge's rule inventory and which rules support annual diagnostics. */
async function loadInventoryView(view) {
  if (!view.gauge) { $('diagnosticInventory').textContent = 'Select a gauge to inspect its inventory.'; return; }
  try {
    const inv = currentInventory(view.gauge,await inventoryFor(view.gauge)); if (!currentPerformance(view)) return;
    $('diagnosticInventory').innerHTML = '<table><tr><th>Rule / planning unit</th><th>Type / handler</th><th>Annual diagnostics</th></tr>'+inv.inventory.map(r => `<tr><td>${esc(r.code+' · '+r.pu)}</td><td>${esc(r.kind+' / '+r.handler)}</td><td>${r.detailed ? 'Available' : 'Not supported'}</td></tr>`).join('')+'</table>';
  } catch (error) { if (currentPerformance(view)) $('diagnosticInventory').textContent = 'Inventory unavailable: '+error.message; }
}
/** A promise that settles like `promise`, or rejects with an AbortError as soon as `signal` aborts. */
function abortable(promise, signal) {
  return new Promise((resolve,reject) => {
    const abort = () => reject(new DOMException('Cancelled','AbortError'));
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort',abort,{once:true});
    promise.then(resolve,reject).finally(() => signal.removeEventListener('abort',abort));
  });
}
/** Run `work` over items with at most `limit` in flight, stopping on abort; results are {value} or {error} in item order. */
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
/** The inventory rules matching the selected rows, one per (code, planning unit). */
function selectedAnnualRules(inventory, rows) {
  const selected = new Map();
  for (const e of inventory.ewrs) for (const row of rows) {
    const pu = rulePU(row);
    if (ruleCode(row) === e.code && e.pus.includes(pu)) {
      // The selector groups equivalent definitions under a canonical PU; evaluate each actual selected asset.
      selected.set(JSON.stringify([e.code,pu]), {...e,pu});
    }
  }
  return [...selected.values()];
}
/** Purge any charts in `parent` and replace its contents with one new, empty chart element. */
function freshChart(parent) {
  parent.querySelectorAll('.js-plotly-plot').forEach(el => Plotly.purge(el));
  const chart = document.createElement('div');
  parent.replaceChildren(chart);
  return chart;
}
/** Load annual diagnostics for selected rules, updating charts only while this request is current. */
async function loadSequences(view) {
  if (!currentPerformance(view) || !view.ctx?.ok) return;
  view.sequence?.abort();
  const controller = new AbortController(); view.sequence = controller;
  const button = $('loadSequence'), cancel = $('cancelSequence'), note = $('sequenceNote');
  const active = () => currentPerformance(view) && view.sequence === controller && !controller.signal.aborted;
  if (!button || !cancel || !note) return;
  button.disabled = true; cancel.hidden = false; note.textContent = 'Loading supported rule inventory…';
  try {
    if (typeof Plotly === 'undefined') throw Error('Plotly is unavailable; annual charts require the chart library.');
    const inv = currentInventory(view.gauge,await abortable(inventoryFor(view.gauge),controller.signal)); if (!active()) return;
    const eligible = view.rows.filter(r => !r.user || (ruleCurrent(r,view.i) && (view.base == null || ruleCurrent(r,view.base))));
    const list = selectedAnnualRules(inv,eligible);
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
      const r = view.rows.find(r => ruleCode(r) === e.code && rulePU(r) === e.pu);
      const group = r ? [...new Set(r[4].map(o => META.themes[META.objTheme[o]]))].sort().join(' / ') || 'Unclassified' : 'Unclassified';
      for (const [key,s] of Object.entries(d.scenarios)) {
        const name = `${group} · ${e.code} · ${e.pu} · ${key === 'base' ? c.bn : c.cn} · ${ayName(d.ystart)}`;
        series.push({name,group,thr:e.thr,years:s.years,stats:sequenceStats(s.years)});
        const v = s.coverage;
        coverage.push(`${name}: ${v.start ?? 'unknown'} – ${v.end ?? 'unknown'}; ${v.missing_days} missing days; ${v.complete_years}/${v.years} complete assessment years; MRIP ${d.mrip_days ?? 'unavailable'} days`);
      }
    });
    series.sort((a,b) => a.group.localeCompare(b.group) || a.thr - b.thr || a.name.localeCompare(b.name));
    if (!series.length) throw Error('No annual results. '+errors.join('; '));
    await plotSequences(series,active);
    if (!active()) return;
    note.textContent = `${series.length} rule/scenario rows. Blue = achieved, dark grey with × = failed, white = incomplete/unavailable (not a failure). Assessment windows differ; aligned start years do not imply simultaneous opportunities. Time since success is unknown before the first observed success or across missing years; rolling values require ${ROLLING_YEARS} consecutive complete years. ${errors.length ? 'Failures: '+errors.join('; ') : ''}`;
    $('performanceCoverage').textContent = 'Provenance: py_ewr evaluation of uploaded daily series. '+coverage.join(' | ');
  } catch (error) { if (active()) note.textContent = 'Could not load annual diagnostics: '+error.message; }
  finally {
    // Captured elements cannot mutate replacement controls. A superseded request must not enable a newer one.
    if (view.sequence === controller) {
      button.disabled = false; cancel.hidden = true;
      if (currentPerformance(view) && controller.signal.aborted) note.textContent = 'Annual loading cancelled. You can retry.';
    }
  }
}
// Each series keeps one colour and dash on every chart: 8 distinguishable colours (all at least 4.5:1 on white), then the dash changes.
const SERIES_COLOURS = ['#1f4e8c','#c0392b','#0e7a62','#8e44ad','#a65200','#2c3e50','#c2185b','#5f6b6d'], SERIES_DASHES = ['solid','dash','dot','dashdot'];
/** Return a stable colour and dash for a zero-based series index, cycling dashes after each palette. */
const seriesStyle = i => ({color:SERIES_COLOURS[i % SERIES_COLOURS.length], dash:SERIES_DASHES[Math.floor(i / SERIES_COLOURS.length) % SERIES_DASHES.length]});
const DASH_SVG = {solid:'', dash:'5 3', dot:'1.5 2.5', dashdot:'5 2 1.5 2'};
// Three annual states: achieved, failed and missing. A colour bar cannot show missing (null) cells, so the key is HTML with a swatch for each.
const ANNUAL_ACHIEVED = '#2166ac', ANNUAL_FAILED = '#6b7280';
/** Return an HTML key distinguishing achieved, failed and missing annual assessments. */
const annualStateKey = () => `<div class="swl"><span><i style="background:${ANNUAL_ACHIEVED}"></i>achieved</span><span><i style="background:${ANNUAL_FAILED}">×</i>failed</span><span><i style="background:#fff;border:1px dashed #9aa3b0"></i>missing: incomplete or unavailable</span></div>`;
// A selector toggles series on the line charts instead of a legend, which is unreadable past a handful of long rule names.
/** Populate an optional selector that toggles matching series on the supplied line charts. */
function seriesSelector(el, series, charts) {
  if (!el) return;
  el.innerHTML = annualStateKey()+`<details class="sersel"><summary>Series shown: <span>${series.length} of ${series.length}</span></summary><div class="pop"><div><button type="button" class="ghost" data-all="1">All</button><button type="button" class="ghost" data-all="0">None</button></div>${series.map((s,i) => { const st = seriesStyle(i);
    return `<label><input type="checkbox" data-i="${i}" checked><svg width="24" height="8"><line x1="0" y1="4" x2="24" y2="4" stroke="${st.color}" stroke-width="2" stroke-dasharray="${DASH_SVG[st.dash]}"/></svg>${esc(s.name)}</label>`; }).join('')}</div></details>`;
  /** Apply checkbox visibility to every initialized chart and update the selected-series count. */
  const sync = () => {
    const on = [...el.querySelectorAll('input[data-i]')].map(i => i.checked);
    charts.forEach(chart => { if (chart.data) Plotly.restyle(chart,{visible:on}); });
    el.querySelector('summary span').textContent = `${on.filter(Boolean).length} of ${on.length}`;
  };
  el.onchange = sync;
  el.onclick = event => { const b = event.target.closest('[data-all]'); if (!b) return; el.querySelectorAll('input[data-i]').forEach(i => { i.checked = b.dataset.all === '1'; }); sync(); };
}
/** Align annual series by start year and draw the raster and trends, purging charts if active() is false. */
async function plotSequences(series, active) {
  const years = [...new Set(series.flatMap(s => s.years.wy))].sort((a,b) => a-b);
  const aligned = (s,v) => { const m = new Map(s.years.wy.map((w,i) => [w,v[i]])); return years.map(w => m.get(w) ?? null); };
  // Each run owns its chart nodes, including retries within the SAME analytical selection.
  const raster = freshChart($('sequenceRaster')), since = freshChart($('sequenceSince')), rolling = freshChart($('sequenceRolling'));
  const z = series.map(s => aligned(s,s.years.achieved.map((a,i) => s.years.complete[i] ? (a ? 1 : 0) : null)));
  // Row labels are short (rule · scenario); the full name, with group, planning unit and window, is in the hover.
  /** Shorten a series name to rule and scenario, falling back to the full name. */
  const short = s => s.name.split(' · ').slice(1,2).concat(s.name.split(' · ').slice(3,4)).join(' · ') || s.name;
  await Plotly.newPlot(raster,[{type:'heatmap',x:years,y:series.map((s,i) => `${i+1}. ${short(s)}`),z,zmin:0,zmax:1,
    colorscale:[[0,ANNUAL_FAILED],[.5,ANNUAL_FAILED],[.5,ANNUAL_ACHIEVED],[1,ANNUAL_ACHIEVED]],showscale:false,hoverongaps:true,xgap:1,ygap:2,
    text:z.map(r => r.map(v => v === 0 ? '×' : '')),texttemplate:'%{text}',textfont:{color:'#fff',size:11},
    customdata:z.map((r,j) => r.map(v => [series[j].name, v == null ? 'missing (incomplete or unavailable)' : v ? 'achieved' : 'failed'])),
    hovertemplate:'%{customdata[0]}<br>%{x}: <b>%{customdata[1]}</b><extra></extra>'}],
    {height:Math.max(250,series.length*30),margin:{l:20,r:20,t:20,b:40},plot_bgcolor:'#fff',yaxis:{automargin:true,autorange:'reversed',tickfont:{size:11}},xaxis:{title:'Assessment start year (windows shown in the hover)',dtick:1}},PCFG);
  if (!active()) { Plotly.purge(raster); return; }
  for (const [el,key,title] of [[since,'since','Years since last successful assessment year'],[rolling,'rolling',`${ROLLING_YEARS}-year rolling achievement proportion`]]) {
    await Plotly.newPlot(el,series.map((s,i) => ({type:'scatter',mode:'lines+markers',name:s.name,x:years,y:aligned(s,s.stats[key]),connectgaps:false,
      line:{color:seriesStyle(i).color,dash:seriesStyle(i).dash,width:2},marker:{color:seriesStyle(i).color,size:5},hovertemplate:`${esc(s.name)}<br>%{x}: <b>%{y:.2f}</b><extra></extra>`})),
      {height:280,margin:{l:70,r:20,t:20,b:45},showlegend:false,xaxis:{title:'Assessment start year'},yaxis:{title,...(key === 'rolling' ? {range:[0,1]} : {})}},PCFG);
    if (!active()) { Plotly.purge(el); return; }
  }
  seriesSelector($('sequenceSeries'),series,[since,rolling]);
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
/** Parse an observations CSV (date plus stage / temperature / dissolved oxygen) into rows sorted by date, with daily stage change. */
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
/** Insert a value-less row after each gap in the dates, so Plotly breaks the lines there. */
function breakConditionGaps(data) {
  // One separator per gap, never one row per missing day. Output is at most 2n-1.
  const out = [];
  data.forEach((r,i) => { if (i && r.time-data[i-1].time > 86400000) out.push({date:new Date(data[i-1].time+86400000).toISOString().slice(0,10)}); out.push(r); });
  return out;
}
/** Read an observations CSV and chart it for the selected gauge; a newer load supersedes this one. */
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
    // A prior Plotly promise may still finish after abort; never let it share this run's target.
    const chart = freshChart(plot);
    await Plotly.newPlot(chart,traces,layout,PCFG);
    if (!active()) { Plotly.purge(chart); return; }
    note.textContent = `${file.name} · gauge ${view.gauge} · ${data[0].date} – ${data.at(-1).date}. ${rates.n} adjacent daily stage pairs. Maximum rise ${rates.n ? rates.rise.toFixed(3) : 'unavailable'} m/day; maximum recession ${rates.n ? rates.fall.toFixed(3) : 'unavailable'} m/day. User-supplied observations for this selection.`;
  } catch (error) { if (active()) { note.textContent = error.message; plot.innerHTML = ''; } }
}
