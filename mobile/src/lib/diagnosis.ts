// Full on-site diagnosis, run on the technician's phone from the Bluetooth
// stream (no server needed). The server only runs a light quick check per
// device; this is the deeper check that confirms or rejects its hypothesis.
//
//  1. plausibility of every sensor (dead, frozen, out of range, impossible)
//  2. per-signal statistics: level vs the device's healthy baseline, spread, trend
//  3. cross-check: a fault is named only when related signals agree;
//     one abnormal signal on its own means a sensor fault, not a component
//  4. severity -> component health, confidence, remaining-life range
//  5. dynamic quote, and the verdict against the server's hypothesis
import type { Reading } from './protocol';
import type { Baseline, ComponentKey, Report } from './types';

export const MIN_READINGS = 15; // 30 s of data at one reading every 2 s
const FAULT_LABEL: Record<string, string> = {
  normal: 'No fault', clog: 'Filter/line clogging', pump: 'Pump weakness', membrane: 'RO membrane wear',
  turbidity: 'Pre-filter exhausted (cloudy water)', sensor: 'Sensor fault',
};
const FAULT_COMPONENT: Record<string, ComponentKey> = { clog: 'filter', pump: 'pump', membrane: 'membrane', turbidity: 'prefilter', sensor: 'sensors' };
export const PARTS: Record<ComponentKey, [string, number]> = {
  filter: ['Sediment/carbon filter', 400], prefilter: ['Sediment pre-filter', 350], membrane: ['RO membrane', 1800],
  pump: ['Booster pump', 1500], sensors: ['Sensor/electronics fix', 600],
};
const LABOUR = 300, VISIT = 200;
// health points lost per day at normal household use (for the remaining-life range)
const WEAR_PER_DAY: Partial<Record<ComponentKey, number>> = { pump: 0.05, membrane: 0.09, filter: 0.17, prefilter: 0.25 };

const clip = (v: number, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const mean = (a: number[]) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : NaN);
const sd = (a: number[]) => { if (a.length < 2) return 0; const m = mean(a); return Math.sqrt(mean(a.map((x) => (x - m) ** 2))); };
function slopePerMin(rs: Reading[], k: keyof Reading): number {
  if (rs.length < 4) return 0;
  const t0 = rs[0].ts, xs = rs.map((r) => (r.ts - t0) / 60), ys = rs.map((r) => Number(r[k]));
  const mx = mean(xs), my = mean(ys);
  let n = 0, d = 0;
  xs.forEach((x, i) => { n += (x - mx) * (ys[i] - my); d += (x - mx) ** 2; });
  return d ? n / d : 0;
}
const running = (r: Reading) => r.pump_current_A > 0.15 || r.flow_rate_Lmin > 0.05;
const rejection = (r: Reading) => 1 - r.tds_out_ppm / Math.max(r.tds_in_ppm, 1);

export interface Diagnosis {
  fault: string;
  label: string;
  confidence: number;
  reason: string;
  evidence: string[];
  health: Record<ComponentKey, number>;
  rul: Partial<Record<ComponentKey, { low: number; high: number } | null>>;
  quote: Report['quote'];
  stats: Record<string, number>;
  enough: boolean;
  n: number;
}

/** baseline: the device's healthy reference from the server job (may be null). */
export function diagnose(readings: Reading[], baseline: Baseline | null): Diagnosis {
  const n = readings.length;
  const run = readings.filter(running);
  const tail = readings.slice(-4);
  const ev: string[] = [];
  const b: Baseline = {
    // Without a learnt baseline, use the first quarter of this session as the reference for
    // flow/current/turbidity, and the design values for pressure and rejection.
    flow_rate_Lmin: baseline?.flow_rate_Lmin ?? mean(run.slice(0, Math.max(4, run.length >> 2)).map((r) => r.flow_rate_Lmin)),
    pump_current_A: baseline?.pump_current_A ?? mean(run.slice(0, Math.max(4, run.length >> 2)).map((r) => r.pump_current_A)),
    turbidity_voltage_V: baseline?.turbidity_voltage_V ?? mean(readings.slice(0, Math.max(4, n >> 2)).map((r) => r.turbidity_voltage_V)),
    pressure_bar: baseline?.pressure_bar ?? 5.5,
    rejection: baseline?.rejection ?? 0.955,
  };

  // ---- 1. sensor plausibility
  const sensor: string[] = [];
  const tin = mean(readings.map((r) => r.tds_in_ppm)), tout = mean(readings.map((r) => r.tds_out_ppm));
  if (tin > 100 && Math.max(...tail.map((r) => r.tds_out_ppm)) < 3) sensor.push('TDS-out probe reads about 0 ppm while TDS in is normal');
  if (tout > tin * 1.05) sensor.push('TDS out is higher than TDS in');
  if (Math.max(...tail.map((r) => r.turbidity_voltage_V)) < 0.05) sensor.push('Turbidity sensor gives no signal (disconnected?)');
  if (run.length >= 5 && sd(run.map((r) => r.pressure_bar)) < 1e-4) sensor.push('Pressure reading is frozen');
  if (run.length >= 5 && mean(run.map((r) => r.flow_rate_Lmin)) < 0.02 && Math.abs(mean(run.map((r) => r.pump_current_A)) / b.pump_current_A - 1) < 0.1
      && mean(run.map((r) => r.pressure_bar)) > 0.8 * b.pressure_bar) sensor.push('Flow reads 0 while pump current and pressure are normal');

  // ---- 2. statistics
  const rq = run.length ? mean(run.map((r) => r.flow_rate_Lmin)) / b.flow_rate_Lmin : 1;
  const ri = run.length ? mean(run.map((r) => r.pump_current_A)) / b.pump_current_A : 1;
  const rp = run.length ? mean(run.map((r) => r.pressure_bar)) / b.pressure_bar : 1;
  const rej = mean(readings.map(rejection));
  const drop = (b.rejection - rej) * 100;
  const turb = mean(readings.map((r) => r.turbidity_voltage_V));
  const rt = turb / b.turbidity_voltage_V;
  const iRipple = run.length ? sd(run.map((r) => r.pump_current_A)) / Math.max(b.pump_current_A, 0.05) : 0;
  const stats = { flow_ratio: rq, current_ratio: ri, pressure_ratio: rp, rejection_pct: rej * 100, rejection_drop_pts: drop,
    turbidity_ratio: rt, current_ripple: iRipple, tds_out_trend_ppm_min: slopePerMin(readings, 'tds_out_ppm'), running_share: run.length / Math.max(n, 1) };
  const pct = (r: number) => `${Math.abs(r - 1) * 100 | 0}%`;

  // ---- 3. cross-check
  // Without the server's baseline, flow / current / turbidity have no healthy reference,
  // so only pressure (design value) and TDS can point at a component, with lower confidence.
  const known = !!baseline;
  const pLow = rp < 0.9, pHigh = rp > 1.07, qLow = known && rq < 0.88, iAbn = known && (Math.abs(ri - 1) > 0.07 || iRipple > 0.12);
  const sev = {
    clog: clip(((1 - rq) / 0.7 + (rp - 1) / 0.45) / 2), pump: clip(((1 - rp) / 0.55 + Math.abs(1 - ri) / 0.35) / 2),
    membrane: clip(drop / 45), turbidity: clip((1 - rt) / 0.35),
  };
  let fault = 'normal', conf = 0.9, reason = 'All signals agree with a healthy purifier.';
  if (sensor.length) {
    fault = 'sensor'; conf = 0.9; reason = `${sensor[0]}. Related signals look normal, so no component is blamed.`;
    ev.push(...sensor);
  } else if (run.length >= 5 && qLow && pHigh) {
    fault = 'clog'; conf = clip(0.65 + sev.clog * 0.8, 0, 0.99);
    reason = 'Low flow with high pressure: the filter or the line is restricted.';
    ev.push(`Flow ${pct(rq)} below normal`, `Pressure ${pct(rp)} above normal`);
    if (ri > 1.03) ev.push(`Pump working harder: current ${pct(ri)} up`);
  } else if (run.length >= 5 && pLow && iAbn) {
    fault = 'pump'; conf = clip(0.65 + sev.pump * 0.8, 0, 0.99);
    reason = 'Low pressure together with abnormal pump current: the booster pump is weak.';
    ev.push(`Pressure ${pct(rp)} below normal`, `Pump current ${pct(ri)} ${ri < 1 ? 'below' : 'above'} normal`);
    if (qLow) ev.push(`Flow ${pct(rq)} below normal`);
  } else if (drop > 4) {
    fault = 'membrane'; conf = clip(0.7 + sev.membrane, 0, 0.99);
    reason = `Salt rejection has fallen ${drop.toFixed(0)} points while TDS readings are plausible: the RO membrane is worn.`;
    ev.push(`Rejection ${(rej * 100).toFixed(1)}% (healthy ${(b.rejection * 100).toFixed(1)}%)`, `TDS out ${tout.toFixed(0)} ppm`);
  } else if (!known && run.length >= 5 && (pHigh || pLow)) {
    fault = pHigh ? 'clog' : 'pump'; conf = 0.6;
    reason = pHigh ? 'Pressure is above the design value: the filter or line may be restricted.' : 'Pressure is below the design value: the booster pump may be weak.';
    ev.push(`Pressure ${pct(rp)} ${pHigh ? 'above' : 'below'} the design value`, 'No healthy baseline for this purifier: flow and current not compared');
  } else if (known && rt < 0.92 && Math.min(...readings.map((r) => r.turbidity_voltage_V)) > 0.3 * b.turbidity_voltage_V) {
    fault = 'turbidity'; conf = clip(0.65 + sev.turbidity, 0, 0.99);
    reason = 'Water is cloudier than this unit\'s clear-water level: the sediment pre-filter is exhausted.';
    ev.push(`Turbidity signal ${pct(rt)} below clear-water level`);
  } else if (known && run.length >= 5 && (pLow || (qLow && !pHigh && Math.abs(ri - 1) < 0.05) || (pHigh && !qLow))) {
    fault = 'sensor'; conf = 0.7;
    const which = pLow || pHigh ? 'Pressure' : 'Flow';
    reason = `${which} is abnormal but the related signals are normal: ${which.toLowerCase()} sensor suspected.`;
    ev.push(`${which} ${pct(pLow || pHigh ? rp : rq)} ${(pLow || qLow) ? 'below' : 'above'} normal on its own`);
  }
  if (n < MIN_READINGS) conf *= n / MIN_READINGS;

  // ---- 4. component health and remaining life
  const health: Record<ComponentKey, number> = {
    pump: 100 - 70 * sev.pump * (fault === 'pump' ? 1 : 0.15),
    filter: 100 - 70 * sev.clog * (fault === 'clog' ? 1 : 0.15),
    membrane: 100 - 70 * sev.membrane * (fault === 'membrane' ? 1 : fault === 'sensor' ? 0 : 0.3),
    prefilter: 100 - 70 * sev.turbidity * (fault === 'turbidity' ? 1 : fault === 'sensor' ? 0 : 0.3),
    sensors: fault === 'sensor' ? 30 : 99,
  };
  (Object.keys(health) as ComponentKey[]).forEach((k) => { health[k] = Math.round(clip(health[k], 0, 100)); });
  const rul: Diagnosis['rul'] = {};
  (Object.keys(WEAR_PER_DAY) as ComponentKey[]).forEach((k) => {
    const h = health[k];
    if (h < 40) { rul[k] = { low: 0, high: 0 }; return; }
    const mid = (h - 40) / (WEAR_PER_DAY[k]! * (1 + (100 - h) / 60));
    rul[k] = { low: Math.round(mid * 0.7), high: Math.round(mid * 1.4) };
  });

  // ---- 5. quote
  const lines: { part: string; cost: number }[] = [];
  (Object.keys(PARTS) as ComponentKey[]).forEach((k) => {
    const failProb = FAULT_COMPONENT[fault] === k ? conf * (k === 'sensors' ? 1 : clip((100 - health[k]) / 45)) : 0;
    if (health[k] < 40 || failProb > 0.6) lines.push({ part: PARTS[k][0], cost: PARTS[k][1] });
  });
  const parts = lines.reduce((s, l) => s + l.cost, 0);
  const labour = lines.length ? LABOUR : 0;
  const quote = { replace: lines.map((l) => l.part), lines, labour, visit: VISIT, total: parts + labour + VISIT };

  return { fault, label: FAULT_LABEL[fault], confidence: Math.round(conf * 100) / 100, reason, evidence: ev, health, rul, quote,
    stats: Object.fromEntries(Object.entries(stats).map(([k, v]) => [k, Math.round(v * 1000) / 1000])), enough: n >= MIN_READINGS, n };
}

export function toReport(d: Diagnosis, hypothesis: string | null, source: Report['source']): Report {
  const verdict: Report['verdict'] = d.fault === 'normal' ? 'no_fault' : !hypothesis || d.fault === hypothesis ? 'confirmed' : 'different';
  return { verdict, fault: d.fault, fault_label: d.label, confidence: d.confidence, reason: d.reason, evidence: d.evidence, health: d.health,
    rul: d.rul, quote: d.quote, readings: d.n, collected_at: new Date().toISOString().slice(0, 19).replace('T', ' '), source };
}
