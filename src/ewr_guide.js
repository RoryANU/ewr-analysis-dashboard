// EWR guide (left-hand panel): search the EWR parameter sheet, pick up to 5 EWRs, and watch an animated tutorial of each one.
// Inlined into the page by build_dashboard.py (__EWR_GUIDE_JS__). Part 1 is plain logic with no DOM (tested by tests/test_ewr_guide.js);
// part 2 builds the panel and runs the animations. The tutorial's flow record is illustrative, generated from the EWR's own parameters.
// Needs EWRIDX = {n, strs, cols} from the page (see ewr_index() in build_dashboard.py).

// ---- part 1: parameter rows, search, selection, illustrative record ---------------------------------------------------------------
const GUIDE_MAX = 5;
const MON3 = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WY_MONTHS = [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6];   // the EWR year runs July to June
const MONTH_LEN = { 1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31 };
const WY_DAYS = 365;
const STR_COLS = ["pu", "st", "g", "gn", "c", "ca", "sdl", "k", "eo", "mg", "h"];

/** Day of the July-June year (0 = 1 July) of a calendar month and day. */
function wyDay(month, day) {
  let d = 0;
  for (const m of WY_MONTHS) { if (m === month) break; d += MONTH_LEN[m]; }
  return d + Math.min(day, MONTH_LEN[month]) - 1;
}

/** The parameter sheet as one object per row (strings looked up from the shared table). */
function ewrRows(idx) {
  const rows = [];
  for (let i = 0; i < idx.n; i++) {
    const r = { i };
    for (const k in idx.cols) {
      const v = idx.cols[k][i];
      r[k] = v != null && STR_COLS.includes(k) ? idx.strs[v] : v;
    }
    r.key = `${r.g}|${r.c}|${r.pu}`;
    rows.push(r);
  }
  return rows;
}

const REGIMES = [[/^CF/, "Cease to flow"], [/^BF/, "Base flow"], [/^LF|^W-LF/, "Low flow"], [/^SF/, "Small fresh"], [/^OB/, "Overbank"], [/^BK/, "Bankfull"]];
/** Return the flow regime label for a recognised EWR code prefix, or an empty string. */
const regimeName = code => (REGIMES.find(([re]) => re.test(code)) || [0, ""])[1];

/** The tutorial covers the EWRs py_ewr evaluates with flow_handle or lowflow_handle (its calc config); the rest are still in development. */
const ewrSupported = r => r.h === "flow_handle" || r.h === "lowflow_handle";

/** Header text of an EWR's dropdown, in the style of "Flow analysis · 425008 · Darling@wilc. Main C". */
const ewrTitle = r => `EWR · ${r.c} · ${r.g}${r.gn ? " · " + r.gn : ""}`;

/** Filter rows. q = {text, state, pu, gauge, code, kind}; text words must each appear in code / gauge / name / planning unit / catchment / SDL / objectives. */
function searchEwrs(rows, q) {
  const words = (q.text || "").toLowerCase().split(/\s+/).filter(Boolean);
  const gauge = (q.gauge || "").trim().toLowerCase(), code = (q.code || "").trim().toLowerCase();
  const out = rows.filter(r => {
    if (q.state && r.st !== q.state) return false;
    if (q.pu && r.pu !== q.pu) return false;
    if (q.kind && r.k !== q.kind) return false;
    if (gauge && !(`${r.g} ${r.gn || ""}`.toLowerCase().includes(gauge))) return false;
    if (code && !r.c.toLowerCase().includes(code)) return false;
    if (words.length) {
      const hay = `${r.c} ${r.g} ${r.gn || ""} ${r.pu} ${r.ca || ""} ${r.sdl || ""} ${r.eo || ""} ${regimeName(r.c)}`.toLowerCase();
      if (!words.every(w => hay.includes(w))) return false;
    }
    return true;
  });
  return out.sort((a, b) => (a.g < b.g ? -1 : a.g > b.g ? 1 : a.c < b.c ? -1 : a.c > b.c ? 1 : a.pu < b.pu ? -1 : a.pu > b.pu ? 1 : 0));
}

/** Add or remove a key from the selection; refuses a 6th. Returns {sel, added, full}. */
function toggleSelection(sel, key, max = GUIDE_MAX) {
  if (sel.includes(key)) return { sel: sel.filter(k => k !== key), added: false, full: false };
  if (sel.length >= max) return { sel, added: false, full: true };
  return { sel: [...sel, key], added: true, full: false };
}

/** The EWR as a drawable rule. mode: flow (stay in [lo, hi]), dry (flow at or below hi), level, volume, or other (not drawn). */
function ewrSpec(r) {
  const pos = v => (v != null && Number.isFinite(v) ? v : null);
  const sm = r.sm, em = r.em, sd = r.sd || 1, ed = r.ed || MONTH_LEN[em];
  const s = wyDay(sm, sd), e = wyDay(em, ed);
  const segs = s <= e ? [[s, e]] : [[s, WY_DAYS - 1], [0, e]];
  const maxLen = segs.reduce((m, [a, b]) => Math.max(m, b - a + 1), 0);
  const dur = Math.min(pos(r.dur) > 0 ? r.dur : 1, maxLen);   // an event cannot be longer than the window it must sit in
  const ms = Math.min(pos(r.ms) > 0 ? r.ms : dur, dur);
  const epyReq = pos(r.epy) > 0 ? r.epy : 1;
  const epy = Math.max(1, Math.min(epyReq, Math.floor((maxLen + 6) / (dur + 6))));   // how many events fit in the window
  const spec = {
    mode: "other", lo: 0, hi: null, unit: "ML/d", dur, ms, epy, epyReq, segs, maxLen, sm, sd, em, ed, tf: pos(r.tf), tfl: pos(r.tfl), tfh: pos(r.tfh),
    mi: pos(r.mi), gap: pos(r.gap) || 0, vol: null, acc: null, whole: segs.length === 1 && segs[0][0] === 0 && segs[0][1] === WY_DAYS - 1,
  };
  if (r.k === "V") {
    if (pos(r.vol) > 0) {
      spec.mode = "volume"; spec.vol = r.vol;
      spec.acc = Math.max(1, Math.min(pos(r.acc) > 0 ? r.acc : dur, maxLen));
    }
  } else if (r.k === "L" || r.k === "LL") {
    const lo = pos(r.lmin) ?? 0, hi = pos(r.lmax);
    if (lo > 0 || (hi != null && hi > lo)) { spec.mode = "level"; spec.unit = "m"; spec.lo = lo; spec.hi = hi != null && hi > lo ? hi : null; }
  } else {
    const lo = pos(r.tmin) ?? 0, hi = pos(r.tmax);
    if (lo > 0) { spec.mode = "flow"; spec.lo = lo; spec.hi = hi != null && hi > lo ? hi : null; }
    else if (hi != null && hi > 0) { spec.mode = "dry"; spec.lo = 0; spec.hi = hi; }
  }
  return spec;
}

/** Test whether a water-year day falls in any inclusive EWR window segment. */
const inWindow = (spec, d) => spec.segs.some(([a, b]) => d >= a && d <= b);
/** Test whether a value lies within the inclusive threshold range. */
const inZone = (spec, v) => v >= spec.lo && (spec.hi == null || v <= spec.hi);

/** Plateau value of a qualifying event, and the baseline the flow sits at between events. */
function levels(spec) {
  const { lo, hi } = spec;
  if (spec.mode === "level") {
    const base = lo - 0.9;
    return { base, plateau: hi != null ? (lo + hi) / 2 : lo + 0.5, ymin: lo - 1.6, ymax: (hi != null ? hi : lo) + 1.4 };
  }
  if (spec.mode === "dry") {
    const base = spec.hi * 12;
    return { base, plateau: spec.hi * 0.35, ymin: 0, ymax: base * 1.4 };
  }
  if (spec.mode === "volume") {
    const q = spec.vol / spec.acc * 1.25;
    return { base: q * 0.12, plateau: q, ymin: 0, ymax: q * 1.6 };
  }
  const plateau = hi != null ? Math.min(lo * 1.6, (lo + hi) / 2) : lo * 1.6;
  const top = hi != null && hi <= lo * 20 ? hi * 1.7 : 0;
  return { base: lo * 0.5, plateau, ymin: 0, ymax: Math.max(plateau * 1.4, top, lo * 1.5) };
}

/** Create a seeded Mulberry32 generator returning repeatable values in [0, 1). */
function rng(seed) {   // mulberry32: the same record every time
  let a = seed >>> 0;
  return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

/** Runs of days outside the EWR window: [[start, end], ...]. */
function freeRuns(spec) {
  const runs = [];
  for (let d = 0; d < WY_DAYS; d++) {
    if (inWindow(spec, d)) continue;
    const last = runs[runs.length - 1];
    if (last && last[1] === d - 1) last[1] = d; else runs.push([d, d]);
  }
  return runs;
}
/** Return the accumulation period for volume rules or the duration for other rules. */
const eventLen = spec => (spec.mode === "volume" ? spec.acc : spec.dur);

/** Why a year misses; each needs the rule to allow it (else the next feasible one is used). */
function missFeasible(spec, type) {
  if (type === "short") return spec.mode !== "volume" && Math.min(spec.ms, spec.dur) >= 2;
  if (type === "outside") return freeRuns(spec).some(([a, b]) => b - a + 1 >= eventLen(spec) + 8);
  if (type === "over") return (spec.mode === "flow" || spec.mode === "level") && spec.hi != null && (spec.mode === "level" || spec.hi <= spec.lo * 20);
  return true;   // low
}

/** One year of daily values (length 365, day 0 = 1 July) for type met | short | low | outside | over. */
function genYear(spec, type, seed) {
  const R = rng(seed * 7919 + 17), L = levels(spec), flow = new Array(WY_DAYS);
  const wob = 0.12 + 0.05 * R(), ph = R() * 6.28, ph2 = R() * 6.28;
  for (let d = 0; d < WY_DAYS; d++) {
    const w = 1 + wob * Math.sin(d / 58 + ph) + 0.05 * Math.sin(d / 9 + ph2) + 0.02 * (R() - 0.5);
    flow[d] = spec.mode === "level" ? L.base + 0.3 * (w - 1) : L.base * w;
  }
  const bump = (c, h, wd) => { for (let d = 0; d < WY_DAYS; d++) { const k = Math.exp(-(((d - c) / wd) ** 2)); flow[d] += spec.mode === "level" ? h * k * 0.5 : spec.mode === "dry" ? h * k : L.base * h * k; } };
  const stop = spec.mode === "level" ? 0.2 : spec.mode === "dry" ? L.base * 0.3 : 0.4;
  if (type !== "outside") { bump(40 + R() * 40, stop, 6); bump(250 + R() * 60, stop * 0.8, 9); }
  const events = [];   // [start, length, value]
  const place = (a, b, len, n, value) => {   // n events of len days inside days a..b
    const room = b - a + 1, gap = 6;
    const k = Math.max(1, Math.min(n, Math.floor((room + gap) / (len + gap))));
    const slot = room / k;
    for (let i = 0; i < k; i++) {
      const l = Math.min(len, Math.floor(slot));
      events.push([Math.round(a + i * slot + Math.max(0, (slot - l) * 0.4)), l, value]);
    }
  };
  const big = spec.segs.reduce((m, s) => (s[1] - s[0] > m[1] - m[0] ? s : m), spec.segs[0]);
  const len = Math.max(spec.dur, spec.ms);
  const bigFree = freeRuns(spec).reduce((m, s) => (s[1] - s[0] > m[1] - m[0] ? s : m), [0, -1]);
  if (spec.mode === "volume") {
    const n = spec.acc;
    if (type === "met") place(big[0], big[1], n, 1, L.plateau);
    else if (type === "outside") place(bigFree[0] + 4, bigFree[1] - 4, Math.min(n, bigFree[1] - bigFree[0] - 7), 1, L.plateau);
    else place(big[0], big[1], n, 1, L.plateau * 0.4);
  } else if (type === "met") place(big[0], big[1], len, spec.epy, L.plateau);
  else if (type === "short") place(big[0], big[1], Math.max(1, Math.floor(Math.min(spec.ms, spec.dur) / 2)), 1, L.plateau);
  else if (type === "outside") place(bigFree[0] + 4, bigFree[1] - 4, Math.min(len, bigFree[1] - bigFree[0] - 7), 1, L.plateau);
  else if (type === "over") place(big[0], big[1], len, 1, spec.mode === "level" ? spec.hi + 0.6 : spec.hi * 1.5);
  else place(big[0], big[1], len, 1, spec.mode === "level" ? spec.lo - 0.35 : spec.mode === "dry" ? spec.hi * 2.2 : spec.lo * 0.8);   // low
  const edge = spec.mode === "dry" ? spec.hi * 1.15 : spec.mode === "level" ? spec.lo - 0.1 : spec.mode === "volume" ? 0 : spec.lo * 0.92;
  for (const [s, l, v] of events) {
    const target = type === "low" || type === "over" || spec.mode === "volume" ? v : edge;
    for (let k = 1; k <= 3 && type !== "over"; k++) {   // (no ramp for "over": it would cross the range on the way up) three-day ramps that stay outside the zone, so a qualifying event is exactly l days long
      const t = k / 4, iu = s - 4 + k, id = s + l - 1 + k;
      if (iu >= 0 && iu < WY_DAYS) flow[iu] += (target - flow[iu]) * t;
      if (id >= 0 && id < WY_DAYS) flow[id] += (target - flow[id]) * (1 - t);
    }
    for (let d = s; d < s + l && d < WY_DAYS; d++) {
      const wig = spec.mode === "level" ? 0.04 * Math.sin(d / 5) : v * 0.03 * Math.sin(d / 5);
      let val = v + wig;
      if (type === "met" || type === "short" || type === "outside") val = clampZone(spec, val);
      flow[d] = val;
    }
  }
  return flow;
}

/** Keep a generated value just inside the thresholds; leave volume values unchanged. */
function clampZone(spec, v) {
  if (spec.mode === "volume") return v;
  const eps = spec.mode === "level" ? 0.02 : Math.max(spec.lo, spec.hi || spec.lo) * 0.01;
  return Math.max(spec.lo + eps, spec.hi != null ? Math.min(spec.hi - eps, v) : v);
}

/** Runs of consecutive days in the zone inside the EWR window: [[start, length], ...]. */
function zoneRuns(spec, flow) {
  const runs = [];
  let s = -1;
  for (let d = 0; d <= WY_DAYS; d++) {
    const on = d < WY_DAYS && inWindow(spec, d) && inZone(spec, flow[d]);
    if (on && s < 0) s = d;
    if (!on && s >= 0) { runs.push([s, d - s]); s = -1; }
  }
  return runs;
}

/** Does this year meet the EWR? Also returns the days counted and (volume) the best accumulated volume. */
function yearResult(spec, flow) {
  if (spec.mode === "volume") {
    let best = 0, at = 0;
    for (const [a, b] of spec.segs) {
      let sum = 0;
      for (let d = a; d <= b; d++) {
        sum += flow[d];
        if (d - a >= spec.acc) sum -= flow[d - spec.acc];
        if (d - a >= spec.acc - 1 && sum > best) { best = sum; at = d - spec.acc + 1; }
      }
    }
    return { met: best >= spec.vol, days: spec.acc, volume: best, at };
  }
  const runs = zoneRuns(spec, flow), good = runs.filter(([, l]) => l >= spec.ms);
  const days = good.reduce((n, [, l]) => n + l, 0), all = runs.reduce((n, [, l]) => n + l, 0);
  return { met: good.length >= spec.epy && days >= spec.dur * spec.epy, days, all, runs, good };
}

const RECORD_PATTERN = ["met", "met", "short", "met", "low", "outside", "met", "met", "over", "met"];   // 6 of 10 years, longest gap 3 years

/** The illustrative 10-year record plus a met example year and a missed example year, all from the EWR's own numbers. */
function buildTutorial(spec) {
  if (spec.mode === "other") return { spec, drawable: false, record: RECORD_PATTERN.map((t, i) => ({ type: t, met: t === "met", i })) };
  const make = (type, seed) => {
    let t = type;
    if (t !== "met" && !missFeasible(spec, t)) t = ["short", "low"].find(x => missFeasible(spec, x));
    const flow = genYear(spec, t, seed);
    return { type: t, flow, res: yearResult(spec, flow), met: yearResult(spec, flow).met };
  };
  const record = RECORD_PATTERN.map((t, i) => ({ ...make(t, i + 1), i }));
  const metEx = record.find(y => y.met), missEx = record.find(y => !y.met && y.type !== "outside") || record.find(y => !y.met);
  const met = record.filter(y => y.met).map(y => y.i);
  let gap = 0;
  for (let k = 1; k < met.length; k++) gap = Math.max(gap, met[k] - met[k - 1]);
  const n = met.length;
  return { spec, drawable: true, record, metEx, missEx, n, pct: n * 10, gap, L: levels(spec), fr: spec.tf ? Math.min(1, n * 10 / spec.tf) : null, frRaw: spec.tf ? n * 10 / spec.tf : null };
}

// ---- part 2: the panel and the animations ------------------------------------------------------------------------------------------
if (typeof document !== "undefined" && typeof EWRIDX !== "undefined") (function () {
  const C = { acc: "#2171B5", ok: "#229954", bad: "#C0392B", okd: "#145A32", text: "#2A3F5F", mut: "#8A96A3", org: "#E67E22" };
  const FONT = "Verdana, system-ui, sans-serif";
  const ROWS_ALL = ewrRows(EWRIDX), BY_KEY = new Map(ROWS_ALL.map(r => [r.key, r]));
  const fmt = v => (v == null ? "–" : (Math.abs(v) >= 100 ? Math.round(v) : +v.toFixed(2)).toLocaleString("en-AU"));
  const dm = (m, d) => `${d} ${MON3[m - 1]}`;
  const reduced = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ease = p => p * p * (3 - 2 * p);
  const clamp01 = x => Math.max(0, Math.min(1, x));
  const sub = (a, b, p) => clamp01((p - a) / (b - a));   // progress of a sub-interval
  const W = 520, H = 300, X0 = 66, X1 = 508, Y0 = 40, Y1 = 206;
  const T = (x, y, s, o = {}) => `<text x="${x}" y="${y}" font-size="${+((o.size || 12) * 1.12).toFixed(1)}" font-weight="${o.w || 400}" fill="${o.fill || C.text}" text-anchor="${o.a || "start"}" opacity="${o.op ?? 1}">${esc(s)}</text>`;

  /** Describe the EWR window as all year or a start and end calendar date. */
  function windowText(sp) { return sp.whole ? "all year" : `${dm(sp.sm, sp.sd)} – ${dm(sp.em, sp.ed)}`; }
  /** Describe the threshold condition and units for the tutorial rule. */
  function ruleText(sp) {
    if (sp.mode === "other") return "the flow conditions set out in the parameter sheet";
    if (sp.mode === "volume") return `${fmt(sp.vol)} ML of flow accumulated within ${sp.acc} days`;
    if (sp.mode === "level") return sp.hi != null ? `water level between ${fmt(sp.lo)} and ${fmt(sp.hi)} m` : `water level of ${fmt(sp.lo)} m or higher`;
    if (sp.mode === "dry") return `flow of no more than ${fmt(sp.hi)} ML/d`;
    return sp.hi != null ? `flow between ${fmt(sp.lo)} and ${fmt(sp.hi)} ML/d` : `flow of at least ${fmt(sp.lo)} ML/d`;
  }
  /** Describe duration, event count, minimum spell, and allowed gaps for non-volume rules. */
  function durText(sp) {
    if (sp.mode === "volume") return "";
    const per = sp.epyReq > 1 ? ` each time, ${sp.epyReq} times a year${sp.epy < sp.epyReq ? " (the chart has room for " + sp.epy + ")" : ""}` : "";
    const ms = sp.ms < sp.dur ? (sp.ms <= 1 ? ", not necessarily in a row" : `, in spells of at least ${sp.ms} days`) : "";
    return `for ${sp.dur} day${sp.dur === 1 ? "" : "s"}${per}${ms}${sp.gap > 0 ? ` (up to ${sp.gap} days out of range allowed within an event)` : ""}`;
  }
  const yrs = v => (v < 1 ? `${Math.max(1, Math.round(v * 12))} month${Math.round(v * 12) === 1 ? "" : "s"}` : `${fmt(v)} year${v === 1 ? "" : "s"}`);   // MaxInter-event is in years, sometimes under one
  const targetText = sp => (sp.tf != null ? `in ${fmt(sp.tf)}% of years` : "in some of the years");

  /** Choose readable, evenly spaced axis ticks between the supplied limits. */
  function ticks(lo, hi, n = 4) {
    const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw)), step = [1, 2, 2.5, 5, 10].map(x => x * mag).find(s => s >= raw) || mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
    return out;
  }

  /** The year chart. o: line (0..1 how much of the hydrograph is drawn), thr, win, hi (0..1 highlighted days), hiCol, dim */
  function yearSvg(m, y, o) {
    const sp = m.spec, L = m.L, X = d => X0 + d / (WY_DAYS - 1) * (X1 - X0), Y = v => Y1 - (v - L.ymin) / (L.ymax - L.ymin) * (Y1 - Y0);
    let s = "";
    const wa = o.win ?? 0;
    if (wa) for (const [a, b] of sp.segs) s += `<rect x="${X(a)}" y="${Y0 - 8}" width="${X(b + 1) - X(a)}" height="${Y1 - Y0 + 8}" fill="${C.acc}" opacity="${0.1 * wa}"/>`;
    for (const t of ticks(L.ymin, L.ymax)) s += `<line x1="${X0}" x2="${X1}" y1="${Y(t)}" y2="${Y(t)}" stroke="#e5e9ef"/>` + T(X0 - 6, Y(t) + 4, fmt(t), { size: 10, a: "end", fill: C.mut });
    s += `<line x1="${X0}" x2="${X1}" y1="${Y1}" y2="${Y1}" stroke="${C.mut}"/><line x1="${X0}" x2="${X0}" y1="${Y0 - 8}" y2="${Y1}" stroke="${C.mut}"/>`;
    let d0 = 0;
    for (const mo of WY_MONTHS) { s += T(X(d0 + MONTH_LEN[mo] / 2), Y1 + 14, MON3[mo - 1], { size: 9.5, a: "middle", fill: C.mut }); d0 += MONTH_LEN[mo]; }
    s += T(11, (Y0 + Y1) / 2, sp.mode === "level" ? "Water level (m)" : "Flow (ML/d)", { size: 10, a: "middle", fill: C.mut }).replace("<text ", `<text transform="rotate(-90 11 ${(Y0 + Y1) / 2})" `);
    if (wa) s += T((X(sp.segs[0][0]) + X(sp.segs[0][1] + 1)) / 2, Y0 - 12, `EWR window: ${windowText(sp)}`, { size: 10.5, a: "middle", w: 600, fill: C.acc, op: wa });
    const ta = o.thr ?? 0;
    if (ta && sp.mode !== "volume") {
      const lines = sp.mode === "dry" ? [[sp.hi, `≤ ${fmt(sp.hi)}`]] : [[sp.lo, `≥ ${fmt(sp.lo)}`], ...(sp.hi != null ? [[sp.hi, `≤ ${fmt(sp.hi)}`]] : [])];
      if (sp.mode === "dry") s += `<rect x="${X0}" width="${X1 - X0}" y="${Y(sp.hi)}" height="${Y1 - Y(sp.hi)}" fill="${C.org}" opacity="${0.1 * ta}"/>`;
      else if (sp.hi != null) s += `<rect x="${X0}" width="${X1 - X0}" y="${Y(Math.min(sp.hi, L.ymax))}" height="${Y(sp.lo) - Y(Math.min(sp.hi, L.ymax))}" fill="${C.org}" opacity="${0.1 * ta}"/>`;
      for (const [v, lab] of lines) {
        const yy = Y(v), xe = X0 + (X1 - X0) * ease(Math.min(1, ta * 1.4));
        s += `<line x1="${X0}" x2="${xe}" y1="${yy}" y2="${yy}" stroke="${C.org}" stroke-width="1.8" stroke-dasharray="6 4"/>` + T(X1 - 2, yy - 5, `${lab} ${sp.unit}`, { size: 11, a: "end", w: 700, fill: C.org, op: ta });
      }
    }
    const nDraw = Math.max(1, Math.floor((o.line ?? 1) * (WY_DAYS - 1)) + 1);
    let path = "";
    for (let d = 0; d < nDraw; d++) path += (d ? "L" : "M") + X(d).toFixed(1) + " " + Y(y.flow[d]).toFixed(1);
    s += `<path d="${path}" fill="none" stroke="${C.acc}" stroke-width="1.7" stroke-linejoin="round" opacity="${o.dim ?? 1}"/>`;
    const hl = o.hl;
    if (hl && hl.days.length) {   // highlighted days in colour: a thick line over the hydrograph
      const upto = Math.ceil(hl.p * hl.days.length);
      let seg = "", prev = -9;
      hl.days.slice(0, upto).forEach(d => { seg += (d === prev + 1 ? "L" : "M") + X(d).toFixed(1) + " " + Y(y.flow[d]).toFixed(1); prev = d; });
      s += `<path d="${seg}" fill="none" stroke="${hl.col}" stroke-width="4.2" stroke-linecap="round" stroke-linejoin="round"/>`;
      if (hl.days.length === 1 || upto === 1) s += `<circle cx="${X(hl.days[0])}" cy="${Y(y.flow[hl.days[0]])}" r="4.5" fill="${hl.col}"/>`;
    }
    if (sp.mode === "volume" && o.vol) {   // accumulating volume shading
      const r = y.res, upto = Math.floor(o.vol * sp.acc);
      let a = `M${X(r.at)} ${Y1}`;
      for (let d = r.at; d < r.at + upto; d++) a += `L${X(d)} ${Y(y.flow[d])}`;
      a += `L${X(r.at + Math.max(upto - 1, 0))} ${Y1}Z`;
      s += `<path d="${a}" fill="${C.ok}" opacity="0.35"/>`;
    }
    return s;
  }

  /** The strip under the chart: a progress bar and the verdict. */
  function barSvg(label, frac, col, note, badge) {
    let s = T(X0, 252, label, { size: 12.5, w: 700 });
    s += `<rect x="${X0}" y="262" width="200" height="12" rx="6" fill="#e9edf2"/><rect x="${X0}" y="262" width="${200 * clamp01(frac)}" height="12" rx="6" fill="${col}"/>`;
    s += T(X0 + 208, 272, note, { size: 9.5, fill: C.mut });
    if (badge) s += `<g opacity="${badge.op}"><rect x="${X1 - 126}" y="246" width="126" height="30" rx="8" fill="${badge.col}"/>` + T(X1 - 63, 266, badge.text, { size: 13, w: 700, a: "middle", fill: "#fff" }) + "</g>";
    return s;
  }

  /** Return the animated ten-year outcome strip and its tile layout coordinates. */
  function tilesSvg(m, p, mark) {   // the 10-year strip
    let s = "";
    const tw = 42, gx = 5, x0 = (W - (10 * tw + 9 * gx)) / 2;
    m.record.forEach((y, i) => {
      const a = sub(i / 12, i / 12 + 0.2, p), x = x0 + i * (tw + gx);
      if (a <= 0) return;
      const col = y.met ? C.ok : C.bad;
      s += `<g opacity="${a}"><rect x="${x}" y="${60 - (1 - a) * 8}" width="${tw}" height="58" rx="8" fill="${col}" opacity="0.14" stroke="${col}"/>` +
        T(x + tw / 2, 96 - (1 - a) * 8, y.met ? "✓" : "✗", { size: 22, w: 700, a: "middle", fill: col }) + T(x + tw / 2, 138, `Yr ${i + 1}`, { size: 9, a: "middle", fill: C.mut }) + "</g>";
    });
    return { svg: s, x0, tw, gx };
  }

  /** Build tutorial steps with captions, durations, and SVG drawing callbacks. */
  function stages(m) {
    const sp = m.spec;
    const common = [];
    const intro = {
      title: "What this EWR is", ms: 3200,
      cap: `An <b>environmental water requirement</b> (EWR) says how much water a river site needs, when, and how often. <b>${esc(m.row.c)}</b>${regimeName(m.row.c) ? ` is a <b>${regimeName(m.row.c).toLowerCase()}</b> requirement` : ""} at gauge ${esc(m.row.g)}${m.row.gn ? ` (${esc(m.row.gn)})` : ""}, planning unit ${esc(m.row.pu)}.`,
      draw: p => {
        let s = T(W / 2, 54, m.row.c, { size: 34, w: 700, a: "middle", fill: C.acc, op: ease(sub(0, 0.3, p)) });
        const lines = [[`Gauge ${m.row.g}${m.row.gn ? " · " + m.row.gn : ""}`, 0.2], [`${m.row.pu} · ${m.row.st}`, 0.3], [`Needs ${ruleText(sp)}`, 0.45], [sp.mode === "volume" ? `Window: ${windowText(sp)}` : `${durText(sp)} · window: ${windowText(sp)}`, 0.6], [`Target: ${targetText(sp)}${sp.mi != null ? ` · at most ${yrs(sp.mi)} between events` : ""}`, 0.75]];
        lines.forEach(([t, a], i) => { s += T(W / 2, 100 + i * 28, t, { size: t.length > 52 ? 10.5 : i >= 2 ? 13 : 12, w: i >= 2 ? 700 : 400, a: "middle", op: ease(sub(a, a + 0.2, p)), fill: i >= 2 ? C.text : C.mut }); });
        const obj = (m.row.eo || "").split("+").filter(Boolean).slice(0, 8);
        if (obj.length) {
          s += T(W / 2, 262, "Supports objectives", { size: 10.5, a: "middle", fill: C.mut, op: ease(sub(0.8, 1, p)) });
          const cw = 44, x0 = (W - obj.length * (cw + 4)) / 2;
          obj.forEach((o, i) => { s += `<g opacity="${ease(sub(0.8, 1, p))}"><rect x="${x0 + i * (cw + 4)}" y="270" width="${cw}" height="18" rx="9" fill="${C.acc}" opacity="0.12"/>` + T(x0 + i * (cw + 4) + cw / 2, 283, o, { size: 10.5, a: "middle", w: 600, fill: C.acc }) + "</g>"; });
        }
        return s;
      },
    };
    if (!m.drawable) {
      common.push(intro);
    } else {
      const ex = m.metEx, bad = m.missEx;
      common.push(intro,
        {
          title: sp.mode === "volume" ? "Flow through the year" : sp.mode === "level" ? "The level threshold" : "The flow threshold", ms: 3600,
          cap: sp.mode === "volume" ? `Here is one year of daily flow at the gauge. This EWR is about the <b>total volume</b> of water, not a single flow rate.` : `Here is one year of daily ${sp.mode === "level" ? "water level" : "flow"} at the gauge (an illustrative record, not real data). The EWR needs <b>${esc(ruleText(sp))}</b>${sp.mode === "dry" ? " (the river nearly stops flowing)" : ""}.`,
          draw: p => yearSvg(m, ex, { line: ease(sub(0, 0.6, p)), thr: ease(sub(0.55, 0.95, p)) }),
        },
        {
          title: "The EWR window", ms: 2800,
          cap: sp.whole ? "The requirement can be met at any time of the <b>July–June year</b>, so every day counts." : `Only <b>${esc(windowText(sp))}</b> counts. Water outside this window does not help meet the EWR.`,
          draw: p => yearSvg(m, ex, { thr: 1, win: ease(sub(0, 0.5, p)) }),
        },
        {
          title: sp.mode === "volume" ? "Volume" : "Duration", ms: 4600,
          cap: sp.mode === "volume" ? `Within any ${sp.acc}-day period of the window, the flow must add up to <b>${fmt(sp.vol)} ML</b>. The green area is the water counted.` : `Meeting the threshold is not enough: it must last <b>${esc(durText(sp))}</b>. The green line counts the days inside the window.`,
          draw: p => {
            const q = ease(sub(0.1, 0.9, p)), r = ex.res;
            if (sp.mode === "volume") {
              const got = r.volume * q;
              return yearSvg(m, ex, { win: 1, hl: null, vol: q }) + barSvg(`Accumulated: ${fmt(got)} ML of ${fmt(sp.vol)} ML`, got / sp.vol, C.ok, `within ${sp.acc} days`, p > 0.9 ? { col: C.ok, text: "Year met ✓", op: sub(0.9, 1, p) } : null);
            }
            const days = r.good.flatMap(([s, l]) => Array.from({ length: l }, (_, k) => s + k));
            const shown = Math.floor(q * days.length), need = sp.dur * sp.epy;
            return yearSvg(m, ex, { thr: 1, win: 1, hl: { days, p: q, col: C.ok } }) + barSvg(`Days in range: ${Math.min(shown, need)} of ${need}`, Math.min(shown, need) / need, C.ok, sp.epy > 1 ? `${sp.epy} events needed` : "", p > 0.92 ? { col: C.ok, text: "Year met ✓", op: sub(0.92, 1, p) } : null);
          },
        },
        {
          title: "A year that misses", ms: 4600,
          cap: missText(m, bad),
          draw: p => {
            const q = ease(sub(0.15, 0.9, p)), r = bad.res;
            const dim = { line: ease(sub(0, 0.35, p)) };
            if (sp.mode === "volume") {
              const got = r.volume * q;
              return yearSvg(m, bad, { win: 1, ...dim, vol: bad.type === "outside" ? 0 : q }) + barSvg(`Accumulated: ${fmt(got)} ML of ${fmt(sp.vol)} ML`, got / sp.vol, C.bad, `within ${sp.acc} days`, p > 0.92 ? { col: C.bad, text: "Year missed ✗", op: sub(0.92, 1, p) } : null);
            }
            const days = (r.runs || []).flatMap(([s, l]) => Array.from({ length: l }, (_, k) => s + k));
            const outside = [];
            if (bad.type === "outside") for (let d = 0; d < WY_DAYS; d++) if (!inWindow(sp, d) && inZone(sp, bad.flow[d])) outside.push(d);
            const need = sp.dur * sp.epy, shown = Math.floor(q * days.length);
            return yearSvg(m, bad, { thr: 1, win: 1, ...dim, hl: { days: days.length ? days : outside, p: days.length ? q : q, col: days.length ? C.org : C.mut } }) +
              barSvg(`Days in range: ${Math.min(shown, need)} of ${need}`, Math.min(shown, need) / need, C.bad, bad.type === "outside" ? "none in window" : "", p > 0.92 ? { col: C.bad, text: "Year missed ✗", op: sub(0.92, 1, p) } : null);
          },
        });
    }
    if (!m.drawable) m.record.forEach(y => { y.met = y.type === "met"; });
    common.push(
      {
        title: "Over the whole record", ms: 5200,
        cap: `Each year is checked the same way. Across ten example years this EWR is met in <b>${m.n ?? 6} of 10</b>, or <b>${m.pct ?? 60}%</b> of years${sp.tf != null ? `, against a target of <b>${fmt(sp.tf)}%</b>` : ""}.`,
        draw: p => {
          const t = tilesSvg(m, sub(0, 0.7, p));
          const a = ease(sub(0.7, 1, p));
          const n = m.n ?? 6, pct = m.pct ?? 60;
          let s = t.svg + `<g opacity="${a}">`;
          s += `<line x1="${X0 - 18}" x2="${X1 + 18}" y1="165" y2="165" stroke="${C.mut}"/>`;
          const cols = [[X0 + 55, "Years met", `${pct}%`, pct >= (sp.tf ?? 0) ? C.ok : C.bad], [X0 + 225, "Target", sp.tf != null ? `${fmt(sp.tf)}%` : "–", C.text], [X0 + 395, "Frequency ratio", m.frRaw != null ? m.frRaw.toFixed(2) : "–", m.frRaw != null && m.frRaw >= 1 ? C.ok : C.bad]];
          cols.forEach(([x, h, v, c]) => { s += T(x, 195, h, { size: 12, w: 700, a: "middle" }) + T(x, 235, v, { size: 30, w: 700, a: "middle", fill: c }); });
          if (m.frRaw != null) s += T(W / 2, 276, m.frRaw >= 1 ? "At or above target: counted as 1.00 (capped)" : `Below target: ${n} ÷ ${fmt(sp.tf / 10)} years = ${m.frRaw.toFixed(2)}`, { size: 11, a: "middle", fill: C.mut });
          return s + "</g>";
        },
      },
      {
        title: "Gaps between events", ms: 4200,
        cap: sp.mi != null ? `The EWR also limits how long the river can go between successful years: <b>${yrs(sp.mi)}</b> at most. In this record the longest gap is <b>${m.gap ?? 3} years</b>${(m.gap ?? 3) > sp.mi ? ", which is too long" : ", which is within the limit"}.` : `This EWR has no maximum gap between events set, but the longer a river goes without a successful year, the more the ecology suffers.`,
        draw: p => {
          const t = tilesSvg(m, 1), a = ease(sub(0.2, 0.8, p));
          const idx = m.record.map((y, i) => (y.met ? i : -1)).filter(i => i >= 0);
          let best = [0, 0];
          for (let k = 1; k < idx.length; k++) if (idx[k] - idx[k - 1] > best[1] - best[0]) best = [idx[k - 1], idx[k]];
          const xa = t.x0 + best[0] * (t.tw + t.gx) + t.tw / 2, xb = t.x0 + best[1] * (t.tw + t.gx) + t.tw / 2, over = sp.mi != null && best[1] - best[0] > sp.mi;
          const col = sp.mi == null ? C.org : over ? C.bad : C.ok;
          let s = t.svg + `<g opacity="${a}"><path d="M${xa} 150 V168 H${xb} V150" fill="none" stroke="${col}" stroke-width="2.5"/>` + T((xa + xb) / 2, 192, `${best[1] - best[0]} years between successful years`, { size: 13, w: 700, a: "middle", fill: col });
          if (sp.mi != null) s += T((xa + xb) / 2, 216, `limit: ${yrs(sp.mi)} → ${over ? "exceeded ✗" : "within limit ✓"}`, { size: 12, a: "middle", fill: col });
          return s + "</g>";
        },
      },
      {
        title: "Putting it together", ms: 3800,
        cap: `<b>${esc(m.row.c)}</b> asks for ${esc(ruleText(sp))}${sp.mode === "other" || sp.mode === "volume" ? "" : " " + esc(durText(sp))}, ${sp.whole ? "at any time of year" : "between " + esc(windowText(sp))}, ${esc(targetText(sp))}${sp.mi != null ? `, with no more than ${yrs(sp.mi)} between events` : ""}. The dashboard scores how close the modelled flows come to that.`,
        draw: p => {
          const items = [["1", "Flow", ruleText(sp)], ["2", "Window", windowText(sp)], ["3", sp.mode === "volume" ? "Volume" : "Duration", sp.mode === "volume" ? `within ${sp.acc} days` : durText(sp).replace(/^for /, "")], ["4", "Frequency", sp.tf != null ? `${fmt(sp.tf)}% of years` : "–"], ["5", "Gap", sp.mi != null ? `≤ ${yrs(sp.mi)}` : "–"]];
          return items.map(([n, h, v], i) => {
            const a = ease(sub(i * 0.15, i * 0.15 + 0.3, p)), y = 56 + i * 44;
            return `<g opacity="${a}">` + T(60, y + 4, n, { size: 22, w: 700, fill: C.acc }) + T(94, y - 2, h, { size: 11, fill: C.mut }) + T(94, y + 16, v.length > 70 ? v.slice(0, 68) + "…" : v, { size: 14, w: 700 }) + "</g>";
          }).join("");
        },
      });
    return common;
  }

  /** Explain why the example year misses the rule, using its evaluated result. */
  function missText(m, bad) {
    const sp = m.spec, r = bad.res;
    const text = {
      short: () => `The flow reaches the threshold, but for only <b>${r.all ?? 0} day${r.all === 1 ? "" : "s"}</b> – it was needed for <b>${sp.dur}</b>. Not enough.`,
      outside: () => `Flow is right and lasts long enough, but <b>outside the EWR window</b> (${esc(windowText(sp))}), so it does not count.`,
      over: () => `Flow rises <b>above the upper limit</b> of ${fmt(sp.hi)} ${sp.unit}, outside the range the EWR wants, so those days do not count.`,
      low: () => sp.mode === "volume" ? `Only about <b>${fmt(r.volume)} ML</b> builds up – short of the <b>${fmt(sp.vol)} ML</b> needed.`
        : sp.mode === "dry" ? `The river never drops low enough: flow stays above <b>${fmt(sp.hi)} ML/d</b>, so the EWR is not met.`
        : `Flow peaks <b>below the threshold</b> of ${fmt(sp.lo)} ${sp.unit}, so no day counts.`,
    };
    return "Now a year with a different outcome. " + (text[bad.type] || text.low)();
  }

  if (typeof window !== "undefined") window.ewrGuideApi = { stages, rows: ROWS_ALL };   // for tests/test_ewr_guide.js

  // ---- the panel ----
  const lp = document.getElementById("lpanel");
  if (!lp) return;
  const store = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  const load = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v ?? d; } catch (e) { return d; } };
  let sel = load("ewrGuideSel", []).filter(k => BY_KEY.has(k)).slice(0, GUIDE_MAX);
  const opts = (vals, all) => `<option value="">${all}</option>` + vals.map(v => `<option>${esc(v)}</option>`).join("");
  const uniq = f => [...new Set(ROWS_ALL.map(f).filter(Boolean))].sort();
  lp.innerHTML = `<h2>EWR guide</h2><div class="crumbs">Search the EWR parameter sheet (${ROWS_ALL.length.toLocaleString()} rows) and pick up to ${GUIDE_MAX} to see how each one works.</div>
    <div class="gq"><input id="gqText" type="search" placeholder="Search code, gauge, name, planning unit, objective…" aria-label="Search EWRs">
    <div class="gqrow"><select id="gqState" aria-label="State">${opts(uniq(r => r.st), "Any state")}</select>
    <select id="gqPu" aria-label="Planning unit">${opts(uniq(r => r.pu), "Any planning unit")}</select>
    <select id="gqKind" aria-label="Type"><option value="">Flow, level or volume</option><option value="F">Flow</option><option value="L">Level</option><option value="V">Volume</option></select></div>
    <div class="gqrow"><input id="gqGauge" type="search" placeholder="Gauge (ID or name)" aria-label="Gauge"><input id="gqCode" type="search" placeholder="EWR code" aria-label="EWR code"></div></div>
    <div id="gqMsg" class="gmsg" role="status"></div><div id="gqList" class="glist"></div>
    <div class="gsel"><div class="gselh"><b id="gselN"></b><button id="gselClear" class="ghost">Clear all</button></div><div id="gCards"></div></div>`;
  const $g = id => document.getElementById(id);
  const query = () => ({ text: $g("gqText").value, state: $g("gqState").value, pu: $g("gqPu").value, kind: $g("gqKind").value, gauge: $g("gqGauge").value, code: $g("gqCode").value });
  let notice = "";

  /** Render the first 60 search matches and update the selection controls and status. */
  function renderList() {
    const res = searchEwrs(ROWS_ALL, query()), shown = res.slice(0, 60);
    $g("gqMsg").textContent = notice || `${res.length.toLocaleString()} EWR${res.length === 1 ? "" : "s"} match${res.length > 60 ? " – showing the first 60, refine the search" : ""}`;
    $g("gqList").innerHTML = shown.map(r => {
      const on = sel.includes(r.key);
      return `<div class="gitem${on ? " on" : ""}"><div class="gtxt"><b>${esc(r.c)}</b> · ${esc(r.g)}${r.gn ? " · " + esc(r.gn) : ""}<br><span>${esc(r.pu)} · ${esc(r.st)}${regimeName(r.c) ? " · " + regimeName(r.c) : ""}${ewrSupported(r) ? "" : " · demo in development"}</span></div><button class="gadd" data-key="${esc(r.key)}" aria-pressed="${on}"${!on && sel.length >= GUIDE_MAX ? " disabled" : ""}>${on ? "✓ Added" : "＋ Add"}</button></div>`;
    }).join("");
  }
  const tutorials = new Map();   // key -> {card, ctl}
  /** Reconcile tutorial cards with the selection, stopping and removing deselected cards. */
  function renderSel() {
    $g("gselN").textContent = `Selected EWRs (${sel.length} of ${GUIDE_MAX})`;
    $g("gselClear").style.display = sel.length ? "" : "none";
    for (const [k, t] of tutorials) if (!sel.includes(k)) { t.ctl.stop(); t.card.remove(); tutorials.delete(k); }
    sel.forEach((k, i) => {
      if (!tutorials.has(k)) tutorials.set(k, makeCard(BY_KEY.get(k), i === sel.length - 1));
      const t = tutorials.get(k);
      if ($g("gCards").children[i] !== t.card) $g("gCards").insertBefore(t.card, $g("gCards").children[i] || null);
    });
    if (!sel.length && !$g("gCards").firstChild) $g("gCards").innerHTML = `<div class="gempty">Nothing selected yet.</div>`;
    else if (sel.length) $g("gCards").querySelector(".gempty")?.remove();
  }
  /** Persist the new selection and refresh the selected cards, results, and notice. */
  function setSel(next, note) { sel = next; notice = note || ""; store("ewrGuideSel", sel); renderSel(); renderList(); }

  /** Create an EWR card and its animation controller, or an unsupported-rule message. */
  function makeCard(row, open) {
    const card = document.createElement("details");
    card.className = "flowp ewrcard"; card.open = open;
    if (!ewrSupported(row)) {   // not a flow_handle / lowflow_handle EWR
      card.innerHTML = `<summary><span class="gtitle">${esc(ewrTitle(row))}</span><button class="ghost grm" title="Remove this EWR" aria-label="Remove ${esc(row.c)}">×</button></summary>
        <div class="gbody"><div class="gdev"><b>Demo still in development</b><br>${esc(row.c)} is calculated by py_ewr's <code>${esc(row.h || "unknown")}</code>; tutorials so far cover EWRs evaluated by flow_handle and lowflow_handle.</div></div>`;
      card.querySelector(".grm").addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); setSel(sel.filter(k => k !== row.key)); });
      return { card, ctl: { stop() {} } };
    }
    const m = { ...buildTutorial(ewrSpec(row)), row }, list = stages(m);
    card.innerHTML = `<summary><span class="gtitle">${esc(ewrTitle(row))}</span><button class="ghost grm" title="Remove this EWR" aria-label="Remove ${esc(row.c)}">×</button></summary>
      <div class="gbody"><div class="gstage"></div><div class="gcap" aria-live="polite"></div><svg class="gsvg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Tutorial animation"></svg>
      <div class="gctl"><button data-a="prev" aria-label="Previous step">◀</button><button data-a="play" aria-label="Play or pause">⏸</button><button data-a="next" aria-label="Next step">▶</button><button data-a="replay" aria-label="Replay from the start">↺ Replay</button><span class="gdots"></span></div>
      <div class="gnote">${m.drawable ? "Illustrative record generated from this EWR's parameters – not gauge data." : "No drawable threshold for this EWR; the steps below use its other parameters."}</div></div>`;
    const svg = card.querySelector(".gsvg"), cap = card.querySelector(".gcap"), stg = card.querySelector(".gstage"), playBtn = card.querySelector('[data-a="play"]'), dots = card.querySelector(".gdots");
    dots.innerHTML = list.map((s, i) => `<button data-i="${i}" aria-label="Step ${i + 1}: ${esc(s.title)}" title="${esc(s.title)}"></button>`).join("");
    const st = { i: 0, p: 0, playing: false, t0: 0, hold: 0 };
    const paint = () => {
      const s = list[st.i];
      stg.textContent = `Step ${st.i + 1} of ${list.length} · ${s.title}`;
      if (cap.dataset.i !== String(st.i)) { cap.innerHTML = s.cap; cap.dataset.i = st.i; }
      svg.innerHTML = s.draw(st.p);
      dots.querySelectorAll("button").forEach((b, k) => b.className = k === st.i ? "on" : k < st.i ? "done" : "");
      playBtn.textContent = st.playing ? "⏸" : "▶";
    };
    const ctl = {
      go(i, p = 0) { st.i = Math.max(0, Math.min(list.length - 1, i)); st.p = p; st.t0 = performance.now(); st.hold = 0; paint(); },
      play() { if (reduced()) { st.p = 1; paint(); return; } if (st.i === list.length - 1 && st.p >= 1) ctl.go(0); st.playing = true; st.t0 = performance.now() - st.p * list[st.i].ms; ACTIVE.add(ctl); kick(); paint(); },
      stop() { st.playing = false; ACTIVE.delete(ctl); paint(); },
      tick(now) {
        const s = list[st.i];
        if (st.hold) { if (now - st.hold > 1100) { st.hold = 0; if (st.i < list.length - 1) ctl.go(st.i + 1); else ctl.stop(); } return; }
        st.p = clamp01((now - st.t0) / s.ms);
        paint();
        if (st.p >= 1) st.hold = now;
      },
    };
    card.addEventListener("toggle", () => { if (card.open) { if (!st.played) { st.played = true; ctl.go(0); } ctl.play(); } else ctl.stop(); });
    card.querySelector(".gctl").addEventListener("click", e => {
      const b = e.target.closest("button"); if (!b) return;
      const a = b.dataset.a;
      if (a === "play") st.playing ? ctl.stop() : ctl.play();
      else if (a === "next") { ctl.go(st.i + 1, 1); if (st.playing) ctl.play(); }
      else if (a === "prev") { ctl.go(st.i - 1, 1); if (st.playing) ctl.play(); }
      else if (a === "replay") { ctl.go(0); ctl.play(); }
    });
    dots.addEventListener("click", e => { const b = e.target.closest("button[data-i]"); if (b) { const was = st.playing; ctl.go(+b.dataset.i, 1); if (was) ctl.play(); } });
    card.querySelector(".grm").addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); setSel(sel.filter(k => k !== row.key)); });
    st.played = false;
    ctl.go(0);
    if (open) setTimeout(() => { st.played = true; ctl.play(); }, 0);
    return { card, ctl };
  }

  const ACTIVE = new Set();
  let raf = 0;
  /** Schedule the shared animation loop when no frame is already pending. */
  function kick() { if (!raf) raf = requestAnimationFrame(loop); }
  /** Advance active tutorial controllers and schedule another frame while any remain active. */
  function loop(now) { raf = 0; for (const c of ACTIVE) c.tick(now); if (ACTIVE.size) kick(); }
  document.addEventListener("visibilitychange", () => { if (document.hidden) for (const c of [...ACTIVE]) c.stop(); });

  for (const id of ["gqText", "gqGauge", "gqCode"]) $g(id).addEventListener("input", () => { notice = ""; renderList(); });
  for (const id of ["gqState", "gqPu", "gqKind"]) $g(id).addEventListener("change", () => { notice = ""; renderList(); });
  $g("gqList").addEventListener("click", e => {
    const b = e.target.closest("button.gadd"); if (!b) return;
    const r = toggleSelection(sel, b.dataset.key);
    setSel(r.sel, r.full ? `You can pick up to ${GUIDE_MAX} EWRs. Remove one first.` : "");
  });
  $g("gselClear").addEventListener("click", () => setSel([]));
  renderList(); renderSel();
  window.ewrGuide = { stopAll: () => [...ACTIVE].forEach(c => c.stop()) };
})();
