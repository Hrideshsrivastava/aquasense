import { COMP_NAME, FAULT_NAME, csvLine, inr, rulText, REPLACE_T } from './engine.js';
import { World, analyseCard, demoCardRows } from './world.js';
import { lineChart } from './charts.js';

const W = new World();
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pct = (x) => `${Math.round(x * 100)}%`;
const hhmmss = (ms) => new Date(ms).toLocaleTimeString('en-IN', { hour12: false });
const STATUS_TXT = { normal: 'Healthy', warning: 'Watch', replace: 'Service needed', none: 'No data' };
const VERDICT = { replace: 'replace', watch: 'watch', ok: 'ok' };
const COLOR = { normal: 'var(--good)', warning: 'var(--warn)', replace: 'var(--bad)' };
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const pill = (s) => `<span class="pill ${s || 'none'}">${STATUS_TXT[s || 'none']}</span>`;
const healthBars = (comps, opts = {}) => comps.map((c) => `
  <div class="hb" role="group" aria-label="${esc(c.name)} ${Math.round(c.health)} percent">
    <span class="n">${esc(c.name)}</span>
    <span class="bar"><i style="width:${c.health}%;background:${COLOR[c.status]}"></i><s title="40% replacement threshold"></s></span>
    <span class="v">${Math.round(c.health)}% · ${VERDICT[c.decision]}</span>
    <span class="r">${c.component === 'sensors' ? `fault probability ${c.failure_prob.toFixed(2)}` : `${opts.lifeLabel || 'RUL'} ${rulText(c.rul)}`}</span>
  </div>`).join('');

function toast(text, kind = '') {
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  t.textContent = text;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), 4200);
}
function download(name, text) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function modal(html) {
  const m = $('#modal');
  m.innerHTML = `<div class="box" role="dialog" aria-modal="true">${html}</div>`;
  m.hidden = false;
}
const closeModal = () => { $('#modal').hidden = true; };

// ====================================================================== state
const ui = {
  route: 'overview',
  console: { sub: 'fleet', unit: 'AQ-1001' },
  hh: { mode: 'home', tab: 'health', res: null, source: null, inReader: false },
  phone: { tab: 'purifier', unit: 'AQ-1001', booked: null },
};
const ROUTES = [
  ['overview', 'Overview'], ['rig', 'Purifier'], ['console', 'Console'], ['handheld', 'Handheld'],
  ['app', 'Customer app'], ['models', 'Models'],
];

// ====================================================================== header
function renderNav() {
  $('#nav').innerHTML = ROUTES.map(([r, l]) => {
    const n = r === 'console' ? W.alerts.filter((a) => a.status === 'open').length : 0;
    return `<a href="#${r}" ${ui.route === r ? 'aria-current="page"' : ''}>${l}${n ? `<span class="badge">${n}</span>` : ''}</a>`;
  }).join('');
}
function renderSimbar() {
  const theme = document.documentElement.dataset.theme;
  $('#simbar').innerHTML = `
    <span class="chip ${W.wifi ? 'live' : 'off'}" title="Connection to AquaSense cloud"><span class="dot"></span>${W.wifi ? 'Cloud connected' : 'Unit offline'}</span>
    <button class="btn pri" data-act="tour-menu">Take the tour</button>
    <button class="btn" data-act="theme" aria-label="Toggle dark mode">${theme === 'dark' ? '☀' : '☾'}</button>`;
}

// ====================================================================== overview
const V = {};
V.overview = {
  html: () => `
  <section class="hero">
    <div>
      <div class="small muted" style="font-weight:700;letter-spacing:.08em">SMART RO SERVICE PLATFORM</div>
      <h1>AquaSense: from purifiers to intelligent water ecosystems</h1>
      <p class="lead">Purifiers report TDS and one filter-life percentage. AquaSense gives <b>every component</b> a health score and a
        remaining-useful-life range, names a fault only when related signals agree, and turns that into a <b>dynamic repair price</b>,
        online through the company cloud and offline through a technician handheld that reads the purifier's SD card.</p>
      <div class="row" style="margin-top:16px">
        <button class="btn pri big" data-act="tour" data-arg="s1">▶ Scenario 1: company notices</button>
        <button class="btn big" data-act="tour" data-arg="s2">▶ Scenario 2: routine visit</button>
      </div>
    </div>
    <div class="card">
      <h3>Live system</h3>
      <div id="ov-live"></div>
    </div>
  </section>
  <section class="card" style="margin-bottom:16px">
    <h2>How it fits together</h2>
    <div class="arch">
      <button class="node" data-act="go" data-arg="rig"><span class="tag">DEVICE</span><b>Smart purifier</b><span>Reads TDS in/out, pressure, flow, pump current and temperature; logs to SD; syncs over Wi-Fi</span></button>
      <button class="node" data-act="go" data-arg="console"><span class="tag">CLOUD</span><b>Service console</b><span>Ingest → database → ML engine → alerts → contact customer with the right part</span></button>
      <button class="node" data-act="go" data-arg="handheld"><span class="tag">FIELD</span><b>Technician handheld</b><span>Reads the SD card offline: health, fault, RUL, dynamic price</span></button>
      <button class="node" data-act="go" data-arg="models"><span class="tag">AI</span><b>Diagnosis models</b><span>Fault classifier, anomaly detector, health scores, RUL, explanations</span></button>
    </div>
    <p class="small muted" style="margin:12px 0 0">Both paths carry the same data format, so the same models give the same answer in the cloud and on the handheld.</p>
  </section>
  <section class="grid g2" style="margin-bottom:16px">
    <div class="card scen"><h3>Scenario 1 · Company notices</h3>
      <ol><li>The source water turns hard: TDS in rises, rejection stays healthy, <b>no false alarm</b>.</li>
      <li>The filter starts clogging: flow falls, pressure rises.</li>
      <li>After three confident windows the console raises <b>Filter/line clogging</b>.</li>
      <li><b>Contact customer</b> creates a job card naming the part to carry.</li></ol>
      <button class="btn pri" data-act="tour" data-arg="s1">Run it</button></div>
    <div class="card scen"><h3>Scenario 2 · Routine visit</h3>
      <ol><li>Wi-Fi goes down; the purifier keeps logging to its SD card.</li>
      <li>The booster pump weakens. The console sees nothing.</li>
      <li>The technician puts the card in the handheld: <b>pump degradation, RUL range, ₹2,000 quote</b>.</li>
      <li>A different fault, a rescan, and the quote moves.</li></ol>
      <button class="btn pri" data-act="tour" data-arg="s2">Run it</button></div>
  </section>
  <section class="feature-list">
    <div class="card"><b>Component-level health</b>Pump, membrane, filter/line and sensors each get a score, with the 40% replacement threshold.</div>
    <div class="card"><b>Cross-checked diagnosis</b>Low pressure blames the pump only when current and build-up agree; one odd signal alone means a sensor fault.</div>
    <div class="card"><b>RUL as a range</b>"41–52 days", not a single date, so visits are planned before anything fails.</div>
    <div class="card"><b>Works offline</b>SD card first, Wi-Fi second. The backlog uploads when the link returns and is scored in time order.</div>
    <div class="card"><b>Dynamic repair price</b>Parts to replace now + labour + visit; watch list; expected cost for warranty planning.</div>
    <div class="card"><b>Right part, first visit</b>The job card lists the likely fault and the part to carry before the van leaves.</div>
  </section>`,
  live() {
    const a = W.assessment('AQ-1001');
    const open = W.alerts.filter((x) => x.status === 'open').length;
    $('#ov-live').innerHTML = `
      <div class="row between"><b>AQ-1001 · Kitchen RO</b>${pill(a?.status)}</div>
      <div class="muted small" style="margin:2px 0 8px">${esc(a?.fault_name)} · ${a ? pct(a.confidence) : ''} confidence · ${W.wifi ? 'online' : 'offline, logging to SD'}</div>
      ${a ? healthBars(a.components) : ''}
      <div class="row between small" style="margin-top:10px"><span class="muted">Fleet: ${Object.keys(W.units).length} units · ${open} open alerts</span>
      <a href="#console">Open console →</a></div>`;
  },
};

// ====================================================================== rig
function leds(row) {
  if (!row || !row.pump_on) return W._led ?? 0;
  const rej = (1 - row.tds_out / Math.max(row.tds_in, 1)) * 100, q = row.flow_lpm / 0.6, p = row.pressure_bar / 5.5;
  let l = 0;
  if (rej < 85 || q < 0.7 || p > 1.3 || p < 0.75) l = 1;
  if (rej < 70 || q < 0.4 || p < 0.55) l = 2;
  W._led = l;
  return l;
}
const SEG = (name, opts, cur) => `<div class="seg" role="group" aria-label="${name}">${opts.map(([v, l]) =>
  `<button data-act="fault" data-arg='${JSON.stringify({ [name]: v })}' aria-pressed="${cur === v}">${l}</button>`).join('')}</div>`;

V.rig = {
  html: () => `
  <h1>Smart purifier · AQ-1001</h1>
  <p class="sub">Live view of the purifier: sensors along the filtration path, the on-unit display, and the SD card that keeps every reading,
    even when Wi-Fi is down. A log line every 5 s; every 10 s new lines sync to the cloud.</p>
  <div class="split">
    <div class="stack">
      <div class="card"><div class="row between"><h2>Service mode</h2><span class="small muted">set component condition · levels 1 · 2 · 3</span></div>
        <div id="panel"></div></div>
      <div class="card"><h2>Event log</h2><div id="feed" class="feed"></div></div>
    </div>
    <div class="stack">
      <div class="card loop"><h2>Water loop</h2><div id="loop"></div></div>
      <div class="card"><div class="row between"><h2>On-unit display</h2><div class="leds" id="leds"></div></div><div id="oled" class="oled"></div></div>
      <div class="card"><div class="row between"><h2>SD card · <span class="mono small" id="sdname"></span></h2><span id="syncst" class="small"></span></div>
        <div id="sdlog" class="sdlog"></div>
        <p class="small muted" style="margin:8px 0 0">Orange lines are on the card but have not reached the cloud yet.</p></div>
    </div>
  </div>`,
  panel() {
    const f = W.rig.f;
    const lvl = (v) => Math.round(v * 3);
    const L = [[0, 'off'], [1, '1'], [2, '2'], [3, '3']].map(([v, l]) => [v / 3, l]);
    const pick = (v) => L.find(([x]) => Math.abs(x - v) < 0.01)?.[0] ?? v;
    $('#panel').innerHTML = `
      <div class="panelrow"><div><div class="t">Filter / line</div><div class="d">Clogging → flow falls, pressure rises</div></div>${SEG('clog', L, pick(f.clog))}</div>
      <div class="panelrow"><div><div class="t">Booster pump</div><div class="d">Wear → low pressure, odd current, slow build-up</div></div>${SEG('pump', L, pick(f.pump))}</div>
      <div class="panelrow"><div><div class="t">RO membrane</div><div class="d">Wear → TDS out rises, rejection falls</div></div>${SEG('membrane', L, pick(f.membrane))}</div>
      <div class="panelrow"><div><div class="t">Sensor fault</div><div class="d">One signal goes wrong, the others stay normal</div></div>
        ${SEG('sensor', [[null, 'none'], ['tds_out_unplugged', 'TDS-out probe'], ['pressure_frozen', 'pressure sensor']], f.sensor)}</div>
      <div class="panelrow"><div><div class="t">Source water</div><div class="d">Municipal / mixed / borewell TDS. Not a fault</div></div>
        ${SEG('tds', [[300, '300'], [800, '800'], [1500, '1500 ppm']], f.tds)}</div>
      <div class="panelrow"><div><div class="t">Wi-Fi</div><div class="d">Off = the purifier keeps logging to SD only</div></div>
        <div class="seg"><button data-act="wifi" data-arg="1" aria-pressed="${W.wifi}">on</button><button data-act="wifi" data-arg="0" aria-pressed="${!W.wifi}">off</button></div></div>
      <div class="panelrow"><div><div class="t">SD card</div><div class="d">${W.cardInRig ? 'In the purifier, logging' : 'In the technician handheld'}</div></div>
        ${W.cardInRig ? '<button class="btn" data-act="pull-card">Pull card → handheld</button>' : '<button class="btn" data-act="return-card">Put card back</button>'}</div>
      <div class="row" style="margin-top:12px"><button class="btn" data-act="fault" data-arg='{"clog":0,"pump":0,"membrane":0,"sensor":null}'>All components normal</button>
        <label class="small muted" for="speed">Time scale</label>
        <select id="speed" class="btn" aria-label="Time scale">${[1, 5, 10, 20, 60].map((x) => `<option value="${x}" ${W.speed === x ? 'selected' : ''}>${x}×</option>`).join('')}</select>
        <button class="btn" data-act="reset">Reset</button></div>`;
    void lvl;
  },
  live() {
    const r = W.rig.last || {};
    const on = !!r.pump_on;
    const s = W.rig.f;
    const v = (x, d = 2) => (x === undefined ? '–' : Number(x).toFixed(d));
    $('#loop').innerHTML = `
      <svg viewBox="0 0 560 230" role="img" aria-label="Water loop with live sensor values">
        <rect class="box" x="10" y="80" width="80" height="70" rx="8"/><text x="50" y="108" text-anchor="middle">Inlet</text>
        <text x="50" y="126" text-anchor="middle" class="val">${v(r.tds_in, 0)} ppm</text><text x="50" y="142" text-anchor="middle" class="small">${v(r.temp_c, 1)} °C</text>
        <path class="pipe" d="M90 115 H150"/><path class="flow ${on ? '' : 'stopped'}" d="M90 115 H150"/>
        <rect class="box" x="150" y="85" width="90" height="60" rx="8"/><text x="195" y="108" text-anchor="middle">Pump</text>
        <text x="195" y="126" text-anchor="middle" class="val">${on ? v(r.pump_current_a) + ' A' : 'OFF'}</text>
        ${s.pump ? `<text x="195" y="77" text-anchor="middle" class="fault">pump wear ${Math.round(s.pump * 3)}</text>` : ''}
        <path class="pipe" d="M240 115 H330"/><path class="flow ${on ? '' : 'stopped'}" d="M240 115 H330"/>
        <circle cx="275" cy="115" r="16" class="box"/><text x="275" y="119" text-anchor="middle" style="font-size:10px">P</text>
        <text x="275" y="88" text-anchor="middle" class="val">${v(r.pressure_bar)} bar</text>
        <rect x="300" y="105" width="18" height="20" rx="3" fill="${s.clog ? 'var(--bad)' : 'var(--ink-3)'}"/><text x="309" y="146" text-anchor="middle" class="small ${s.clog ? 'fault' : ''}">Filter${s.clog ? ' ' + Math.round(s.clog * 3) : ''}</text>
        <rect class="box" x="330" y="80" width="100" height="70" rx="8"/><text x="380" y="110" text-anchor="middle">RO membrane</text>
        <text x="380" y="128" text-anchor="middle" class="small ${s.membrane ? 'fault' : ''}">${s.membrane ? 'wear ' + Math.round(s.membrane * 3) : 'rejection ' + v((1 - (r.tds_out || 0) / Math.max(r.tds_in || 1, 1)) * 100, 1) + '%'}</text>
        <path class="pipe" d="M430 95 H520 V40"/><path class="flow ${on ? '' : 'stopped'}" d="M430 95 H520 V40"/>
        <text x="470" y="30" text-anchor="middle">Permeate</text><text x="470" y="84" text-anchor="middle" class="val">${s.sensor === 'tds_out_unplugged' ? '<tspan class="fault">probe?</tspan>' : v(r.tds_out, 1) + ' ppm'}</text>
        <path class="pipe" d="M430 135 H520 V200 H50 V150"/><path class="flow ${on ? '' : 'stopped'}" d="M430 135 H520 V200 H50 V150"/>
        <text x="300" y="218" text-anchor="middle">Reject water · flow <tspan class="val">${v(r.flow_lpm, 3)} L/min</tspan></text>
      </svg>`;
    const lv = leds(r);
    $('#leds').innerHTML = `<span class="led g ${lv === 0 ? 'on' : ''}"><i></i>Normal</span><span class="led y ${lv === 1 ? 'on' : ''}"><i></i>Warning</span><span class="led r ${lv === 2 ? 'on' : ''}"><i></i>Replace</span>`;
    $('#oled').textContent = [
      `AquaSense ${W.wifi ? 'WiFi' : 'noWiFi'}   ${hhmmss(W.rig.t)}`,
      `P ${v(r.pressure_bar)}bar  Q ${v(r.flow_lpm)}L/m`,
      `I ${v(r.pump_current_a)}A   pump ${on ? 'ON' : 'off'}`,
      `TDS ${v(r.tds_in, 0).padStart(4)} -> ${v(r.tds_out, 0)}`,
      `build ${v(r.buildup_s, 1)}s  starts ${W.rig.k.cycles}`,
      'normal mode',
      `${W.cardInRig ? 'SD ok ' : 'SD ERR '}${W.wifi ? (W.backlog > 2 ? 'sending' : 'in sync') : 'WiFi down'}`,
    ].join('\n');
    const tail = W.sd.slice(-12);
    const firstUnsent = W.sent;
    $('#sdname').textContent = W.sd.length ? `${W.sd[W.sd.length - 1].timestamp.slice(0, 10)}.csv` : '';
    $('#sdlog').innerHTML = esc('timestamp,device_id,tds_in,tds_out,temp_c,pressure_bar,flow_lpm,pump_current_a,pump_on,buildup_s,…') + '\n' +
      tail.map((row, k) => { const idx = W.sd.length - tail.length + k; const line = esc(csvLine(row)); return idx >= firstUnsent ? `<span class="unsent">${line}</span>` : line; }).join('\n');
    // a line is written every 5 s and sent every 10 s, so 1-2 lines in flight is "in sync"
    $('#syncst').innerHTML = !W.wifi || W.backlog > 2 ? `<b style="color:var(--serious)">${W.backlog} lines waiting${W.wifi ? ', uploading' : ' (Wi-Fi off)'}</b>` : '<span style="color:var(--good)">● in sync with cloud</span>';
    $('#feed').innerHTML = W.log.map((l) => `<div><time>${hhmmss(l.t)}</time>${esc(l.text)}</div>`).join('') || '<div class="muted">Change a fault to see what happens.</div>';
  },
};

// ====================================================================== console
V.console = {
  html: () => `
    <div class="row between" style="margin-bottom:6px"><h1>Service console</h1><span class="small muted">Service view · live</span></div>
    <div class="subnav" id="subnav"></div>
    <div id="cbody"></div>`,
  mount() { this.skeleton(); },
  skeleton() {
    const s = ui.console.sub;
    const open = W.alerts.filter((a) => a.status === 'open').length;
    $('#subnav').innerHTML = [['fleet', 'Fleet view'], ['unit', 'Unit detail'], ['alerts', `Alerts & tickets${open ? `<span class="badge">${open}</span>` : ''}`], ['warranty', 'Warranty & parts']]
      .map(([k, l]) => `<button data-act="csub" data-arg="${k}" aria-pressed="${s === k}">${l}</button>`).join('');
    const b = $('#cbody');
    if (s === 'fleet') b.innerHTML = '<div class="grid g4" id="fk" style="margin-bottom:16px"></div><div class="card"><div class="tblwrap" id="ft"></div><p class="small muted" style="margin:10px 0 0">Click a unit to open it.</p></div>';
    if (s === 'unit') {
      b.innerHTML = `
        <div class="row" style="margin-bottom:12px"><label class="small muted" for="unitpick">Unit</label>
          <select id="unitpick" class="btn">${Object.values(W.units).map((u) => `<option value="${u.id}" ${u.id === ui.console.unit ? 'selected' : ''}>${u.id} · ${esc(u.customer)} · ${esc(u.city)}</option>`).join('')}</select></div>
        <div class="split">
          <div class="stack"><div class="card" id="uh"></div><div class="card"><h2>Component health</h2><div id="uhb"></div></div></div>
          <div class="stack"><div class="card" id="ua"></div>
            <div class="card"><div class="row between"><h2>Remaining useful life</h2><div id="rulpick"></div></div><div id="c-rul" class="chartbox"></div><p class="small muted" id="rulnote" style="margin:6px 0 0"></p></div></div>
        </div>
        <h2 style="margin:20px 0 10px">Live sensors</h2>
        <div class="grid g3"><div class="card tight"><h3>Pressure (bar)</h3><div id="c-p" class="chartbox"></div></div>
          <div class="card tight"><h3>Reject flow (L/min)</h3><div id="c-q" class="chartbox"></div></div>
          <div class="card tight"><h3>Pump current (A)</h3><div id="c-i" class="chartbox"></div></div></div>
        <div class="grid g2" style="margin-top:16px"><div class="card tight"><div class="row between"><h3>TDS in and out (ppm)</h3>
          <div class="legend"><span><span class="sw" style="background:var(--series-1)"></span>TDS in</span><span><span class="sw" style="background:var(--series-2)"></span>TDS out</span></div></div><div id="c-tds" class="chartbox"></div></div>
          <div class="card tight"><h3>Salt rejection % (pump on)</h3><div id="c-rej" class="chartbox"></div></div></div>`;
      $('#unitpick').addEventListener('change', (e) => { ui.console.unit = e.target.value; ui.rulComp = null; this.live(); });
    }
    if (s === 'alerts') b.innerHTML = '<div class="card" style="margin-bottom:16px"><h2>Alerts</h2><p class="small muted" style="margin-top:-4px">Raised when a fault is predicted with probability above 0.7 for 3 consecutive 60-second windows.</p><div class="tblwrap" id="at"></div></div><div class="card"><h2>Service tickets</h2><div class="tblwrap" id="tt"></div></div>';
    if (s === 'warranty') b.innerHTML = '<div class="grid g3" id="wk" style="margin-bottom:16px"></div><div class="grid g2"><div class="card"><h2>Parts forecast</h2><p class="small muted" style="margin-top:-4px">Expected units = Σ failure probability. "Due now" = replace-now decisions.</p><div id="wp"></div></div><div class="card"><h2>Per unit</h2><div class="tblwrap" id="wu"></div></div></div>';
    this.live();
  },
  live() {
    const s = ui.console.sub;
    if (s === 'fleet') this.fleet();
    if (s === 'unit') this.unit();
    if (s === 'alerts') this.alerts();
    if (s === 'warranty') this.warranty();
    const open = W.alerts.filter((a) => a.status === 'open').length;
    const btn = $('#subnav [data-arg="alerts"]');
    if (btn) btn.innerHTML = `Alerts & tickets${open ? `<span class="badge">${open}</span>` : ''}`;
  },
  fleet() {
    const fl = W.fleet();
    const exp = fl.reduce((s, x) => s + (x.a?.quote.expected_cost || 0), 0);
    $('#fk').innerHTML = [
      ['Units', fl.length, `${fl.filter((x) => x.u.simulated || W.wifi).length} online`],
      ['Open alerts', W.alerts.filter((a) => a.status === 'open').length, 'needs a call'],
      ['Service needed', fl.filter((x) => x.a?.status === 'replace').length, 'replace-now parts'],
      ['Expected repair cost', inr(exp), 'warranty planning'],
    ].map(([l, v, d]) => `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="small muted">${d}</div></div>`).join('');
    $('#ft').innerHTML = `<table class="tbl"><thead><tr><th>Status</th><th>Unit</th><th>Customer</th><th>City</th><th>Source</th><th>Diagnosis</th><th>Lowest health</th><th>Weakest part · RUL</th><th>Repair now</th><th>Link</th></tr></thead><tbody>
      ${fl.map(({ u, a, worst, alerts }) => `<tr class="click" data-act="open-unit" data-arg="${u.id}">
        <td>${pill(a?.status)}</td><td><b>${u.id}</b>${alerts ? `<span class="badge">${alerts}</span>` : ''}</td><td>${esc(u.customer)}</td><td>${esc(u.city)}</td><td>${u.source}</td>
        <td>${esc(a?.fault_name)} <span class="muted small">${a ? pct(a.confidence) : ''}</span></td>
        <td>${worst ? `<span class="minibar"><i style="width:${worst.health}%;background:${COLOR[worst.status]}"></i></span>${Math.round(worst.health)}%` : ''}</td>
        <td>${worst ? `${worst.name} · ${worst.rul ? rulText(worst.rul) : 'n/a'}` : ''}</td><td>${a ? inr(a.quote.repair_price_now) : ''}</td>
        <td class="small">${u.simulated || W.wifi ? '<span style="color:var(--good)">● live</span>' : '<span class="muted">○ offline</span>'}</td></tr>`).join('')}
      </tbody></table>`;
  },
  unit() {
    const u = W.units[ui.console.unit], a = W.assessment(u.id);
    if (!a) return;
    const alert = W.alerts.find((x) => x.unit === u.id && x.status === 'open');
    const ticket = W.tickets.find((t) => t.unit === u.id && t.status !== 'done');
    const lastRow = u.rows[u.rows.length - 1];
    const link = u.simulated || W.wifi ? '<span style="color:var(--good)">● Live: receiving data</span>' : `○ Offline: no data since ${lastRow ? hhmmss(new Date(lastRow.timestamp).getTime()) : '–'}`;
    $('#uh').innerHTML = `
      <div class="row between"><h2 style="margin:0">${u.id} · ${esc(u.name)}</h2>${pill(a.status)}</div>
      <div class="small muted" style="margin:4px 0 12px">${esc(u.customer)} · ${esc(u.phone)} · ${esc(u.address)}, ${esc(u.city)} · ${u.source} water · ${link}</div>
      <div style="font-size:18px;font-weight:700">${esc(a.fault_name)} <span class="muted" style="font-weight:500">· ${pct(a.confidence)} confidence</span></div>
      <p style="margin:6px 0">${esc(a.reason)}</p>
      <ul style="margin:0 0 8px;padding-left:18px">${a.evidence.slice(0, 4).map((e) => `<li>${esc(e)}</li>`).join('')}</ul>
      <div class="small muted">Signals that drove the model: ${a.drivers.join(', ')}</div>
      <div class="grid g3" style="margin-top:14px">
        <div class="kpi"><div class="l">TDS in</div><div class="v">${Math.round(a.tds_in)}<span class="small muted"> ppm</span></div></div>
        <div class="kpi"><div class="l">Rejection</div><div class="v">${a.rejection.toFixed(1)}%</div></div>
        <div class="kpi"><div class="l">Repair now</div><div class="v">${inr(a.quote.repair_price_now)}</div></div></div>`;
    $('#uhb').innerHTML = healthBars(a.components) + '<p class="small muted" style="margin:8px 0 0">Line at 40% = replacement threshold.</p>';
    $('#ua').innerHTML = `
      ${alert ? `<div class="alertbox"><b>ALERT · ${esc(alert.fault_name)}</b> (${pct(alert.confidence)}) raised ${hhmmss(alert.t)}<div class="small">${esc(alert.reason)}</div></div>`
        : ticket ? `<div class="infobox"><b>Ticket #${ticket.id}</b> · ${esc(ticket.window)} · carry ${esc(ticket.carry.join(', '))}</div>`
          : `<div class="okbox"><b>No open alert</b><div class="small muted">An alert needs 3 consecutive windows above 70%.</div></div>`}
      <div class="row" style="margin-top:12px">
        <button class="btn pri" data-act="contact" data-arg="${u.id}">📞 Contact customer</button>
        ${alert ? `<button class="btn" data-act="ack" data-arg="${alert.id}">Acknowledge</button>` : ''}
        ${ticket ? `<button class="btn" data-act="jobcard" data-arg="${ticket.id}">View job card</button>` : ''}
      </div>
      <div class="small muted" style="margin-top:10px">Quote: ${a.quote.replace_now.map((l) => `${l.part} ${inr(l.cost)}`).join(' + ') || 'no parts'}${a.quote.labour ? ` + labour ${inr(a.quote.labour)}` : ''} + visit ${inr(a.quote.visit_charge)}${a.quote.watch_list.length ? ` · watch list: ${a.quote.watch_list.map((l) => l.part).join(', ')}` : ''}</div>`;
    // charts
    const rows = u.rows.filter((r) => new Date(r.timestamp).getTime() > new Date(lastRow.timestamp).getTime() - 15 * 60000);
    const pts = (k, filt = () => true) => rows.filter(filt).map((r) => [new Date(r.timestamp).getTime(), r[k]]);
    const c1 = css('--series-1'), c2 = css('--series-2');
    lineChart($('#c-p'), [{ name: 'Pressure', color: c1, points: pts('pressure_bar') }], { unit: 'bar', height: 150, digits: 2, label: 'Pressure' });
    lineChart($('#c-q'), [{ name: 'Flow', color: c1, points: pts('flow_lpm') }], { unit: 'L/min', height: 150, digits: 3, label: 'Flow' });
    lineChart($('#c-i'), [{ name: 'Current', color: c1, points: pts('pump_current_a') }], { unit: 'A', height: 150, digits: 2, label: 'Current' });
    lineChart($('#c-tds'), [{ name: 'TDS in', color: c1, points: pts('tds_in') }, { name: 'TDS out', color: c2, points: pts('tds_out') }], { unit: 'ppm', height: 170, digits: 0, label: 'TDS' });
    const rej = rows.filter((r) => r.pump_on).map((r) => [new Date(r.timestamp).getTime(), (1 - r.tds_out / Math.max(r.tds_in, 1)) * 100]);
    lineChart($('#c-rej'), [{ name: 'Rejection', color: c1, points: rej, kind: 'dots' }], { unit: '%', height: 170, digits: 1, label: 'Rejection' });
    // RUL
    const comps = a.components.filter((c) => c.component !== 'sensors');
    const pick = ui.rulComp || [...comps].sort((x, y) => x.health - y.health)[0].component;
    $('#rulpick').innerHTML = `<div class="seg">${comps.map((c) => `<button data-act="rulc" data-arg="${c.component}" aria-pressed="${c.component === pick}">${c.name.split(' ')[0]}</button>`).join('')}</div>`;
    const c = comps.find((x) => x.component === pick);
    const day = 86400000, today = new Date(new Date(W.rig.t).toDateString()).getTime();
    const hist = u.history ? u.history[pick].map((h, k, arr) => [today - (arr.length - 1 - k) * day, h]) : [[today, c.health]];
    const band = c.rul && c.rul.high > 0 ? [[today, c.health], [today + c.rul.high * day, REPLACE_T], [today + c.rul.low * day, REPLACE_T], [today, c.health]] : null;
    const fc = c.rul && c.rul.high > 0 ? [[today, c.health], [today + c.rul.mid * day, REPLACE_T]] : [];
    lineChart($('#c-rul'), [{ name: 'Observed', color: c1, points: hist }, { name: 'Forecast', color: c1, points: fc, dash: '4 4' }],
      { yMin: 0, yMax: 100, digits: 0, unit: '%', height: 210, xFormat: 'date', band, hline: { y: REPLACE_T, label: 'Replacement threshold (40%)' }, label: 'RUL' });
    $('#rulnote').textContent = `${c.name}: ${c.rul && c.rul.high > 0 ? `RUL ${rulText(c.rul)} to the 40% threshold` : 'below the threshold: replace now'}. ${u.history ? 'History: daily health, last 60 days.' : 'Forecast from current health, usage and source water.'}`;
  },
  alerts() {
    $('#at').innerHTML = W.alerts.length ? `<table class="tbl"><thead><tr><th>#</th><th>Status</th><th>Unit</th><th>Fault</th><th>Conf.</th><th>Why</th><th>Raised</th><th></th></tr></thead><tbody>
      ${W.alerts.map((a) => `<tr><td>${a.id}</td><td>${a.status}</td><td><b>${a.unit}</b></td><td>${esc(a.fault_name)}</td><td>${pct(a.confidence)}</td><td class="small">${esc(a.reason)}</td><td class="small mono">${hhmmss(a.t)}</td>
        <td>${a.status === 'open' ? `<button class="btn pri" data-act="contact" data-arg="${a.unit}">Contact</button>` : ''}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">No alerts</div>';
    $('#tt').innerHTML = W.tickets.length ? `<table class="tbl"><thead><tr><th>#</th><th>Status</th><th>Unit</th><th>Fault</th><th>Part to carry</th><th>Estimate</th><th>Window</th><th></th></tr></thead><tbody>
      ${W.tickets.map((t) => `<tr><td>${t.id}</td><td>${t.status}</td><td><b>${t.unit}</b></td><td>${esc(t.fault)}</td><td>${esc(t.carry.join(', '))}</td><td>${inr(t.price)}</td><td>${t.window}</td>
        <td class="row"><button class="btn" data-act="jobcard" data-arg="${t.id}">Job card</button>${t.status !== 'done' ? `<button class="btn" data-act="tdone" data-arg="${t.id}">Mark done</button>` : ''}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">No tickets yet. Press Contact customer on an alert.</div>';
  },
  warranty() {
    const w = W.warranty();
    $('#wk').innerHTML = [['Expected repair cost (fleet)', inr(w.total)], ['Repairs due now', inr(w.now)], ['Units', w.units.length]]
      .map(([l, v]) => `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join('');
    const max = Math.max(...w.parts.map((p) => p.expected), 1);
    $('#wp').innerHTML = w.parts.map((p) => `<div class="hb" style="grid-template-columns:170px 1fr 150px"><span class="n">${p.part}</span>
      <span class="bar" title="${p.expected.toFixed(2)} expected units"><i style="width:${p.expected / max * 100}%;background:var(--series-1)"></i></span>
      <span class="v">${p.expected.toFixed(2)} · ${p.due} due · ${inr(p.expectedCost)}</span></div>`).join('') +
      '<p class="small muted" style="margin:10px 0 0">Prices: filter ₹400 · membrane ₹1,800 · pump ₹1,500 · sensor fix ₹600 · labour ₹300 · visit ₹200.</p>';
    $('#wu').innerHTML = `<table class="tbl"><thead><tr><th>Unit</th><th>Status</th><th>Replace now</th><th>Watch</th><th>Now</th><th>Expected</th></tr></thead><tbody>
      ${w.units.map(({ u, a }) => `<tr><td><b>${u.id}</b><div class="small muted">${esc(u.customer)}</div></td><td>${pill(a.status)}</td><td class="small">${a.quote.replace_now.map((l) => l.part).join(', ') || '—'}</td>
        <td class="small">${a.quote.watch_list.map((l) => l.part).join(', ') || '—'}</td><td>${inr(a.quote.repair_price_now)}</td><td>${inr(a.quote.expected_cost)}</td></tr>`).join('')}</tbody></table>`;
  },
};

// ====================================================================== handheld
V.handheld = {
  html: () => `
    <h1>Technician handheld</h1>
    <p class="sub">Raspberry Pi with a small touchscreen and a card reader. On a routine visit the technician moves the purifier's SD card into the reader;
      the same models as the cloud run on the device, fully offline, and produce a quote.</p>
    <div class="device"><div class="label">AquaSense · technician</div><div class="screen" id="hh"></div></div>
    <div class="grid g2" style="margin-top:16px;max-width:860px;margin-left:auto;margin-right:auto">
      <div class="card"><h3>Where is the SD card?</h3><div id="hhcard"></div></div>
      <div class="card"><h3>Why the quote changes</h3><p class="small muted" style="margin:0">Replace now if health &lt; 40% or failure probability within 30 days &gt; 0.6.
        Watch list at 0.2–0.6 (not billed). Price = parts now + labour ₹300 + visit ₹200. When the unit's condition changes, the quote changes with it.</p></div>
    </div>`,
  mount() { this.screen(); },
  live() {
    const n = W.sd.length;
    $('#hhcard').innerHTML = `<div class="sdslot"><span class="sdcard"></span><div><b>${W.cardInRig ? 'In the purifier' : 'In the handheld reader'}</b>
      <div class="small muted">${n} log lines on the card · ${W.backlog} never reached the cloud</div></div></div>
      <div class="row" style="margin-top:10px">${W.cardInRig ? '<button class="btn" data-act="hh-insert">Move card to handheld</button>' : '<button class="btn" data-act="return-card">Put card back in the purifier</button>'}</div>`;
    if (ui.hh.mode === 'home') this.screen();
  },
  screen() {
    const h = ui.hh, el = $('#hh');
    if (!el) return;
    if (h.mode === 'home') {
      const detected = !W.cardInRig;
      el.innerHTML = `<div style="padding:1.2em 1.4em;flex:1">
        <div style="font-size:1.9em;font-weight:800;color:#3987e5">AquaSense <span style="font-size:.45em;color:#9aa8bf;font-weight:400">technician handheld · offline</span></div>
        <div style="font-size:1.3em;font-weight:700;margin-top:1.2em">Insert the RO unit's SD card into the reader.</div>
        <div style="margin-top:.3em;color:${detected ? '#0ca30c' : '#9aa8bf'}">${detected ? 'Card found: /media/pi/AQUASENSE (AQ-1001)' : 'No AquaSense card detected yet.'}</div>
        <div style="display:flex;gap:.6em;margin-top:1.4em;flex-wrap:wrap">
          <button class="hh-btn pri" data-act="hh-scan" ${detected ? '' : 'disabled'}>Scan card</button>
          ${detected ? '' : '<button class="hh-btn" data-act="hh-insert">Insert the purifier\'s card</button>'}</div>
        <div style="position:absolute;bottom:.8em;left:1.4em;font-size:.8em;color:#9aa8bf">Everything runs on this device. No internet needed.</div></div>`;
      return;
    }
    if (h.mode === 'scanning') {
      el.innerHTML = `<div style="padding:1.6em"><div style="font-size:1.3em;font-weight:700">Reading the card and running the models…</div>
        <div class="muted" style="margin-top:.4em">AQ-1001 card · building 60 s windows · fault classifier · anomaly check · health · RUL · price</div>
        <div class="scanbar"><i id="scanbar"></i></div></div>`;
      requestAnimationFrame(() => { const b = $('#scanbar'); if (b) b.style.width = '100%'; });
      return;
    }
    const { a, rows } = h.res;
    const colour = { normal: '#0ca30c', warning: '#fab219', replace: '#d03b3b' }[a.status];
    const label = { normal: 'OK', warning: 'WATCH', replace: 'REPLACE' }[a.status];
    const tab = h.tab;
    const q = a.quote;
    let body = '';
    if (tab === 'health') body = healthBars(a.components) + '<div class="muted" style="font-size:.8em;margin-top:.4em">Dashed line = 40% replacement threshold.</div>';
    if (tab === 'fault') body = `<div style="font-size:1.3em;font-weight:700">${esc(a.fault_name)}</div><div class="muted">Confidence ${pct(a.confidence)}</div>
      <p>${esc(a.reason)}</p>${a.evidence.slice(0, 4).map((e) => `<div>• ${esc(e)}</div>`).join('')}<div class="muted" style="margin-top:.6em;font-size:.85em">Signals that drove the model: ${a.drivers.join(', ')}</div>`;
    if (tab === 'quote') body = `${q.replace_now.length ? '' : '<div style="color:#0ca30c">No part needs replacing now.</div>'}
      ${q.replace_now.map((l) => `<div class="qrow"><b>Replace: ${l.part}</b><span>${inr(l.cost)}</span></div>`).join('')}
      ${q.labour ? `<div class="qrow"><span>Labour</span><span>${inr(q.labour)}</span></div>` : ''}<div class="qrow"><span>Visit charge</span><span>${inr(q.visit_charge)}</span></div>
      <div class="qrow" style="font-size:1.3em;font-weight:800;margin-top:.4em"><span>Repair price now</span><span style="color:#3987e5">${inr(q.repair_price_now)}</span></div>
      ${q.watch_list.length ? `<div class="muted" style="font-size:.85em;margin-top:.4em">Watch list (not billed): ${q.watch_list.map((l) => `${l.part} p=${l.failure_prob.toFixed(2)}`).join('; ')}</div>` : ''}
      <div class="muted" style="font-size:.85em;margin-top:.3em">Expected cost for warranty planning: ${inr(q.expected_cost)}</div>`;
    if (tab === 'unit') {
      const last = rows[rows.length - 1];
      body = `<div class="muted" style="font-size:.9em;line-height:1.7">Unit: AQ-1001<br>Card: /media/pi/AQUASENSE<br>
        Data analysed: ${rows[0].timestamp.replace('T', ' ')} → ${last.timestamp.replace('T', ' ')} (${rows.length} rows)<br>
        TDS in ${Math.round(a.tds_in)} ppm · rejection ${a.rejection.toFixed(1)}%<br>Pump ${(last.pump_runtime_s / 3600).toFixed(1)} h · ${last.pump_cycles} starts · UV ${last.uv_hours.toFixed(1)} h · filter ${last.filter_hours.toFixed(1)} h<br>
        Models: AquaSense v1.0 (same as the cloud)</div>`;
    }
    el.innerHTML = `<div class="hh-head"><span class="hh-pill" style="background:${colour};color:${a.status === 'warning' ? '#000' : '#fff'}">${label}</span>
      <b style="font-size:1.15em">${esc(a.fault_name)} · ${pct(a.confidence)}</b><span style="margin-left:auto;color:#9aa8bf">AQ-1001</span></div>
      <div class="hh-tabs">${['health', 'fault', 'quote', 'unit'].map((t) => `<button data-act="hh-tab" data-arg="${t}" aria-pressed="${t === tab}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}</div>
      <div class="hh-body">${body}</div>
      <div class="hh-foot"><button class="hh-btn pri" data-act="hh-save">Save quote</button><button class="hh-btn" data-act="hh-rescan">Rescan</button><button class="hh-btn" data-act="hh-done">Done</button></div>`;
  },
};

function hhScan(source) {
  ui.hh.source = source;
  ui.hh.mode = 'scanning';
  V.handheld.screen();
  setTimeout(() => {
    const rows = source === 'demo' ? demoCardRows() : W.cardRows(30);
    const res = analyseCard(rows);
    if (!res) { ui.hh.mode = 'home'; toast('Not enough pump-on data on the card yet'); V.handheld.screen(); return; }
    ui.hh.res = res;
    ui.hh.mode = 'result';
    ui.hh.tab = res.a.quote.replace_now.length ? 'quote' : 'health';
    if (ui.route === 'handheld') V.handheld.screen();
    tourCheck();
  }, 1600);
}
function quoteText(res) {
  const { a, rows } = res, q = a.quote;
  return ['AQUASENSE SERVICE QUOTE (technician handheld, offline)', '='.repeat(50), `Date        : ${new Date().toLocaleString('en-IN')}`, 'Unit        : AQ-1001',
    `Data on card: ${rows[0].timestamp} to ${rows[rows.length - 1].timestamp} (${rows.length} rows)`, '',
    `Most likely fault: ${a.fault_name} (${pct(a.confidence)} confidence)`, `Why: ${a.reason}`, ...a.evidence.map((e) => `  - ${e}`), '', 'Component health',
    ...a.components.map((c) => `  ${c.name.padEnd(16)}${String(Math.round(c.health)).padStart(5)}%   ${c.rul ? 'RUL ' + rulText(c.rul) : ''}`), '', 'Replace now',
    ...(q.replace_now.length ? q.replace_now.map((l) => `  ${l.part.padEnd(28)}${inr(l.cost).padStart(9)}`) : ['  (nothing)']),
    ...(q.labour ? [`  ${'Labour'.padEnd(28)}${inr(q.labour).padStart(9)}`] : []), `  ${'Visit charge'.padEnd(28)}${inr(q.visit_charge).padStart(9)}`,
    `  ${'REPAIR PRICE NOW'.padEnd(28)}${inr(q.repair_price_now).padStart(9)}`, '', `Expected cost for warranty planning: ${inr(q.expected_cost)}`].join('\n');
}

// ====================================================================== customer app
V.app = {
  html: () => `
    <h1>Customer app</h1>
    <p class="sub">Customers see their purifier like a connected-car tracker and book service in one tap; technicians see their jobs with the part to carry.</p>
    <div class="grid g2" style="align-items:start">
      <div class="phone"><div class="ps"><div class="notch">9:41 · AquaSense</div><div class="pc" id="pc"></div>
        <div class="ptabs"><button data-act="ptab" data-arg="purifier" aria-pressed="${ui.phone.tab === 'purifier'}">💧<br>My purifier</button><button data-act="ptab" data-arg="jobs" aria-pressed="${ui.phone.tab === 'jobs'}">🔧<br>Technician</button></div></div></div>
      <div class="card"><h2>What the app does</h2><ul style="padding-left:18px;color:var(--ink-2)">
        <li>Live status, component health with the 40% threshold and estimated life left</li><li>Alert with the reason in plain words</li>
        <li>Estimated service cost from the same pricing as the handheld</li><li><b>Book service</b> creates a ticket and job card in the console</li>
        <li>Technician tab: jobs, part to carry, job card, mark done</li></ul>
      </div>
    </div>`,
  live() {
    const p = ui.phone, el = $('#pc');
    if (!el) return;
    if (p.tab === 'jobs') {
      el.innerHTML = `<h2 style="margin:6px 0">Service jobs</h2><p class="small muted" style="margin-top:0">Each job card names the likely fault and the part to carry.</p>
        ${W.tickets.map((t) => `<div class="card"><div class="row between"><b>#${t.id} · ${t.unit}</b><span class="small muted">${t.status.toUpperCase()}</span></div>
          <div>${esc(t.fault)}</div><div class="small muted">${esc(W.units[t.unit].customer)} · ${t.window}</div><div class="small muted">Carry: ${esc(t.carry.join(', '))} · ${inr(t.price)}</div>
          <div class="row" style="margin-top:8px"><button class="btn" data-act="jobcard" data-arg="${t.id}">Job card</button>${t.status !== 'done' ? `<button class="btn pri" data-act="tdone" data-arg="${t.id}">Done</button>` : ''}</div></div>`).join('') || '<div class="muted small">No jobs yet. Book service on the other tab, or press Contact customer in the console.</div>'}`;
      return;
    }
    const u = W.units[p.unit], a = W.assessment(p.unit);
    el.innerHTML = `<div style="font-size:22px;font-weight:800;color:var(--accent);margin:4px 0 8px">AquaSense <span style="font-size:13px;color:var(--ink-3);font-weight:400">· My purifier</span></div>
      <div class="unitchips">${Object.values(W.units).map((x) => `<button data-act="punit" data-arg="${x.id}" aria-pressed="${x.id === p.unit}">${x.id}</button>`).join('')}</div>
      <div class="card"><div class="row between"><b style="font-size:17px">${esc(u.name)}</b><span class="small muted">${u.simulated || W.wifi ? '● CONNECTED' : '○ OFFLINE'}</span></div>
        <div class="small muted">${esc(u.customer)} · ${esc(u.city)} · ${u.source} water</div>
        <div style="margin-top:8px">${pill(a.status)}</div><div style="font-weight:600;margin-top:6px">${a.fault === 'normal' ? 'All components healthy' : esc(a.fault_name)}</div>
        <div class="small muted">Source water ${Math.round(a.tds_in)} ppm · rejection ${a.rejection.toFixed(1)}%</div></div>
      <div class="card"><b>Component health</b>${healthBars(a.components, { lifeLabel: 'Est. life left' })}</div>
      ${a.fault !== 'normal' ? `<div class="card" style="border-left:5px solid var(--warn)"><b>Alert: ${esc(a.fault_name)}</b><div class="small">${esc(a.reason)}</div></div>` : ''}
      <div class="card"><b>Estimated service cost</b>
        ${a.quote.replace_now.length ? a.quote.replace_now.map((l) => `<div class="row between small"><span>${l.part}</span><span>${inr(l.cost)}</span></div>`).join('') +
          `<div class="row between small muted"><span>Labour + visit</span><span>${inr(a.quote.labour + a.quote.visit_charge)}</span></div><div class="row between"><b>Total</b><b>${inr(a.quote.repair_price_now)}</b></div>`
          : `<div class="small muted">No part needs replacing now. Routine visit ${inr(a.quote.visit_charge)}</div>`}
        <button class="btn pri" style="width:100%;margin-top:10px" data-act="book" data-arg="${u.id}">Book service</button>
        ${p.booked && p.booked.unit === u.id ? `<div class="small muted" style="margin-top:6px">Booked: ticket #${p.booked.id} · ${p.booked.window} · technician brings ${esc(p.booked.carry.join(', '))}</div>` : ''}</div>`;
  },
};

// ====================================================================== models
V.models = {
  html: () => {
    const raw = [[27, 0, 0, 0, 15], [0, 39, 0, 0, 0], [0, 0, 40, 0, 0], [0, 0, 0, 29, 0], [2, 0, 2, 0, 27]];
    const full = [[42, 0, 0, 0, 0], [0, 39, 0, 0, 0], [0, 0, 40, 0, 0], [0, 0, 0, 29, 0], [0, 0, 0, 0, 31]];
    const names = ['Normal', 'Clog', 'Pump', 'Membrane', 'Sensor'];
    const cm = (m) => `<div class="tblwrap"><table class="tbl heat"><thead><tr><th>true \\ pred</th>${names.map((n) => `<th>${n}</th>`).join('')}</tr></thead><tbody>
      ${m.map((r, i) => { const tot = r.reduce((a, b) => a + b, 0); return `<tr><th>${names[i]}</th>${r.map((v, j) => `<td class="c" style="background:${v ? `color-mix(in srgb, ${i === j ? 'var(--series-1)' : 'var(--bad)'} ${Math.round(12 + 60 * v / tot)}%, transparent)` : 'transparent'}">${v}</td>`).join('')}</tr>`; }).join('')}</tbody></table></div>`;
    return `
    <h1>Diagnosis models</h1>
    <p class="sub">Trained centrally and shipped as versioned files to the cloud and every handheld, so the diagnosis is the same everywhere.</p>
    <div class="grid g4" style="margin-bottom:16px">
      <div class="card kpi"><div class="l">Macro F1, model + cross-check</div><div class="v">1.00</div><div class="small muted">16 held-out runs</div></div>
      <div class="card kpi"><div class="l">Macro F1, model only</div><div class="v">0.90</div><div class="small muted">before the cross-check</div></div>
      <div class="card kpi"><div class="l">High-TDS false alarms</div><div class="v">0%</div><div class="small muted">1500 ppm normal water</div></div>
      <div class="card kpi"><div class="l">Training data</div><div class="v">45 runs</div><div class="small muted">9,959 rows · 548 windows</div></div>
    </div>
    <div class="grid g2" style="margin-bottom:16px">
      <div class="card"><h2>Confusion matrix · model only</h2>${cm(raw)}<p class="small muted">The classifier alone confuses some normal windows with sensor faults.</p></div>
      <div class="card"><h2>Confusion matrix · with cross-check</h2>${cm(full)}<p class="small muted">A sensor fault is raised only for a physical impossibility or one lone abnormal signal.</p></div>
    </div>
    <div class="card" style="margin-bottom:16px"><h2>The models</h2><div class="tblwrap"><table class="tbl"><thead><tr><th>Model</th><th>Algorithm</th><th>Job</th></tr></thead><tbody>
      <tr><td>A. Fault classifier</td><td>XGBoost (Random Forest fallback)</td><td>normal / clogging / pump / membrane / sensor</td></tr>
      <tr><td>B. Anomaly detector</td><td>Isolation Forest on normal data</td><td>unusual behaviour; separates sensor faults from wear</td></tr>
      <tr><td>C. Health score</td><td>XGBoost regressor per component</td><td>100 healthy; 77 / 53 / 30 at fault-panel levels 1 / 2 / 3</td></tr>
      <tr><td>D. RUL forecast</td><td>Quantile gradient boosting (5 / 50 / 95%)</td><td>days to the 40% threshold, as a range</td></tr>
      <tr><td>Explanations</td><td>TreeSHAP</td><td>which signals drove the decision</td></tr></tbody></table></div></div>
    <div class="grid g2">
      <div class="card"><h2>Features (per 60 s window)</h2><p class="small" style="color:var(--ink-2)">Mean, standard deviation and slope of pressure, flow, pump current, rejection % and
        pressure per amp in steady state, plus build-up time, start-up current, TDS noise, and pressure/flow while the pump is off. Rows are averaged into 10 s bins and the
        10 s after each pump start are kept apart, so 2 s training logs and 5 s field logs give identical features.</p></div>
      <div class="card"><h2>Evaluation rules</h2><ul class="small" style="color:var(--ink-2);padding-left:18px;margin:0">
        <li>Split by run, never by row: whole runs held out</li><li>Confusion matrix and F1 per class on held-out runs</li>
        <li>High-TDS normal water tested explicitly</li><li>RUL validated on run-to-failure sequences</li></ul></div>
    </div>`;
  },
};

// ====================================================================== guided tours
const TOURS = {
  s1: { title: 'Scenario 1 · Company notices', steps: [
    { route: 'rig', title: 'Normal operation', text: 'The purifier runs; the display and the cloud show live data and a healthy status.', act: () => { W.setFault({ clog: 0, pump: 0, membrane: 0, sensor: null, tds: 300 }); W.setWifi(true); W.speed = 10; } },
    { route: ['console', 'unit'], title: 'Source water changes', text: 'The household switches to borewell water (1500 ppm). TDS in jumps, rejection stays ~95%, and there is no false alarm.', act: () => W.setFault({ tds: 1500 }), wait: (s) => W.rig.t - s.t0 > 90000, waitText: 'Watching 90 s of salty water…' },
    { route: ['console', 'unit'], title: 'The filter starts clogging', text: 'Flow falls and pressure rises. After three confident 60 s windows the console raises an alert.', act: () => W.setFault({ clog: 2 / 3 }), wait: () => W.alerts.some((a) => a.unit === 'AQ-1001' && a.fault === 'clogging'), waitText: 'Waiting for 3 confident windows…' },
    { route: ['console', 'unit'], title: 'Contact customer', text: 'One press creates a ticket and a job card listing the likely fault and the part to carry. The handheld is not needed.', act: () => openJobCard(W.contact('AQ-1001')) },
    { route: 'rig', title: 'Done', text: 'The filter is replaced and the unit is healthy again. Try Scenario 2, or use service mode on the purifier page.', act: () => { closeModal(); W.setFault({ clog: 0, tds: 300 }); } },
  ] },
  s2: { title: 'Scenario 2 · Routine visit', steps: [
    { route: 'rig', title: 'Wi-Fi goes down', text: 'The purifier keeps logging to its SD card. Watch the orange lines pile up: they never reach the cloud.', act: () => { W.setFault({ clog: 0, pump: 0, membrane: 0, sensor: null, tds: 800 }); W.setWifi(false); W.speed = 20; } },
    { route: 'rig', title: 'The booster pump weakens', text: 'Pressure drops, current changes, build-up slows. Only the SD card knows.', act: () => W.setFault({ pump: 2 / 3 }), wait: (s) => W.rig.t - s.t0 > 180000, waitText: 'Logging 3 minutes to the SD card…' },
    { route: ['console', 'unit'], title: 'The console sees nothing new', text: 'The unit is offline. No alert, no ticket: this is the routine-visit case.' },
    { route: 'handheld', title: 'Card into the handheld', text: 'The technician moves the SD card into the reader. The models run offline: pump degradation, RUL range and a quote of ₹2,000.', act: () => { pullCard(); hhScan('card'); }, wait: () => ui.hh.mode === 'result', waitText: 'Scanning…' },
    { route: 'rig', title: 'A different visit', text: 'Card back in the purifier. This time the pump is fine but the RO membrane is worn.', act: () => { returnCard(); W.setFault({ pump: 0, membrane: 1 }); }, wait: (s) => W.rig.t - s.t0 > 240000, waitText: 'Logging 4 minutes…' },
    { route: 'handheld', title: 'Rescan: the quote moves', text: 'New condition, new quote: the handheld now quotes an RO membrane, ₹2,300.', act: () => { pullCard(); hhScan('card'); }, wait: () => ui.hh.mode === 'result', waitText: 'Scanning…' },
    { route: ['console', 'unit'], title: 'Wi-Fi back', text: 'The card goes back, Wi-Fi returns and the backlog uploads in batches. The cloud scores it in time order and catches up.', act: () => { returnCard(); W.setFault({ membrane: 0 }); W.setWifi(true); }, wait: () => W.backlog <= 2, waitText: 'Uploading the SD backlog…' },
  ] },
};
const tour = { id: null, i: 0, t0: 0, waiting: false };
function startTour(id) { tour.id = id; tour.i = -1; nextStep(); }
function nextStep() {
  const T = TOURS[tour.id];
  tour.i++;
  if (tour.i >= T.steps.length) { endTour(); return; }
  const s = T.steps[tour.i];
  const r = Array.isArray(s.route) ? s.route : [s.route];
  if (r[0] === 'console') { ui.console.sub = r[1] || 'fleet'; ui.console.unit = 'AQ-1001'; }
  go(r[0]);
  s.act?.();
  tour.t0 = W.rig.t;
  tour.waiting = !!s.wait;
  renderTour();
  renderSimbar();
}
function tourCheck() {
  if (!tour.id || !tour.waiting) return;
  const s = TOURS[tour.id].steps[tour.i];
  if (s.wait({ t0: tour.t0 })) { tour.waiting = false; renderTour(); }
}
function endTour() { tour.id = null; $('#tour').hidden = true; }
function renderTour() {
  const T = TOURS[tour.id], s = T.steps[tour.i], el = $('#tour');
  el.hidden = false;
  const last = tour.i === T.steps.length - 1;
  el.innerHTML = `<div class="row between"><b>${T.title}</b><button class="btn" data-act="tour-end" aria-label="Close tour">✕</button></div>
    <div class="steps">${T.steps.map((_, k) => `<i class="${k <= tour.i ? 'done' : ''}"></i>`).join('')}</div>
    <div class="small muted">Step ${tour.i + 1} of ${T.steps.length}</div><h3 style="margin:2px 0 4px">${s.title}</h3><p class="small" style="margin:0 0 12px">${s.text}</p>
    ${tour.waiting ? `<div class="wait"><span class="spinner"></span>${s.waitText}</div>` : `<button class="btn pri" data-act="tour-next">${last ? 'Finish' : 'Next →'}</button>`}`;
}
function tourMenu() {
  modal(`<h2>Take the tour</h2><p class="muted">Each tour follows a real service case across the purifier, the console, the handheld and the app. About 2 minutes each.</p>
    <div class="grid g2"><button class="btn pri big" data-act="tour" data-arg="s1">Scenario 1 · Company notices</button><button class="btn big" data-act="tour" data-arg="s2">Scenario 2 · Routine visit</button></div>
    <div class="row" style="margin-top:14px"><button class="btn" data-act="close">Close</button></div>`);
}

// ====================================================================== actions
function pullCard() { if (W.cardInRig) { W.cardInRig = false; W.note('SD card moved from the purifier to the handheld reader'); } ui.hh.inReader = true; }
function returnCard() { if (!W.cardInRig) { W.cardInRig = true; W.note('SD card back in the purifier: logging resumes'); } ui.hh.mode = 'home'; ui.hh.inReader = false; }
function openJobCard(t) {
  modal(`<div class="row between"><h2 style="margin:0">Job card #${t.id}</h2><button class="btn" data-act="close">✕</button></div>
    <p class="small muted">Carry: <b>${esc(t.carry.join(', '))}</b> · estimate ${inr(t.price)} · ${t.window}</p><pre>${esc(t.card)}</pre>
    <div class="row"><button class="btn pri" data-act="dl-job" data-arg="${t.id}">Download</button><button class="btn" data-act="close">Close</button></div>`);
}
const ACTIONS = {
  go: (a) => go(a),
  pause: () => { W.paused = !W.paused; renderSimbar(); },
  reset: () => { endTour(); closeModal(); W.reset(); ui.hh = { mode: 'home', tab: 'health', res: null, source: null }; ui.phone.booked = null; render(); toast('Reset'); },
  theme: () => {
    const d = document.documentElement, dark = d.dataset.theme ? d.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    d.dataset.theme = dark ? 'light' : 'dark';
    try { localStorage.setItem('aq-theme', d.dataset.theme); } catch { /* private mode */ }
    renderSimbar(); liveNow();
  },
  fault: (a) => { W.setFault(JSON.parse(a)); },
  wifi: (a) => W.setWifi(a === '1'),
  'pull-card': () => { pullCard(); go('handheld'); },
  'return-card': () => { returnCard(); refreshStatic(); },
  csub: (a) => { ui.console.sub = a; V.console.skeleton(); },
  'open-unit': (a) => { ui.console.unit = a; ui.console.sub = 'unit'; ui.rulComp = null; V.console.skeleton(); },
  rulc: (a) => { ui.rulComp = a; V.console.unit(); },
  contact: (a) => { const t = W.contact(a); openJobCard(t); toast(`Ticket #${t.id}: technician will carry ${t.carry.join(', ')}`); },
  ack: (a) => { const al = W.alerts.find((x) => x.id === Number(a)); if (al) al.status = 'acknowledged'; liveNow(); },
  jobcard: (a) => openJobCard(W.tickets.find((t) => t.id === Number(a))),
  'dl-job': (a) => { const t = W.tickets.find((x) => x.id === Number(a)); download(`jobcard_${t.id}.txt`, t.card); },
  tdone: (a) => { W.setTicket(W.tickets.find((t) => t.id === Number(a)), 'done'); },
  'hh-insert': () => { pullCard(); V.handheld.screen(); liveNow(); },
  'hh-scan': () => hhScan('card'),
  'hh-demo': () => hhScan('demo'),
  'hh-rescan': () => hhScan(ui.hh.source || 'card'),
  'hh-tab': (a) => { ui.hh.tab = a; V.handheld.screen(); },
  'hh-save': () => { download(`quote_AQ-1001_${Date.now()}.txt`, quoteText(ui.hh.res)); toast('Quote saved'); },
  'hh-done': () => { ui.hh.mode = 'home'; V.handheld.screen(); },
  ptab: (a) => { ui.phone.tab = a; document.querySelectorAll('.ptabs button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.arg === a)); V.app.live(); },
  punit: (a) => { ui.phone.unit = a; V.app.live(); },
  book: (a) => { ui.phone.booked = W.contact(a, 'Booked by the customer from the app'); toast(`Service booked: ticket #${ui.phone.booked.id}`); V.app.live(); },
  tour: (a) => { closeModal(); startTour(a); },
  'tour-menu': () => tourMenu(),
  'tour-next': () => nextStep(),
  'tour-end': () => endTour(),
  close: () => closeModal(),
};
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-act]');
  if (t && !t.disabled) { ACTIONS[t.dataset.act]?.(t.dataset.arg, t); return; }
  if (e.target.id === 'modal') closeModal();
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
document.addEventListener('change', (e) => { if (e.target.id === 'speed') { W.speed = Number(e.target.value); } });

// ====================================================================== routing + loop
function go(r) { if (location.hash !== `#${r}`) location.hash = r; else render(); }
function render() {
  ui.route = (location.hash.slice(1) || 'overview');
  if (!V[ui.route]) ui.route = 'overview';
  renderNav();
  renderSimbar();
  const v = V[ui.route];
  $('#view').innerHTML = v.html();
  if (ui.route === 'rig') v.panel();
  v.mount?.();
  v.live?.();
  window.scrollTo({ top: 0 });
}
function refreshStatic() { if (ui.route === 'rig') V.rig.panel(); liveNow(); renderSimbar(); }
function liveNow() { V[ui.route].live?.(); const c = $('#clock'); if (c) c.textContent = hhmmss(W.rig.t); renderNav(); }

window.addEventListener('hashchange', render);
W.on((ev, data) => {
  if (ev === 'alert') toast(`⚠ Alert: ${data.unit} · ${data.fault_name} (${pct(data.confidence)})`, 'alert');
  if (ev === 'change') { if (ui.route === 'rig') V.rig.panel(); renderSimbar(); }
});

try { const t = localStorage.getItem('aq-theme'); if (t) document.documentElement.dataset.theme = t; } catch { /* ignore */ }
render();
// never replace a button under the pointer mid-click
let pointerDown = false;
document.addEventListener('pointerdown', () => { pointerDown = true; });
document.addEventListener('pointerup', () => setTimeout(() => { pointerDown = false; }, 120));
let last = performance.now(), acc = 0;
setInterval(() => {
  const now = performance.now(), dt = now - last;
  last = now;
  W.tick(dt);
  acc += dt;
  if (acc >= 900 && !pointerDown) { acc = 0; liveNow(); tourCheck(); }
}, 200);
void FAULT_NAME; void COMP_NAME;
