// The simulated AquaSense world: one live rig (Unit A) with its SD card and
// Wi-Fi link, the company cloud (Unit C: ingest, scoring, alerts, tickets) and
// six simulated fleet units. Everything runs in the browser.
import {
  ALERT_N, ALERT_P, COMPONENTS, PARTS, Rig, SYNC_S, csvLine, diagnose, inr, isoLocal, rng, rulText,
  summarise, wearRate, windowFeatures, FAULT_COMP,
} from './engine.js';

const FLEET = [
  { id: 'H-1042', name: 'Kitchen RO', customer: 'Asha Verma', phone: '+91 98100 11042', address: 'B-12 Shanti Nagar', city: 'Jaipur', source: 'borewell', tds: 1400, usage: 3, target: { pump: 48, membrane: 82, filter: 87 } },
  { id: 'H-2210', name: 'Flat 4B RO', customer: 'Ravi Menon', phone: '+91 98200 22210', address: '4B Sea View Apts', city: 'Chennai', source: 'tanker', tds: 1100, usage: 2.5, target: { pump: 90, membrane: 43, filter: 86 } },
  { id: 'H-0871', name: 'Villa RO', customer: 'Meera Iyer', phone: '+91 98300 10871', address: '17 Palm Grove', city: 'Bengaluru', source: 'borewell', tds: 900, usage: 2, target: { pump: 92, membrane: 83, filter: 38 } },
  { id: 'H-3305', name: 'Office pantry RO', customer: 'Kabir Shah', phone: '+91 98400 33305', address: '3rd floor, Orbit Tower', city: 'Pune', source: 'municipal', tds: 320, usage: 4, target: { pump: 90, membrane: 88, filter: 80 }, sensor: 'tds_out_unplugged' },
  { id: 'H-1180', name: 'Home RO', customer: 'Nupur Gupta', phone: '+91 98500 11180', address: '22 Civil Lines', city: 'Lucknow', source: 'municipal', tds: 280, usage: 1.5, target: { pump: 96, membrane: 92, filter: 89 } },
  { id: 'H-2731', name: 'Clinic RO', customer: 'Dr. Arjun Rao', phone: '+91 98600 22731', address: 'Rao Clinic, MG Road', city: 'Hyderabad', source: 'tanker', tds: 750, usage: 3.5, target: { pump: 81, membrane: 79, filter: 85 } },
];

const sev = (h) => (h < 85 ? Math.min(1, (100 - h) / 70) : 0);

function scoreRows(rows, stepMs = 10000, fromMs = null) {
  const out = [];
  if (!rows.length) return out;
  const t0 = fromMs ?? new Date(rows[0].timestamp).getTime() + 60000;
  const t1 = new Date(rows[rows.length - 1].timestamp).getTime();
  for (let e = t0; e <= t1; e += stepMs) {
    const f = windowFeatures(rows, e);
    if (f) { const d = diagnose(f); d.end = e; out.push(d); }
  }
  return out;
}

function history(target, tds, usage, seed, days = 60) {
  const r = rng(seed), out = {};
  for (const c of ['pump', 'membrane', 'filter']) {
    const rate = wearRate(c, tds, usage) * (1 + (100 - target[c]) / 60) * (0.8 + 0.4 * r.u());
    const pts = [];
    for (let k = days - 1; k >= 0; k--) pts.push(Math.min(100, target[c] + rate * k + r.normal(0, 0.7)));
    pts[pts.length - 1] = target[c];
    out[c] = pts;
  }
  return out;
}

function slope(arr) {
  const y = arr.slice(-14), n = y.length, xm = (n - 1) / 2, ym = y.reduce((a, b) => a + b, 0) / n;
  let num = 0, den = 0;
  y.forEach((v, i) => { num += (i - xm) * (v - ym); den += (i - xm) ** 2; });
  return num / den;
}

export class World {
  constructor() { this.reset(); }

  reset() {
    this.listeners = this.listeners || [];
    this.speed = 5;
    this.paused = false;
    this.wifi = true;
    this.cardInRig = true;
    this.log = [];
    const now = Date.now();
    this.rig = new Rig({ id: 'AQ-RIG-01', seed: 42, start: now - 12 * 60 * 1000,
      counters: { runtime: 1250000, cycles: 14200, uv: 347, filter: 212 } });
    this.sd = this.rig.run(12 * 60);
    this.sent = this.sd.length;
    this.alerts = [];
    this.tickets = [];
    this.nextId = { alert: 1, ticket: 1 };
    this.units = {};
    this.addUnit({ id: 'AQ-RIG-01', name: 'Live RO test rig', customer: 'Hackathon demo customer', phone: '+91 90000 00000',
      address: 'Demo stand', city: 'Venue', source: 'tanker', tds: 300, usage: 2, simulated: false });
    const u = this.units['AQ-RIG-01'];
    u.rows = this.sd.slice();
    u.windows = scoreRows(u.rows);
    u.lastRx = Date.now();
    FLEET.forEach((f, k) => this.seedFleetUnit(f, k));
    this.carry = 0;
    this.lastSync = this.rig.t;
    this.emit('reset');
  }

  addUnit(info) {
    this.units[info.id] = { ...info, rows: [], windows: [], alertState: { fault: null, streak: 0 }, history: null, lastRx: null };
  }

  seedFleetUnit(f, k) {
    this.addUnit({ ...f, simulated: true });
    const u = this.units[f.id];
    const rig = new Rig({ id: f.id, seed: 100 + k, start: Date.now() - 6 * 60 * 1000,
      faults: { clog: sev(f.target.filter), pump: sev(f.target.pump), membrane: sev(f.target.membrane), sensor: f.sensor || null, tds: f.tds },
      counters: { runtime: f.usage * 3600 * 300, cycles: Math.round(f.usage * 9000), uv: f.usage * 300, filter: f.usage * 120 } });
    u.rows = rig.run(6 * 60);
    u.windows = scoreRows(u.rows);
    u.history = history(f.target, f.tds, f.usage, 7 + k);
    u.lastRx = Date.now() - (2 + k) * 60000;
    for (const w of u.windows) this.alertRule(u, w, true);
  }

  on(fn) { this.listeners.push(fn); }
  emit(ev, data) { for (const fn of this.listeners) fn(ev, data); }
  note(text) { this.log.unshift({ t: this.rig.t, text }); this.log = this.log.slice(0, 40); this.emit('log'); }

  // ---------------------------------------------------------------- controls
  setFault(patch) {
    Object.assign(this.rig.f, patch);
    const f = this.rig.f;
    const parts = [];
    if (f.clog) parts.push(`CLOG valve at mark ${Math.round(f.clog * 3)}`);
    if (f.pump) parts.push(`PWM knob at mark ${Math.round(f.pump * 3)}`);
    if (f.membrane) parts.push(`BYPASS valve at mark ${Math.round(f.membrane * 3)}`);
    if (f.sensor) parts.push({ tds_out_unplugged: 'TDS-out probe unplugged', pressure_frozen: 'pressure reading frozen', tds_out_frozen: 'TDS-out frozen' }[f.sensor]);
    this.note(`Fault panel: ${parts.join(', ') || 'all faults off'}; salt ${f.tds} ppm`);
    this.emit('change');
  }

  setWifi(on) {
    this.wifi = on;
    this.note(on ? 'Hotspot on: the rig will upload its SD backlog' : 'Hotspot off: the rig keeps logging to the SD card only');
    this.emit('change');
  }

  // ---------------------------------------------------------------- time
  tick(realMs) {
    if (this.paused) return;
    this.carry += (realMs / 1000) * this.speed;
    let n = Math.floor(this.carry);
    this.carry -= n;
    n = Math.min(n, 600);
    for (let k = 0; k < n; k++) this.second();
    if (n) this.emit('tick');
  }

  second() {
    const row = this.rig.step();
    if (row && this.cardInRig) this.sd.push(row);
    if (this.rig.t - this.lastSync >= SYNC_S * 1000) { this.lastSync = this.rig.t; this.sync(); }
  }

  get backlog() { return this.sd.length - this.sent; }

  sync() {
    if (!this.wifi || this.backlog === 0) return;
    // firmware: up to 5 POSTs of 40 lines per 10 s tick
    const batch = this.sd.slice(this.sent, this.sent + 200);
    this.sent += batch.length;
    this.ingest('AQ-RIG-01', batch);
  }

  ingest(id, rows) {
    const u = this.units[id];
    const lastScored = u.windows.length ? u.windows[u.windows.length - 1].end : null;
    u.rows.push(...rows);
    if (u.rows.length > 2000) u.rows = u.rows.slice(-2000);
    u.lastRx = Date.now();
    const from = lastScored ? lastScored + 10000 : null;
    const ws = scoreRows(u.rows, 10000, from);
    for (const w of ws) {
      u.windows.push(w);
      this.alertRule(u, w);
    }
    if (u.windows.length > 400) u.windows = u.windows.slice(-400);
    if (rows.length > 20) this.note(`Cloud received ${rows.length} backlog lines from the SD card and scored them in time order`);
    this.emit('ingest', { id, n: rows.length });
  }

  alertRule(u, w, silent = false) {
    const st = u.alertState;
    const ok = w.fault !== 'normal' && w.confidence > ALERT_P;
    if (!ok) { st.fault = null; st.streak = 0; return; }
    st.streak = st.fault === w.fault ? st.streak + 1 : 1;
    st.fault = w.fault;
    if (st.streak < ALERT_N) return;
    if (this.alerts.some((a) => a.unit === u.id && a.fault === w.fault && (a.status === 'open' || a.status === 'ticketed'))) return;
    const a = { id: this.nextId.alert++, unit: u.id, fault: w.fault, fault_name: w.fault_name, confidence: w.confidence,
      reason: w.reason, status: 'open', t: w.end };
    this.alerts.unshift(a);
    if (!silent) { this.note(`ALERT ${u.id}: ${w.fault_name} (${Math.round(w.confidence * 100)}%), 3 confident windows in a row`); this.emit('alert', a); }
  }

  // ---------------------------------------------------------------- read
  assessment(id) {
    const u = this.units[id];
    const override = u.simulated ? u.target : null;
    const slopes = u.history ? Object.fromEntries(Object.entries(u.history).map(([c, h]) => [c, slope(h)])) : {};
    return summarise(u.windows, { usage: u.usage, healthOverride: override, slopes });
  }

  online(id) {
    const u = this.units[id];
    if (u.simulated) return false;
    return this.wifi;
  }

  fleet() {
    return Object.values(this.units).map((u) => {
      const a = this.assessment(u.id);
      const worst = a ? [...a.components].sort((x, y) => x.health - y.health)[0] : null;
      return { u, a, worst, alerts: this.alerts.filter((x) => x.unit === u.id && x.status === 'open').length };
    });
  }

  // ---------------------------------------------------------------- service
  contact(id, note = '') {
    const u = this.units[id], a = this.assessment(id);
    const dup = this.tickets.find((t) => t.unit === id && t.fault === a.fault_name && t.status !== 'done');
    if (dup) return dup;
    const carry = a.quote.replace_now.map((l) => l.part);
    const fc = FAULT_COMP[a.fault];
    if (fc && !carry.includes(PARTS[fc][0])) carry.push(PARTS[fc][0]);
    if (!carry.length) carry.push('Standard service kit');
    const window = a.status === 'replace' ? 'Within 48 hours' : 'Within 7 days';
    const t = { id: this.nextId.ticket++, unit: id, fault: a.fault_name, carry, price: a.quote.repair_price_now, window,
      status: 'open', created: this.rig.t, note, card: jobCard(u, a, carry, window, note, this.rig.t) };
    this.tickets.unshift(t);
    const al = this.alerts.find((x) => x.unit === id && x.status === 'open');
    if (al) al.status = 'ticketed';
    this.note(`Ticket #${t.id} for ${id}: carry ${carry.join(', ')}, estimate ${inr(t.price)}`);
    this.emit('change');
    return t;
  }

  setTicket(t, status) {
    t.status = status;
    if (status === 'done') { const al = this.alerts.find((x) => x.unit === t.unit && x.status === 'ticketed'); if (al) al.status = 'resolved'; }
    this.emit('change');
  }

  warranty() {
    const parts = Object.fromEntries(Object.entries(PARTS).map(([k, [part, cost]]) => [k, { part, cost, expected: 0, due: 0, expectedCost: 0 }]));
    let total = 0, now = 0;
    const units = [];
    for (const { u, a } of this.fleet()) {
      if (!a) continue;
      for (const l of a.quote.lines) { const p = parts[l.component]; p.expected += l.failure_prob; p.expectedCost += l.expected_cost; p.due += l.decision === 'replace' ? 1 : 0; }
      total += a.quote.expected_cost; now += a.quote.repair_price_now;
      units.push({ u, a });
    }
    return { parts: Object.values(parts), total, now, units };
  }

  // ---------------------------------------------------------------- handheld
  cardRows(minutes = 30) {
    if (!this.sd.length) return [];
    const end = new Date(this.sd[this.sd.length - 1].timestamp).getTime();
    return this.sd.filter((r) => new Date(r.timestamp).getTime() > end - minutes * 60000);
  }
}

export function analyseCard(rows) {
  const ws = scoreRows(rows, 30000);
  const a = summarise(ws);
  return a ? { a, rows, windows: ws.length } : null;
}

export function demoCardRows() {
  const rig = new Rig({ id: 'AQ-RIG-01', seed: 21, start: Date.now() - 40 * 60000, faults: { tds: 800 },
    counters: { runtime: 1250000, cycles: 14200, uv: 347, filter: 212 } });
  const rows = rig.run(25 * 60);
  rig.f.pump = 2 / 3;
  rows.push(...rig.run(15 * 60));
  return rows;
}

export function jobCard(u, a, carry, window, note, t) {
  const L = ['AQUASENSE SERVICE JOB CARD', '='.repeat(42),
    `Created      : ${isoLocal(t).replace('T', ' ').slice(0, 16)}`,
    `Unit         : ${u.id}  (${u.name})`,
    `Customer     : ${u.customer}   ${u.phone}`,
    `Address      : ${u.address}, ${u.city}`,
    `Water source : ${u.source}  (TDS in ~${Math.round(a.tds_in)} ppm)`,
    `Visit window : ${window}`, '',
    `LIKELY FAULT : ${a.fault_name}  (confidence ${Math.round(a.confidence * 100)}%)`,
    `Why          : ${a.reason}`, ...a.evidence.slice(0, 4).map((e) => `   - ${e}`), '',
    'PARTS TO CARRY', ...carry.map((p) => `   [ ] ${p}`), '', 'COMPONENT HEALTH',
    ...a.components.map((c) => `   ${c.name.padEnd(16)} ${String(Math.round(c.health)).padStart(4)}%  ${c.rul ? 'RUL ' + rulText(c.rul) : ''}`),
    '', 'ESTIMATE (dynamic)',
    ...a.quote.replace_now.map((l) => `   ${l.part.padEnd(26)} ${inr(l.cost).padStart(8)}`)];
  if (a.quote.labour) L.push(`   ${'Labour'.padEnd(26)} ${inr(a.quote.labour).padStart(8)}`);
  L.push(`   ${'Visit charge'.padEnd(26)} ${inr(a.quote.visit_charge).padStart(8)}`, `   ${'TOTAL'.padEnd(26)} ${inr(a.quote.repair_price_now).padStart(8)}`);
  if (a.quote.watch_list.length) L.push(`   Watch list (not billed): ${a.quote.watch_list.map((l) => `${l.part} (p=${l.failure_prob.toFixed(2)})`).join(', ')}`);
  if (note) L.push('', `Note: ${note}`);
  L.push('', 'RUL ranges use a model trained on simulated degradation.');
  return L.join('\n');
}

export { COMPONENTS, csvLine };
