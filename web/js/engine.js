// AquaSense in-browser engine.
// A JavaScript port of the Python prototype (aquasense/rigsim.py, features.py,
// engine.py cross-check logic, rul.py, pricing.py) so the demo runs with no
// server and no hardware. Signals are simulated; the logic is the same.

export const HEADER = ['timestamp', 'device_id', 'tds_in', 'tds_out', 'temp_c', 'pressure_bar', 'flow_lpm',
  'pump_current_a', 'pump_on', 'buildup_s', 'pump_runtime_s', 'pump_cycles', 'uv_hours', 'filter_hours', 'label'];

export const PUMP_ON_S = 90, PUMP_OFF_S = 30, LOG_S = 5, SYNC_S = 10, WINDOW_S = 60;
export const REPLACE_T = 40, ALERT_P = 0.7, ALERT_N = 3;

export const COMPONENTS = ['pump', 'membrane', 'filter', 'sensors'];
export const COMP_NAME = { pump: 'Booster pump', membrane: 'RO membrane', filter: 'Filter / line', sensors: 'Sensors' };
export const FAULT_NAME = {
  normal: 'Normal', clogging: 'Filter/line clogging', pump: 'Pump weakness',
  membrane: 'Membrane wear', sensor: 'Sensor fault suspected',
};
export const FAULT_COMP = { clogging: 'filter', pump: 'pump', membrane: 'membrane', sensor: 'sensors' };

// ------------------------------------------------------------------ random
export function rng(seed) {
  let s = seed >>> 0 || 1;
  const u = () => { s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0; return (s >>> 0) / 4294967296; };
  const normal = (m = 0, sd = 1) => {
    const a = Math.max(u(), 1e-12), b = u();
    return m + sd * Math.sqrt(-2 * Math.log(a)) * Math.cos(2 * Math.PI * b);
  };
  return { u, normal };
}
const clip = (v, a, b) => Math.max(a, Math.min(b, v));
const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : NaN);
const std = (a) => { if (a.length < 2) return 0; const m = mean(a); return Math.sqrt(mean(a.map((x) => (x - m) ** 2))); };

// ------------------------------------------------------------------ rig model
export const BASE = { p0: 5.5, q0: 0.6, i0: 0.95, rej0: 0.955, tau0: 1.1 };
const BASE_BUILDUP = -BASE.tau0 * Math.log(1 - 0.8);

export class Rig {
  constructor({ id = 'AQ-1001', seed = 42, start = Date.now(), faults = {}, counters = {}, calib = {} } = {}) {
    this.id = id;
    this.r = rng(seed);
    this.t = Math.floor(start / 1000) * 1000;
    this.f = { clog: 0, pump: 0, membrane: 0, sensor: null, tds: 300, ...faults };
    this.c = { ...BASE, temp0: 27, ...calib };
    this.k = { runtime: 36000, cycles: 420, uv: 10, filter: 10, ...counters };
    this.cycleT = 0;
    this.pressure = 0.2;
    this.buildup = PUMP_ON_S;
    this.sinceStart = 0;
    this.acc = [];
    this.accOn = null;
    this.sinceLog = 0;
    this.frozen = {};
    this.drift = 0;
    this.last = null;
  }

  steady() {
    const { c, f } = this;
    const p = c.p0 * (1 + 0.45 * f.clog) * (1 - 0.55 * f.pump) * (1 - 0.06 * f.membrane);
    const q = c.q0 * (1 - 0.7 * f.clog) * (1 - 0.45 * f.pump) * (1 - 0.1 * f.membrane);
    const i = c.i0 * (1 + 0.15 * f.clog) * (1 - 0.35 * f.pump);
    let rej = 1 - (1 - c.rej0) * Math.sqrt(c.p0 / Math.max(p, 0.5)) - 0.45 * f.membrane;
    const tau = c.tau0 * (1 + 5 * f.pump) * (1 - 0.2 * f.clog);
    return { p, q, i, rej: clip(rej, 0, 0.995), tau };
  }

  measureBuildup(pss, tau) {
    if (this.f.sensor === 'pressure_frozen') return 0.1;
    const target = 0.8 * this.c.p0;
    if (pss <= target * 1.01) return PUMP_ON_S;
    const t = -tau * Math.log(1 - target / pss);
    return clip(Math.round(t * 10) / 10 + this.r.normal(0, 0.1), 0.1, PUMP_ON_S);
  }

  second() {
    const { r, c, f } = this;
    const on = this.cycleT < PUMP_ON_S;
    const s = this.steady();
    this.drift = clip(this.drift + r.normal(0, 0.002), -0.05, 0.05);
    let pressure, flow, current;
    if (on) {
      if (this.cycleT === 0) { this.k.cycles++; this.sinceStart = 0; this.buildup = this.measureBuildup(s.p, s.tau); }
      this.sinceStart++;
      const ramp = 1 - Math.exp(-this.sinceStart / s.tau);
      this.pressure = s.p * ramp;
      const ripple = 1 + 2.5 * f.pump;
      pressure = this.pressure * (1 + this.drift) + r.normal(0, 0.07 * ripple);
      flow = s.q * Math.min(1, ramp * 1.05) * r.normal(1, 0.025) + r.normal(0, 0.008);
      current = s.i * (1 + 0.6 * Math.exp(-this.sinceStart / 1.5)) * (1 + r.normal(0, 0.025 * ripple));
      this.k.runtime++; this.k.uv += 1 / 3600; this.k.filter += 1 / 3600;
    } else {
      this.pressure = Math.max(0.15, this.pressure * 0.6);
      pressure = this.pressure + r.normal(0, 0.03);
      flow = Math.max(0, r.normal(0, 0.003));
      current = Math.abs(r.normal(0.01, 0.005));
    }
    const temp = c.temp0 + 0.4 * Math.sin(this.sinceStart / 600) + r.normal(0, 0.08);
    const tdsIn = f.tds * r.normal(1, 0.015);
    let tdsOut = f.tds * (1 - s.rej) * r.normal(1, 0.03) * (on ? 1 : 1.08);
    if (f.sensor === 'tds_out_unplugged') tdsOut = Math.abs(r.normal(0.6, 0.6));
    if (f.sensor === 'tds_out_frozen') tdsOut = this.frozen.tds ??= tdsOut; else delete this.frozen.tds;
    if (f.sensor === 'pressure_frozen') pressure = this.frozen.p ??= Math.max(pressure, 3.0); else delete this.frozen.p;
    this.cycleT = (this.cycleT + 1) % (PUMP_ON_S + PUMP_OFF_S);
    this.t += 1000;
    return { on: on ? 1 : 0, tds_in: Math.max(0, tdsIn), tds_out: Math.max(0, tdsOut), temp_c: temp,
      pressure_bar: Math.max(0, pressure), flow_lpm: Math.max(0, flow), pump_current_a: Math.max(0, current) };
  }

  flush() {
    if (!this.acc.length) return null;
    const a = this.acc, row = { timestamp: isoLocal(this.t), device_id: this.id };
    for (const k of ['tds_in', 'tds_out', 'temp_c', 'pressure_bar', 'flow_lpm', 'pump_current_a']) row[k] = mean(a.map((x) => x[k]));
    Object.assign(row, {
      pump_on: a[a.length - 1].on, buildup_s: this.buildup, pump_runtime_s: Math.floor(this.k.runtime),
      pump_cycles: this.k.cycles, uv_hours: this.k.uv, filter_hours: this.k.filter, label: '',
    });
    this.acc = []; this.sinceLog = 0;
    return roundRow(row);
  }

  step() {
    const s = this.second();
    let out = null;
    if (this.accOn !== null && s.on !== this.accOn) out = this.flush();
    this.acc.push(s); this.accOn = s.on; this.sinceLog++;
    if (this.sinceLog >= LOG_S) out = this.flush() ?? out;
    if (out) this.last = out;
    return out;
  }

  run(seconds) { const rows = []; for (let k = 0; k < seconds; k++) { const r = this.step(); if (r) rows.push(r); } return rows; }
}

export function isoLocal(ms) {
  const d = new Date(ms), p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function roundRow(r) {
  const nd = { tds_in: 1, tds_out: 1, temp_c: 2, pressure_bar: 3, flow_lpm: 3, pump_current_a: 3, buildup_s: 1, uv_hours: 3, filter_hours: 3 };
  for (const [k, d] of Object.entries(nd)) r[k] = Number(r[k].toFixed(d));
  return r;
}
export const csvLine = (r) => HEADER.map((k) => r[k]).join(',');

// ------------------------------------------------------------------ features (60 s window)
const tsOf = (r) => new Date(r.timestamp).getTime();

export function windowFeatures(rows, endMs) {
  const w = rows.filter((r) => { const t = tsOf(r); return t <= endMs && t > endMs - WINDOW_S * 3000; });
  // the first 10 s after a pump start are the transient
  const firstOn = {};
  for (const r of rows) if (r.pump_on && firstOn[r.pump_cycles] === undefined) firstOn[r.pump_cycles] = tsOf(r);
  for (const mult of [1, 2, 3]) {
    const win = w.filter((r) => tsOf(r) > endMs - WINDOW_S * 1000 * mult);
    const on = win.filter((r) => r.pump_on && tsOf(r) - firstOn[r.pump_cycles] >= 10000);
    if (on.length < 4) continue;
    const off = win.filter((r) => !r.pump_on);
    const transient = win.filter((r) => r.pump_on && tsOf(r) - firstOn[r.pump_cycles] < 10000);
    const col = (k) => on.map((r) => r[k]);
    const rej = on.map((r) => (1 - r.tds_out / Math.max(r.tds_in, 1)) * 100);
    return {
      p: mean(col('pressure_bar')), p_std: std(col('pressure_bar')),
      q: mean(col('flow_lpm')), q_std: std(col('flow_lpm')),
      i: mean(col('pump_current_a')), i_std: std(col('pump_current_a')),
      tds_in: mean(col('tds_in')), tds_out: mean(col('tds_out')), tds_out_std: std(col('tds_out')),
      rej: mean(rej), rej_std: std(rej), buildup: mean(col('buildup_s')),
      startup_i: transient.length ? mean(transient.map((r) => r.pump_current_a)) : NaN,
      p_off: off.length ? mean(off.map((r) => r.pressure_bar)) : NaN,
      q_off: off.length ? mean(off.map((r) => r.flow_lpm)) : NaN,
      temp: mean(col('temp_c')),
    };
  }
  return null;
}

// ------------------------------------------------------------------ diagnosis (cross-check, abstract Fig. 3)
export function diagnose(f) {
  const rp = f.p / BASE.p0, rq = f.q / BASE.q0, ri = f.i / BASE.i0;
  const drop = BASE.rej0 * 100 - f.rej;
  const ind = {
    pLow: rp < 0.9, pHigh: rp > 1.07, qLow: rq < 0.88, iAbn: Math.abs(ri - 1) > 0.07,
    buildSlow: f.buildup > BASE_BUILDUP + 2.5, rejLow: drop > 4, pNormal: Math.abs(rp - 1) < 0.05, qNormal: Math.abs(rq - 1) < 0.075,
  };
  const pct = (r) => `${Math.abs(r - 1) * 100 | 0}%`;
  const evidence = [];
  if (ind.pLow) evidence.push(`Pressure ${pct(rp)} below normal`);
  if (ind.pHigh) evidence.push(`Pressure ${pct(rp)} above normal`);
  if (ind.qLow) evidence.push(`Reject flow ${pct(rq)} below normal`);
  if (ind.iAbn) evidence.push(`Pump current ${pct(ri)} ${ri > 1 ? 'above' : 'below'} normal`);
  if (ind.buildSlow) evidence.push('Pressure build-up after pump start is slow');
  if (ind.rejLow) evidence.push(`Salt rejection down ${drop.toFixed(0)} points`);

  const sensor = [];
  if (f.tds_in > 100 && f.tds_out < 3) sensor.push('TDS-out reads about 0 ppm while TDS in is normal (probe disconnected?)');
  if (f.tds_out > f.tds_in * 1.05) sensor.push('TDS out is higher than TDS in');
  if (f.p_std < 1e-6 && f.p > 0.05) sensor.push(`Pressure reading is frozen at ${f.p.toFixed(2)} bar`);
  if (f.tds_out_std < 1e-6 && f.tds_out > 0.05) sensor.push(`TDS-out reading is frozen at ${f.tds_out.toFixed(1)} ppm`);
  if (!Number.isNaN(f.p_off) && f.p_off > Math.max(1.5, 0.7 * f.p)) sensor.push(`Pressure reads ${f.p_off.toFixed(1)} bar while the pump is off`);

  const sev = {
    clogging: clip(((rp - 1) / 0.45 + (1 - rq) / 0.7) / 2, 0, 1),
    pump: clip(((1 - rp) / 0.55 + (1 - rq) / 0.45) / 2, 0, 1),
    membrane: clip(drop / 45, 0, 1),
  };
  let fault = 'normal', conf = 0.9, reason = 'All signals agree. No fault suspected.', method = 'model';
  if (sensor.length) {
    fault = 'sensor'; conf = 0.88; method = 'cross-check';
    reason = `${sensor[0]}. Other signals look normal, so no component is blamed.`;
  } else if (ind.pLow && (ind.iAbn || ind.buildSlow)) {
    fault = 'pump'; conf = ind.iAbn && ind.buildSlow ? 0.98 : 0.84;
    reason = ind.iAbn && ind.buildSlow ? 'Low pressure, abnormal pump current and slow build-up all agree.'
      : `Low pressure with ${ind.iAbn ? 'abnormal pump current' : 'slow build-up'}.`;
  } else if (ind.qLow && ind.pHigh) {
    fault = 'clogging'; conf = clip(0.6 + sev.clogging * 1.2, 0.6, 0.98);
    reason = 'Low flow with high pressure: the line or filter is restricted.';
  } else if (ind.rejLow) {
    fault = 'membrane'; conf = clip(0.7 + sev.membrane, 0.7, 0.99);
    reason = `Salt rejection has fallen ${drop.toFixed(0)} points with plausible TDS readings.`;
  } else if (ind.pLow || (ind.qLow && ind.pNormal) || (ind.pHigh && ind.qNormal)) {
    fault = 'sensor'; conf = 0.7; method = 'cross-check';
    const which = ind.pLow || ind.pHigh ? 'Pressure' : 'Flow';
    reason = ind.pLow ? 'Pressure is low but pump current and build-up time are normal, so the pump is not blamed. Pressure sensor suspected.'
      : `${which} is abnormal but the related signal is normal. ${which} sensor suspected.`;
  }

  // confirmed signature -> full severity; unconfirmed drift -> a small dent only
  const w = (ok) => (ok ? 1 : 0.15);
  const health = {
    pump: 100 - 70 * sev.pump * w(ind.pLow && (ind.iAbn || ind.buildSlow)),
    filter: 100 - 70 * sev.clogging * w(ind.qLow && ind.pHigh),
    membrane: 100 - 70 * sev.membrane * w(ind.rejLow),
    sensors: fault === 'sensor' ? 30 : 99,
  };
  if (fault === 'sensor') for (const k of ['pump', 'filter', 'membrane']) health[k] = Math.max(health[k], 85);
  for (const k of COMPONENTS) health[k] = Math.round(clip(health[k], 0, 100) * 10) / 10;

  // signals that drove the decision (stand-in for the SHAP view of the Python engine)
  const drivers = {
    clogging: ['Pressure (level)', 'Reject flow (level)', 'Pressure per amp (level)'],
    pump: ['Build-up time', 'Pump current (level)', 'Pressure (level)'],
    membrane: ['Rejection % (level)', 'Rejection % (variability)', 'Reject flow (level)'],
    sensor: ['TDS out (noise)', 'Pressure (variability)', 'Pressure while pump off'],
    normal: ['Rejection % (level)', 'Pump current (level)', 'Pressure (level)'],
  }[fault];
  return { fault, fault_name: FAULT_NAME[fault], confidence: conf, reason, method,
    evidence: fault === 'sensor' && sensor.length ? sensor : evidence, health, drivers, features: f };
}

// ------------------------------------------------------------------ RUL + pricing
export function wearRate(comp, tds, usage) {
  const tk = Math.max(tds, 50) / 500, uk = usage / 2;
  if (comp === 'membrane') return 0.085 * tk ** 0.6 * uk;
  if (comp === 'filter') return 0.16 * tk ** 0.5 * uk;
  return 0.045 * uk ** 1.1;
}

export function rul(comp, h, tds, usage = 2, slope = null) {
  if (comp === 'sensors') return null;
  if (h < REPLACE_T) return { low: 0, mid: 0, high: 0 };
  const rate = slope !== null && slope < -0.01 ? -slope : wearRate(comp, tds, usage) * (1 + (100 - h) / 60);
  const mid = (h - REPLACE_T) / rate;
  return { low: Math.round(mid * 0.7), mid: Math.round(mid), high: Math.round(mid * 1.4) };
}

export function probWithin(r, days = 30) {
  if (!r) return 0;
  if (r.high <= 0) return 1;
  const xs = [Math.max(0, r.low - (r.mid - r.low) * 1.5), r.low, r.mid, r.high, r.high + (r.high - r.mid) * 1.5 + 1];
  const ys = [0, 0.05, 0.5, 0.95, 1];
  if (days <= xs[0]) return 0;
  for (let k = 1; k < xs.length; k++) if (days <= xs[k]) return ys[k - 1] + (ys[k] - ys[k - 1]) * (days - xs[k - 1]) / Math.max(xs[k] - xs[k - 1], 1e-6);
  return 1;
}

export const PARTS = {
  filter: ['Sediment/carbon filter', 400], membrane: ['RO membrane', 1800],
  pump: ['Booster pump', 1500], sensors: ['Sensor/electronics fix', 600],
};
export const LABOUR = 300, VISIT = 200;

export function quote(components) {
  const lines = components.map((c) => {
    const [part, cost] = PARTS[c.component];
    const p = clip(c.failure_prob, 0, 1);
    const decision = c.health < REPLACE_T || p > 0.6 ? 'replace' : p >= 0.2 ? 'watch' : 'ok';
    return { component: c.component, part, cost, health: c.health, failure_prob: p, decision, expected_cost: p * cost };
  });
  const replace = lines.filter((l) => l.decision === 'replace');
  const parts = replace.reduce((s, l) => s + l.cost, 0);
  const labour = replace.length ? LABOUR : 0;
  return { lines, replace_now: replace, watch_list: lines.filter((l) => l.decision === 'watch'), parts_total: parts,
    labour, visit_charge: VISIT, repair_price_now: parts + labour + VISIT,
    expected_cost: Math.round(lines.reduce((s, l) => s + l.expected_cost, 0)) };
}

export function inr(n) {
  const s = Math.round(n).toString();
  if (s.length <= 3) return `₹${s}`;
  return `₹${s.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ',')},${s.slice(-3)}`;
}

// Combine the latest windows into one assessment (vote + median health).
export function summarise(windows, { usage = 2, healthOverride = null, slopes = {} } = {}) {
  if (!windows.length) return null;
  const recent = windows.slice(-6);
  const votes = {};
  for (const w of recent) votes[w.fault] = (votes[w.fault] || 0) + w.confidence;
  const fault = Object.entries(votes).sort((a, b) => b[1] - a[1])[0][0];
  const same = recent.filter((w) => w.fault === fault);
  const conf = mean(same.map((w) => w.confidence)) * same.length / recent.length;
  const latest = same[same.length - 1];
  const median = (a) => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
  const health = {};
  for (const c of COMPONENTS) health[c] = median(recent.map((w) => w.health[c]));
  if (healthOverride) Object.assign(health, healthOverride);
  const tds = median(recent.map((w) => w.features.tds_in));
  const comps = COMPONENTS.map((c) => {
    const h = health[c];
    const r = rul(c, h, tds, usage, slopes[c] ?? null);
    let pf = 0;
    if (FAULT_COMP[fault] === c) pf = conf * (c === 'sensors' ? 1 : clip((100 - h) / 45, 0, 1));
    const fp = Math.max(probWithin(r), pf);
    const status = h < REPLACE_T ? 'replace' : h < 70 ? 'warning' : 'normal';
    return { component: c, name: COMP_NAME[c], health: h, status, rul: r, failure_prob: fp };
  });
  const q = quote(comps);
  for (const c of comps) c.decision = q.lines.find((l) => l.component === c.component).decision;
  let status = fault !== 'normal' || comps.some((c) => c.status === 'warning') ? 'warning' : 'normal';
  if (q.replace_now.length) status = 'replace';
  return { status, fault, fault_name: FAULT_NAME[fault], confidence: conf, reason: latest.reason, evidence: latest.evidence,
    drivers: latest.drivers, components: comps, quote: q, tds_in: tds, rejection: median(recent.map((w) => w.features.rej)),
    window_end: latest.end };
}

export function rulText(r) {
  if (!r) return '';
  return r.high <= 0 ? 'replace now' : `${r.low}–${r.high} days`;
}
