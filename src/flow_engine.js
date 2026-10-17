// Flow analysis in the browser: the page's flow charts are drawn from the daily flows and py_ewr results embedded in the page at
// build time (src/build_dashboard.py), so nothing is fetched from a server. Each function mirrors the function of the same name in
// src/ewr_engine.py (same query fields, same output), which is the reference it is tested against (tests/test_flow_engine_parity.py).
//
//   const fe = FlowEngine.create(payload);   payload = {flows, ewrs, ewryears, regimes} (see build_dashboard.bake_scenarios)
//   fe.run("heatmap", {base, comp, gauge, xlim, dmax, sm, em, days, mthr, mdur})          query values are strings, as in a URL
//
// flows[scenario id] = {start: "YYYY-MM-DD", gauges: {gauge: [flow or null, one per calendar day from start]}}
// What needs py_ewr itself (the EWR list at a gauge, one EWR's yearly results and events, the flow regime thresholds) is
// precomputed per scenario and only combined here.
const FlowEngine = (() => {
  const DAY = 864e5;
  const fail = msg => { throw new Error(msg); };
  const dayNum = s => Math.round(Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / DAY);
  const dateStr = n => new Date(n * DAY).toISOString().slice(0, 10);
  const ymOf = n => { const d = new Date(n * DAY); return [d.getUTCFullYear(), d.getUTCMonth() + 1]; };
  const isLeap = y => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
  const feb29 = w => isLeap(w + 1);                                    // water year w (1 Jul w - 30 Jun w+1) holds a 29 February
  const waterYear = n => { const [y, m] = ymOf(n); return m >= 7 ? y : y - 1; };   // py_ewr's label: the calendar year it starts in
  const inWin = (m, sm, em) => (sm <= em ? m >= sm && m <= em : m >= sm || m <= em);
  const num = v => (v == null || v === "" ? NaN : parseFloat(v));
  const int = v => Math.trunc(num(v));

  // numpy semantics, so the numbers match the Python reference
  const rint = y => { const r = Math.round(y); return Math.abs(y % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r; };   // round half to even
  const rnd = (x, d) => { const k = Math.pow(10, d); return rint(x * k) / k; };
  const rndOrNull = (x, d) => (Number.isFinite(x) ? rnd(x, d) : null);
  const linspace = (a, b, n) => { const step = (b - a) / (n - 1), y = Array.from({ length: n }, (_, i) => i * step + a); if (n > 1) y[n - 1] = b; return y; };
  const geomspace = (a, b, n) => { const y = linspace(Math.log10(a), Math.log10(b), n).map(v => Math.pow(10, v)); y[0] = a; y[n - 1] = b; return y; };
  const searchLeft = (a, x) => { let lo = 0, hi = a.length; while (lo < hi) { const mid = (lo + hi) >> 1; if (a[mid] < x) lo = mid + 1; else hi = mid; } return lo; };
  const sortAsc = a => Float64Array.from(a).sort();
  const percentile = (sorted, q) => {                                  // numpy's default (linear) method on an ascending array
    const n = sorted.length, idx = (q / 100) * (n - 1), lo = Math.floor(idx), hi = Math.min(lo + 1, n - 1), t = idx - lo, a = sorted[lo], b = sorted[hi], d = b - a;
    return t >= 0.5 ? b - d * (1 - t) : a + d * t;
  };
  const exceedPct = (sorted, x) => (100 * (sorted.length - searchLeft(sorted, x))) / sorted.length;   // % of days the flow is >= x
  const niceCeiling = x => { if (!x || x <= 0) return 100; const k = Math.pow(10, Math.max(Math.floor(Math.log10(x)) - 1, 0)); return Math.ceil(x / k) * k; };

  function create(payload) {
    const P = Object.assign({ flows: {}, ewrs: {}, ewryears: {}, regimes: {} }, payload || {});

    // a series is {t0: day number of its first day, v: Float64Array (NaN = no flow)} on consecutive calendar days
    function gaugeSeries(id, gauge) {
      const sc = P.flows[id]; if (!sc) fail(`no daily flows are kept for scenario '${id}'`);
      const v = sc.gauges[gauge]; if (!v) fail(`gauge ${gauge} has no flow series in this scenario`);
      return { t0: dayNum(sc.start), v: Float64Array.from(v, x => (x == null ? NaN : x)) };
    }
    function dailySeries(id, gauge) {                                  // from the gauge's first to its last observed day
      const s = gaugeSeries(id, gauge); let a = 0, b = s.v.length - 1;
      while (a <= b && Number.isNaN(s.v[a])) a++;
      while (b >= a && Number.isNaN(s.v[b])) b--;
      if (a > b) fail(`gauge ${gauge} has no flows in this scenario`);
      return { t0: s.t0 + a, v: s.v.slice(a, b + 1) };
    }
    // series on the days [t0, t1]: NaN outside a series' own days
    const onDays = (s, t0, t1) => Float64Array.from({ length: Math.max(0, t1 - t0 + 1) }, (_, i) => { const j = t0 + i - s.t0; return j >= 0 && j < s.v.length ? s.v[j] : NaN; });
    function join(list, how) {
      const ends = list.map(s => [s.t0, s.t0 + s.v.length - 1]);
      const t0 = how === "inner" ? Math.max(...ends.map(e => e[0])) : Math.min(...ends.map(e => e[0]));
      const t1 = how === "inner" ? Math.min(...ends.map(e => e[1])) : Math.max(...ends.map(e => e[1]));
      return { t0, n: Math.max(0, t1 - t0 + 1), cols: list.map(s => onDays(s, t0, t1)) };
    }
    const scenarioIds = q => [["base", q.base]].concat(q.comp ? [["comp", q.comp]] : []);

    // ---- threshold x duration achievement heat map (achievement_frequencies) ----
    function windowDays(wl, sm, em) {                                  // days of the window in water year wl (1 Jul wl-1 - 30 Jun wl)
      let c = 0; for (let n = dayNum(`${wl - 1}-07-01`), e = dayNum(`${wl}-06-30`); n <= e; n++) if (inWin(ymOf(n)[1], sm, em)) c++;
      return c;
    }
    // % of water years in which each threshold is met for >= duration days in the window; flows: one Float64Array per scenario on
    // the days `days` (ascending day numbers). Only water years complete in every scenario count. Returns {z: [scenario][duration][threshold], n}.
    function achievementFrequencies(flows, days, thresholds, durations, sm, em, consecutive) {
      const N = days.length, months = new Int32Array(N), yl = new Int32Array(N), labels = [];
      for (let i = 0; i < N; i++) { const [y, m] = ymOf(days[i]); months[i] = m; const w = m >= 7 ? y + 1 : y; if (!labels.length || labels[labels.length - 1] !== w) labels.push(w); yl[i] = labels.length - 1; }
      const nY = labels.length, mask = Uint8Array.from(months, m => (inWin(m, sm, em) ? 1 : 0));
      const bounds = new Int32Array(nY + 1); for (let i = N - 1; i >= 0; i--) bounds[yl[i]] = i; bounds[nY] = N;
      const expected = labels.map(w => windowDays(w, sm, em));
      const counted = new Uint8Array(nY).fill(1);
      for (const f of flows) {
        const cnt = new Int32Array(nY); for (let i = 0; i < N; i++) if (mask[i] && !Number.isNaN(f[i])) cnt[yl[i]]++;
        for (let y = 0; y < nY; y++) if (!(cnt[y] === expected[y] && expected[y] > 0)) counted[y] = 0;
      }
      const yrs = []; for (let y = 0; y < nY; y++) if (counted[y]) yrs.push(y);
      const z = flows.map(f => {
        const fl = Float64Array.from(f, (v, i) => (mask[i] && !Number.isNaN(v) ? v : -Infinity));
        const annual = durations.map(() => new Float64Array(yrs.length).fill(-Infinity));
        yrs.forEach((y, yi) => {
          const x = fl.subarray(bounds[y], bounds[y + 1]), L = x.length;
          if (consecutive) {                                           // largest, over windows of d days, of the window minimum
            const m = Float64Array.from(x); let cur = 1, len = L;
            durations.forEach((d, k) => {
              while (cur < d && len > 1) { for (let i = 0; i < len - 1; i++) if (x[i + cur] < m[i]) m[i] = x[i + cur]; len--; cur++; }
              if (d <= L && cur === d && len) { let mx = -Infinity; for (let i = 0; i < len; i++) if (m[i] > mx) mx = m[i]; annual[k][yi] = mx; }
            });
          } else {                                                     // the d-th largest in-window flow of the year
            const vals = Float64Array.from(x).sort().reverse();
            durations.forEach((d, k) => { if (d <= vals.length) annual[k][yi] = vals[d - 1]; });
          }
        });
        return durations.map((_, k) => {
          if (!yrs.length) return thresholds.map(() => NaN);
          const a = annual[k].slice().sort();
          return thresholds.map(t => ((yrs.length - searchLeft(a, t)) * 100) / yrs.length);
        });
      });
      return { z, n: yrs.length };
    }
    function heatmap(q) {
      const ids = [q.base].concat(q.comp ? [q.comp] : []), j = join(ids.map(id => gaugeSeries(id, q.gauge)), "inner");
      const days = [], cols = ids.map(() => []);
      for (let i = 0; i < j.n; i++) if (j.cols.some(c => !Number.isNaN(c[i]))) { days.push(j.t0 + i); j.cols.forEach((c, s) => cols[s].push(c[i])); }
      if (!days.length) fail("the scenarios share no dates at this gauge");
      const flows = cols.map(c => Float64Array.from(c));
      let xlim = q.xlim ? int(q.xlim) : niceCeiling(percentile(sortAsc(cols.flat().filter(v => !Number.isNaN(v))), 99.5));
      xlim = Math.max(10, Math.min(xlim, 200000));
      const dmax = Math.max(2, Math.min(int(q.dmax || 120), 366)), sm = int(q.sm || 7), em = int(q.em || 6), consec = q.days === "consec";
      const spacing = Math.max(1, Math.ceil((xlim + 1) / 400));
      const thresholds = []; for (let t = 0; t < xlim + 1; t += spacing) thresholds.push(t);
      const durations = Array.from({ length: dmax }, (_, i) => i + 1);
      const af = achievementFrequencies(flows, days, thresholds, durations, sm, em, consec), Z = a => a.map(r => r.map(v => rndOrNull(v, 3)));
      const out = { thresholds, durations, xlim, spacing, years: af.n, dates: [dateStr(days[0]), dateStr(days[days.length - 1])], base: Z(af.z[0]) };
      if (ids.length === 2) { out.comp = Z(af.z[1]); out.diff = Z(af.z[1].map((r, k) => r.map((v, t) => v - af.z[0][k][t]))); }
      if (q.mthr && q.mdur) {                                          // exact % of years at one (threshold, duration), e.g. an EWR's own
        const one = achievementFrequencies(flows, days, [num(q.mthr)], [int(q.mdur)], sm, em, consec), mark = one.z.map(s => rnd(s[0][0], 3));
        if (mark.every(Number.isFinite)) out.mark = mark;
      }
      out.consecutive = consec;
      return out;
    }

    // ---- hydrograph and flow duration curves (curves) ----
    function paired(q) {                                               // observed days; two scenarios: only the days both have a flow
      const keys = ["base", "comp"].filter(k => q[k]), j = join(keys.map(k => gaugeSeries(q[k], q.gauge)), "inner");
      const out = { days: [] }; keys.forEach(k => (out[k] = []));
      for (let i = 0; i < j.n; i++) if (j.cols.every(c => !Number.isNaN(c[i]))) { out.days.push(j.t0 + i); keys.forEach((k, s) => out[k].push(j.cols[s][i])); }
      if (keys.length === 2 && !out.days.length) fail("the scenarios share no observed dates at this gauge");
      return { keys, ...out };
    }
    function curves(q) {
      const kind = q.kind || (q.comp ? "fdc" : "hydro"), pr = paired(q);
      if (kind === "hydro") {
        const keys = ["base", "comp"].filter(k => q[k]), j = join(keys.map(k => dailySeries(q[k], q.gauge)), q.comp ? "inner" : "outer");
        if (!j.n) fail("the scenarios share no dates at this gauge");
        const out = { kind: "hydro", dates: Array.from({ length: j.n }, (_, i) => dateStr(j.t0 + i)) };
        keys.forEach((k, s) => (out[k] = Array.from(j.cols[s], v => (Number.isNaN(v) ? null : rnd(v, 2)))));
        out.flow = out.base;
        const full = Array.from({ length: j.n }, (_, i) => j.cols.every(c => !Number.isNaN(c[i]))), years = [], byWy = new Map();
        for (let i = 0; i < j.n; i++) { const w = waterYear(j.t0 + i); (byWy.get(w) || byWy.set(w, []).get(w)).push(i); }
        for (const [w, idx] of [...byWy].sort((a, b) => a[0] - b[0])) {
          if (idx.length === (feb29(w) ? 366 : 365) && idx.every(i => full[i])) {
            const y = { wy: w }; keys.forEach((k, s) => (y[k] = rnd(idx.reduce((t, i) => t + j.cols[s][i], 0), 1))); years.push(y);
          }
        }
        out.years = years;
        out.missing_days = full.filter(x => !x).length;
        return out;
      }
      if (kind === "mdfdc") {                                          // per calendar month: change in % of its days at or above each flow
        if (!q.comp) fail("the monthly differential flow duration needs two scenarios");
        const order = [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6], months = pr.days.map(n => ymOf(n)[1]), by = {};
        for (const k of ["base", "comp"]) by[k] = Object.fromEntries(order.map(m => [m, sortAsc(pr[k].filter((_, i) => months[i] === m))]));
        let top = 0;
        for (const k in by) for (const m of order) if (by[k][m].length) top = Math.max(top, percentile(by[k][m], 99));
        let ymax = q.ymax ? num(q.ymax) : top, flows;
        if (ymax <= 0) fail("no positive flows to plot");
        if (q.ymin != null && q.ymin !== "") {                         // zoomed in: the same 200 steps spread over the chosen range
          const ymin = Math.max(0, num(q.ymin)); if (ymax <= ymin) fail("the flow range is empty");
          flows = linspace(ymin, ymax, 200);
        } else { ymax = niceCeiling(ymax); flows = linspace(0, ymax, 201).slice(1); }
        const grid = k => flows.map(f => order.map(m => (by[k][m].length ? exceedPct(by[k][m], f) : NaN)));
        const base = grid("base"), comp = grid("comp"), Z = a => a.map(r => r.map(v => rndOrNull(v, 3)));
        return { kind: "mdfdc", months: order, flows: flows.map(f => rnd(f, 2)), diff: Z(comp.map((r, i) => r.map((v, m) => v - base[i][m]))), base: Z(base), comp: Z(comp), ymax };
      }
      const sm = int(q.sm || 0), em = int(q.em || 0), win = sm >= 1 && sm <= 12 && em >= 1 && em <= 12;   // optional timing window
      const vals = {};
      for (const k of pr.keys) vals[k] = sortAsc(pr[k].filter((_, i) => !win || inWin(ymOf(pr.days[i])[1], sm, em)));
      if (Object.values(vals).some(v => !v.length)) fail("no flow data inside that timing window");
      const thrs = String(q.thr || "").split(",").filter(t => t.trim()).map(Number);
      const marks = thrs.map(t => { const m = { thr: t }; for (const k in vals) m[k] = rnd(exceedPct(vals[k], t), 3); return m; });
      if (kind === "fdc") {
        const p = linspace(0, 100, 401), out = { kind: "fdc", p: p.map(v => rnd(v, 2)), marks };
        for (const k in vals) out[k] = p.map(x => rnd(percentile(vals[k], 100 - x), 2));   // flow equalled or exceeded p% of the time
        if (vals.comp) out.diff = out.comp.map((v, i) => rnd(v - out.base[i], 2));
        return out;
      }
      if (kind === "dfdc") {
        if (!vals.comp) fail("the differential flow duration curve needs two scenarios");
        const all = [...vals.base, ...vals.comp], pos = all.filter(v => v > 0);
        if (!pos.length) fail("no positive flows to plot");
        const xs = geomspace(Math.max(pos.reduce((a, b) => Math.min(a, b)), 0.01), all.reduce((a, b) => Math.max(a, b)), 500);   // no spread: a century of days is too many arguments
        const pb = xs.map(x => exceedPct(vals.base, x)), pc = xs.map(x => exceedPct(vals.comp, x));
        return { kind: "dfdc", x: xs.map(v => rnd(v, 3)), base: pb.map(v => rnd(v, 3)), comp: pc.map(v => rnd(v, 3)), diff: pc.map((v, i) => rnd(v - pb[i], 3)), marks };
      }
      fail(`unknown curve kind '${kind}'`);
    }

    // ---- low-flow spells and flow regime components (_runs_by_year) ----
    // runs of consecutive days where hit(flow) is true (a missing day is never a hit and breaks a run; a run beside a missing day
    // or the record's ends is censored); per complete water year: days that are hits, longest run inside the year, runs starting in it
    function runsByYear(s, hit) {
      const n = s.v.length, miss = Array.from(s.v, Number.isNaN), h = Array.from(s.v, (v, i) => !miss[i] && hit(v)), wy = Array.from({ length: n }, (_, i) => waterYear(s.t0 + i));
      const runs = [];
      for (let i = 0; i < n;) {
        if (!h[i]) { i++; continue; }
        let j = i; while (j + 1 < n && h[j + 1]) j++;
        runs.push({ start: dateStr(s.t0 + i), end: dateStr(s.t0 + j), days: j - i + 1, censored: (i > 0 && miss[i - 1]) || (j + 1 < n && miss[j + 1]) || i === 0 || j === n - 1, wy: wy[i] });
        i = j + 1;
      }
      const starts = new Map(); for (const r of runs) starts.set(r.wy, (starts.get(r.wy) || 0) + 1);
      const years = { wy: [], days: [], longest: [], n: [], complete: [] };
      for (let a = 0; a < n;) {                                        // consecutive days, so each water year is one block
        const w = wy[a]; let b = a; while (b < n && wy[b] === w) b++;
        const idx = Array.from({ length: b - a }, (_, i) => a + i); a = b;
        const comp = idx.length === (feb29(w) ? 366 : 365) && !idx.some(i => miss[i]);
        let best = 0, cur = 0, tot = 0; for (const i of idx) { cur = h[i] ? cur + 1 : 0; if (cur > best) best = cur; if (h[i]) tot++; }
        years.wy.push(w); years.complete.push(comp ? 1 : 0);
        years.days.push(comp ? tot : null); years.longest.push(comp ? best : null); years.n.push(comp ? starts.get(w) || 0 : null);
      }
      return { years, runs };
    }
    function pairYears(out) {                                          // two scenarios: the water years complete in both
      if (out.scenarios.comp) {
        const ok = y => new Set(y.wy.filter((_, i) => y.complete[i])), b = ok(out.scenarios.base.years), c = ok(out.scenarios.comp.years);
        out.paired = { wy: [...b].filter(w => c.has(w)).sort((x, y) => x - y) };
      }
      return out;
    }
    function spells(q) {
      const thr = num(q.thr), out = { gauge: q.gauge, thr, scenarios: {} };
      for (const [k, id] of scenarioIds(q)) {
        const s = dailySeries(id, q.gauge), { years, runs } = runsByYear(s, v => v < thr);
        years.below = years.days; delete years.days;
        out.scenarios[k] = { years, longest_runs: runs.slice().sort((a, b) => b.days - a.days).slice(0, 10), n_runs: runs.length, missing_days: s.v.filter(Number.isNaN).length };
      }
      return pairYears(out);
    }
    function regimes(q) {                                              // thresholds come from the EWR tool (precomputed per gauge)
      const comps = P.regimes[q.gauge] || [], out = { gauge: q.gauge, regimes: comps, scenarios: {} };
      for (const [k, id] of scenarioIds(q)) {
        const s = dailySeries(id, q.gauge), per = comps.map(c => runsByYear(s, v => v >= c.thr).years);
        out.scenarios[k] = { regimes: per, years: per.length ? per[0] : { wy: [], complete: [] } };
      }
      return pairYears(out);
    }

    // ---- py_ewr results precomputed at build time ----
    const records = t => (Array.isArray(t) ? t : t.rows.map(row => Object.fromEntries(t.cols.map((c, i) => [c, row[i]]))));   // build_dashboard.columns, undone
    function ewrResult(id, gauge, code, pu) {
      const r = ((P.ewryears[id] || {})[gauge] || {})[`${code}|${pu || ""}`];
      if (!r) fail(`EWR ${code} is not defined at gauge ${gauge}`);
      if (r.error) fail(r.error);
      return { def: r.def, scenario: Object.assign({}, r.scenario, { events: records(r.scenario.events), gaps: records(r.scenario.gaps) }) };
    }
    function ewryears(q) {
      const out = { code: q.ewr, gauge: q.gauge, scenarios: {} };
      for (const [k, id] of scenarioIds(q)) { const r = ewrResult(id, q.gauge, q.ewr, q.pu); out.scenarios[k] = r.scenario; Object.assign(out, r.def); }
      if (out.scenarios.comp) {                                        // achievement compared over the water years complete in both
        const b = out.scenarios.base.years, c = out.scenarios.comp.years, idx = y => new Map(y.wy.map((w, i) => [w, i]).filter(([, i]) => y.complete[i]));
        const bi = idx(b), ci = idx(c), common = [...bi.keys()].filter(w => ci.has(w)).sort((x, y) => x - y);
        const pb = common.map(w => b.achieved[bi.get(w)]), pc = common.map(w => c.achieved[ci.get(w)]), n = common.length, sum = a => a.reduce((t, x) => t + x, 0);
        out.paired = { wy: common, n, base: sum(pb), comp: sum(pc), gained: pb.filter((x, i) => !x && pc[i]).length, lost: pb.filter((x, i) => x && !pc[i]).length,
          pct_base: n ? rnd((100 * sum(pb)) / n, 3) : null, pct_comp: n ? rnd((100 * sum(pc)) / n, 3) : null };
      }
      return out;
    }
    function interevents(q) {                                          // gaps between successful events longer than the maximum
      const r = ewrResult(q.scn, q.gauge, q.ewr, q.pu), mrip = r.def.mrip_days;
      if (!mrip) return { mrip_days: 0, exceed: [], n_gaps: 0 };
      const gaps = r.scenario.gaps;
      return { mrip_days: mrip, maxint_years: mrip / 365, n_gaps: gaps.length, n_censored: gaps.filter(g => g.censored).length,
        exceed: gaps.filter(g => g.excess).map(g => ({ start: g.start, end: g.end, days: g.days })) };
    }
    const ewrs = q => ({ ewrs: P.ewrs[q.gauge] || [] });

    const KINDS = { heatmap, curves, spells, regimes, ewryears, interevents, ewrs };
    return { run: (kind, q) => (KINDS[kind] || fail(`unknown analysis '${kind}'`))(q || {}) };
  }
  return { create };
})();
