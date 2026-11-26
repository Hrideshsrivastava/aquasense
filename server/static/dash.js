// AquaSense company dashboard: real-time over WebSocket.
import { lineChart } from './charts.js';

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? '–' : Number(v).toFixed(d));
const ago = (ts) => { if (!ts) return 'never'; const s = Math.max(0, Date.now() / 1000 - ts); return s < 60 ? `${s | 0}s ago` : s < 3600 ? `${(s / 60) | 0} min ago` : `${(s / 3600) | 0} h ago`; };
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });

const STATUS = {
  normal: 'Healthy', learning: 'Learning baseline', idle: 'Idle', suspect: 'Suspected fault', alert: 'Alert',
  service: 'Service in progress', offline: 'Offline',
};
const JOB = { new: 'New', assigned: 'Assigned', en_route: 'On the way', on_site: 'On site', confirmed: 'Confirmed', no_fault: 'No fault found', resolved: 'Resolved', cancelled: 'Cancelled' };
const COMP = { pump: 'Booster pump', membrane: 'RO membrane', filter: 'Filter / line', prefilter: 'Sediment pre-filter', sensors: 'Sensors' };
const hcol = (h) => (h < 40 ? 'var(--bad)' : h < 70 ? 'var(--warn)' : 'var(--good)');

const S = {
  token: localStorage.getItem('aq-token'), user: null, devices: {}, readings: {}, jobs: {}, alerts: [], technicians: [], customers: [],
  ws: null, wsUp: false, feed: [], dirty: true,
};

// ------------------------------------------------------------------ api
async function api(path, method = 'GET', body) {
  const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${S.token}` }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (r.status === 401) { logout(); throw new Error(j.error || 'Signed out'); }
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(text, kind = '') {
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  t.textContent = text;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), 4500);
}
function feed(text) { S.feed.unshift({ t: Date.now() / 1000, text }); S.feed = S.feed.slice(0, 60); }

// ------------------------------------------------------------------ websocket
function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${S.token}`);
  S.ws = ws;
  ws.onopen = () => { S.wsUp = true; renderTop(); };
  ws.onclose = () => { S.wsUp = false; renderTop(); if (S.token) setTimeout(connect, 2000); };
  ws.onmessage = (e) => onEvent(JSON.parse(e.data));
}
function onEvent(m) {
  if (m.type === 'error') { logout(); return; }
  if (m.type === 'snapshot') {
    S.user = m.user;
    S.devices = Object.fromEntries(m.devices.map((d) => [d.id, d]));
    S.jobs = Object.fromEntries(m.jobs.map((j) => [j.id, j]));
    S.alerts = m.alerts; S.technicians = m.technicians; S.customers = m.customers;
    render();
    return;
  }
  if (m.type === 'reading') {
    const arr = (S.readings[m.device_id] ||= []);
    arr.push(m.reading);
    if (arr.length > 600) arr.splice(0, arr.length - 600);
    const d = S.devices[m.device_id];
    if (d) { d.latest = m.reading; d.online = true; d.last_seen = m.reading.ts; }
  }
  if (m.type === 'device') {
    const prev = S.devices[m.device.id];
    if (!prev) { toast(`New device connected: ${m.device.id}`); feed(`New device ${m.device.id} connected`); }
    S.devices[m.device.id] = m.device;
  }
  if (m.type === 'alert') {
    S.alerts.unshift(m.alert);
    toast(`⚠ ${m.device_name}: ${m.alert.label} (${Math.round(m.alert.score * 100)}%)`, 'alert');
    feed(`Alert on ${m.alert.device_id}: ${m.alert.label}`);
  }
  if (m.type === 'job') {
    const old = S.jobs[m.job.id];
    S.jobs[m.job.id] = m.job;
    if (!old) feed(`Job #${m.job.id} for ${m.job.device_id} → ${m.job.technician?.name || 'unassigned'}`);
    else if (old.status !== m.job.status) {
      feed(`Job #${m.job.id}: ${JOB[old.status]} → ${JOB[m.job.status]}`);
      if (m.job.status === 'confirmed' || m.job.status === 'no_fault') toast(`Job #${m.job.id}: technician ${m.job.status === 'confirmed' ? 'confirmed' : 'found no fault'} on site`);
    }
  }
  S.dirty = true;
}

// ------------------------------------------------------------------ shell
function logout() {
  localStorage.removeItem('aq-token');
  S.token = null; S.user = null;
  try { S.ws?.close(); } catch { /* ignore */ }
  render();
}
function renderTop() {
  if (!S.user) { $('#nav').innerHTML = ''; $('#topbar').innerHTML = ''; return; }
  const open = Object.values(S.jobs).filter((j) => ['new', 'assigned', 'en_route', 'on_site', 'confirmed'].includes(j.status)).length;
  const route = location.hash.split('/')[0] || '#fleet';
  $('#nav').innerHTML = [['#fleet', 'Fleet'], ['#jobs', `Jobs${open ? `<span class="badge">${open}</span>` : ''}`], ['#people', 'Team & customers']]
    .map(([h, l]) => `<a href="${h}" ${route === h || (h === '#fleet' && route === '#device') ? 'aria-current="page"' : ''}>${l}</a>`).join('');
  $('#topbar').innerHTML = `<span class="chip ${S.wsUp ? 'live' : 'off'}"><span class="dot"></span>${S.wsUp ? 'Live' : 'Reconnecting…'}</span>
    <span class="small muted">${esc(S.user.name)}</span><button class="btn" data-act="logout">Sign out</button>`;
}

function render() {
  renderTop();
  if (!S.token) return renderLogin();
  if (!S.user) { $('#view').innerHTML = '<div class="empty">Connecting…</div>'; if (!S.ws || S.ws.readyState > 1) connect(); return; }
  const [route, arg] = (location.hash || '#fleet').slice(1).split('/');
  S.route = route; S.arg = arg;
  if (route === 'device' && S.devices[arg]) { renderDevice(arg); }
  else if (route === 'jobs') { $('#view').innerHTML = '<h1>Jobs</h1><p class="sub">Created automatically when the quick check raises an alert, or by customers and the service desk. Technicians confirm on site with the full diagnosis on their phone.</p><div id="board"></div>'; live(); }
  else if (route === 'people') { renderPeople(); }
  else { renderFleet(); }
  S.dirty = false;
}

function renderLogin() {
  $('#view').innerHTML = `<div class="card login"><h1>Service console</h1><p class="sub">Sign in to the AquaSense company server.</p>
    <form id="loginf"><div class="field"><label for="u">Username</label><input id="u" autocomplete="username" value="admin"></div>
    <div class="field"><label for="p">PIN</label><input id="p" type="password" inputmode="numeric" autocomplete="current-password"></div>
    <button class="btn pri big" style="width:100%">Sign in</button><p class="err" id="lerr"></p></form></div>`;
  $('#loginf').onsubmit = async (e) => {
    e.preventDefault();
    const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: $('#u').value, pin: $('#p').value }) });
    const j = await r.json();
    if (!r.ok) { $('#lerr').textContent = j.error; return; }
    if (j.user.role !== 'admin') { $('#lerr').textContent = 'Technicians and customers use the AquaSense app.'; return; }
    S.token = j.token; localStorage.setItem('aq-token', j.token);
    connect(); render();
  };
}

// ------------------------------------------------------------------ fleet
const pill = (s) => `<span class="pill ${s}">${STATUS[s] || s}</span>`;
function renderFleet() {
  $('#view').innerHTML = `<div class="row between"><h1>Fleet</h1><span class="small muted">Every purifier connected to this server, live.</span></div>
    <div class="grid g4" id="kpi" style="margin:14px 0 16px"></div><div id="unassigned"></div><div class="devgrid" id="devs"></div>
    <div class="card" style="margin-top:16px"><h2>Activity</h2><div class="feed" id="feed"></div></div>`;
  live();
}
function fleetLive() {
  const ds = Object.values(S.devices).sort((a, b) => a.id.localeCompare(b.id));
  const jobs = Object.values(S.jobs);
  $('#kpi').innerHTML = [
    ['Devices online', `${ds.filter((d) => d.online).length} / ${ds.length}`],
    ['Need attention', ds.filter((d) => ['suspect', 'alert', 'service'].includes(d.status)).length],
    ['Open jobs', jobs.filter((j) => ['new', 'assigned', 'en_route', 'on_site', 'confirmed'].includes(j.status)).length],
    ['Confirmed on site', jobs.filter((j) => j.report).length],
  ].map(([l, v]) => `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join('');
  const un = ds.filter((d) => !d.customer);
  $('#unassigned').innerHTML = un.length ? `<div class="infobox" style="margin-bottom:14px"><b>${un.length} new device${un.length > 1 ? 's' : ''} without a customer:</b>
    ${un.map((d) => `<a href="#device/${d.id}">${d.id}</a>`).join(', ')}. Open a device to assign it.</div>` : '';
  $('#devs').innerHTML = ds.map((d) => {
    const r = d.latest || {};
    const hyp = d.hypothesis && !['normal', 'idle'].includes(d.hypothesis.fault) ? `${esc(d.hypothesis.label)} · ${Math.round(d.hypothesis.score * 100)}%` : (d.online ? 'All signals normal' : `Last seen ${ago(d.last_seen)}`);
    const worst = Object.entries(d.health || {}).sort((a, b) => a[1] - b[1])[0] || ['pump', 100];
    return `<a class="card dev" href="#device/${d.id}" style="text-decoration:none;color:inherit">
      <div class="row between"><b>${esc(d.name)}</b>${pill(d.status)}</div>
      <div class="small muted">${d.id} · ${esc(d.customer?.name || 'unassigned')}</div>
      <div class="small" style="margin-top:6px">${hyp}</div>
      <div class="vals"><div><b>${fmt(r.flow_rate_Lmin, 2)}</b><span>flow L/min</span></div><div><b>${fmt(r.pump_current_A, 2)}</b><span>pump A</span></div><div><b>${fmt(r.tds_out_ppm, 0)}</b><span>TDS out</span></div></div>
      <div class="small muted" style="margin-top:8px">${worst[1] >= 95 ? 'All components healthy' : `Weakest: ${COMP[worst[0]]} <span class="minibar"><i style="width:${worst[1]}%;background:${hcol(worst[1])}"></i></span>${Math.round(worst[1])}%`}</div>
      ${d.job ? `<div class="small" style="margin-top:6px">🔧 Job #${d.job.id} · ${JOB[d.job.status]} · ${esc(d.job.technician?.name || '')}</div>` : ''}</a>`;
  }).join('') || '<div class="empty">No devices yet. Power an ESP32 pointed at this server, or run <code>python -m server.device_sim</code>.</div>';
  $('#feed').innerHTML = S.feed.map((f) => `<div><time>${hhmm(f.t)}</time>${esc(f.text)}</div>`).join('') || '<div class="muted small">Waiting for activity…</div>';
}

// ------------------------------------------------------------------ device detail
const FAULT_ROWS = [
  ['clog', 'Filter / line clogging', 'flow falls, pressure rises'],
  ['pump', 'Booster pump weakness', 'pressure falls, current changes'],
  ['membrane', 'RO membrane wear', 'TDS out rises'],
  ['turbidity', 'Pre-filter exhausted', 'water turns cloudy'],
];
async function renderDevice(id) {
  const d = S.devices[id];
  $('#view').innerHTML = `<div class="row" style="margin-bottom:8px"><a href="#fleet">← Fleet</a></div>
    <div class="split"><div class="stack"><div class="card" id="dh"></div><div class="card"><h2>Component health <span class="small muted">(quick check)</span></h2><div id="dhb"></div></div>
      <div class="card" id="djob"></div></div>
    <div class="stack"><div class="card"><div class="row between"><h2>Fault injection</h2><span id="applied"></span></div>
      <p class="small muted" style="margin-top:-4px">Pushed to the device in its next server reply; the ESP32 applies it to its readings and its Bluetooth stream.</p><div id="faults"></div></div>
      <div class="card"><h2>Device</h2><form id="devf"><div class="grid g2">
        <div class="field"><label for="dn">Name</label><input id="dn" value="${esc(d.name === d.id ? '' : d.name)}" placeholder="Kitchen RO"></div>
        <div class="field"><label for="dc">Customer</label><select id="dc"><option value="">— unassigned —</option>${S.customers.map((c) => `<option value="${c.id}" ${d.customer?.id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></div></div>
        <div class="field"><label for="da">Address</label><input id="da" value="${esc(d.address || '')}"></div>
        <button class="btn pri">Save</button> <span class="small muted" id="devsaved"></span></form>
        <div id="dbase" style="margin-top:12px"></div></div></div></div>
    <h2 style="margin:20px 0 10px">Live sensors</h2>
    <div class="grid g3"><div class="card tight"><h3>Flow (L/min)</h3><div id="c-flow" class="chartbox"></div></div><div class="card tight"><h3>Pump current (A)</h3><div id="c-cur" class="chartbox"></div></div>
      <div class="card tight"><h3>Turbidity signal (V)</h3><div id="c-turb" class="chartbox"></div></div></div>
    <div class="grid g2" style="margin-top:16px"><div class="card tight"><h3>Pressure (bar)</h3><div id="c-p" class="chartbox"></div></div>
      <div class="card tight"><div class="row between"><h3>TDS in and out (ppm)</h3><div class="legend"><span><span class="sw" style="background:var(--series-1)"></span>in</span><span><span class="sw" style="background:var(--series-2)"></span>out</span></div></div><div id="c-tds" class="chartbox"></div></div></div>`;
  $('#devf').onsubmit = async (e) => {
    e.preventDefault();
    await api(`/api/devices/${id}`, 'PATCH', { name: $('#dn').value, address: $('#da').value, customer_id: $('#dc').value ? Number($('#dc').value) : null });
    $('#devsaved').textContent = 'Saved';
  };
  if (!S.readings[id] || S.readings[id].length < 5) {
    try { S.readings[id] = await api(`/api/devices/${id}/readings?minutes=10`); } catch { /* live data will arrive */ }
  }
  live();
}
function deviceLive(id) {
  const d = S.devices[id];
  if (!d || !$('#dh')) return;
  const h = d.hypothesis || {};
  const r = d.latest || {};
  $('#dh').innerHTML = `<div class="row between"><h2 style="margin:0">${esc(d.name)}</h2>${pill(d.status)}</div>
    <div class="small muted" style="margin:4px 0 10px">${d.id} · ${esc(d.customer?.name || 'unassigned')} ${d.customer ? '· ' + esc(d.customer.phone) : ''} · ${esc(d.address || '')}<br>
      fw ${esc(d.fw || '?')} · ${esc(d.ip || '')} · ${d.online ? `<span style="color:var(--good)">● live</span>, last reading ${ago(d.last_seen)}` : `○ offline since ${ago(d.last_seen)}`}</div>
    <div style="font-size:17px;font-weight:700">Quick check: ${esc(h.label || 'waiting for data')} ${h.score ? `<span class="muted" style="font-weight:500">· ${Math.round(h.score * 100)}% · ${(h.streak || 0) >= 3 ? 'confirmed over 3+ windows' : `${h.streak || 0}/3 windows`}</span>` : ''}</div>
    <ul style="margin:6px 0 0;padding-left:18px">${(h.evidence || []).map((e) => `<li>${esc(e)}</li>`).join('')}</ul>
    <div class="grid g4" style="margin-top:12px">
      <div class="kpi"><div class="l">Flow</div><div class="v">${fmt(r.flow_rate_Lmin)}<span class="small muted"> L/min</span></div></div>
      <div class="kpi"><div class="l">Pump</div><div class="v">${fmt(r.pump_current_A)}<span class="small muted"> A</span></div></div>
      <div class="kpi"><div class="l">TDS out</div><div class="v">${fmt(r.tds_out_ppm, 0)}<span class="small muted"> ppm</span></div></div>
      <div class="kpi"><div class="l">Volume</div><div class="v">${fmt(r.total_volume_L, 0)}<span class="small muted"> L</span></div></div></div>`;
  $('#dhb').innerHTML = Object.entries(d.health || {}).map(([k, v]) => `<div class="hb"><span class="n">${COMP[k]}</span><span class="bar"><i style="width:${v}%;background:${hcol(v)}"></i><s></s></span><span class="v">${Math.round(v)}%</span></div>`).join('');
  const ov = d.overlay || {};
  $('#applied').innerHTML = d.overlay_applied ? '<span class="applied ok">✓ applied on device</span>' : '<span class="applied wait">⟳ waiting for device…</span>';
  $('#faults').innerHTML = FAULT_ROWS.map(([k, name, sig]) => `<div class="panelrow"><div><div class="t">${name}</div><div class="d">${sig}</div></div>
      <div class="seg">${[0, 1, 2, 3].map((l) => `<button data-act="ov" data-arg='${JSON.stringify({ [k]: l })}' aria-pressed="${(ov[k] || 0) === l}">${l ? l : 'off'}</button>`).join('')}</div></div>`).join('') +
    `<div class="panelrow"><div><div class="t">Sensor fault</div><div class="d">one sensor misbehaves</div></div><div class="seg">${[['none', 'none'], ['flow_stuck', 'flow'], ['tds_out_dead', 'TDS out'], ['turbidity_disconnected', 'turbidity'], ['pressure_frozen', 'pressure']]
      .map(([v, l]) => `<button data-act="ov" data-arg='${JSON.stringify({ sensor: v })}' aria-pressed="${(ov.sensor || 'none') === v}">${l}</button>`).join('')}</div></div>
    <div class="panelrow"><div><div class="t">Source water</div><div class="d">not a fault: must not alarm</div></div><div class="seg">${[300, 800, 1500]
      .map((v) => `<button data-act="ov" data-arg='${JSON.stringify({ tds_source: v })}' aria-pressed="${(ov.tds_source || 300) === v}">${v}</button>`).join('')}</div></div>
    <div class="row" style="margin-top:10px"><button class="btn" data-act="ov" data-arg='{"clog":0,"pump":0,"membrane":0,"turbidity":0,"sensor":"none"}'>Clear all</button></div>`;
  const b = d.baseline;
  $('#dbase').innerHTML = b ? `<div class="small"><b>Healthy baseline</b> (learnt from ${b.n} readings): flow ${fmt(b.flow_rate_Lmin)} L/min · pump ${fmt(b.pump_current_A)} A · turbidity ${fmt(b.turbidity_voltage_V)} V · pressure ${fmt(b.pressure_bar)} bar · rejection ${fmt(b.rejection * 100, 1)}%
      <button class="btn" style="margin-left:8px" data-act="rebase">Re-learn</button></div>`
    : `<div class="small muted">Learning the healthy baseline: ${d.baseline_progress}/30 readings with water flowing and no fault active.</div>`;
  const j = d.job;
  const lastJob = j || Object.values(S.jobs).filter((x) => x.device_id === id).sort((a, b2) => b2.id - a.id)[0];
  $('#djob').innerHTML = `<div class="row between"><h2 style="margin:0">Service</h2>${!j ? `<button class="btn pri" data-act="newjob" data-arg="${id}">Send a technician</button>` : ''}</div>` +
    (lastJob ? jobCard(lastJob, true) : '<p class="small muted">No service jobs for this device.</p>');
  // charts
  const rows = (S.readings[id] || []).filter((x) => x.ts > Date.now() / 1000 - 600);
  const pts = (k) => rows.map((x) => [x.ts * 1000, x[k]]);
  const c1 = css('--series-1'), c2 = css('--series-2');
  lineChart($('#c-flow'), [{ name: 'Flow', color: c1, points: pts('flow_rate_Lmin') }], { unit: 'L/min', height: 150 });
  lineChart($('#c-cur'), [{ name: 'Current', color: c1, points: pts('pump_current_A') }], { unit: 'A', height: 150 });
  lineChart($('#c-turb'), [{ name: 'Turbidity', color: c1, points: pts('turbidity_voltage_V') }], { unit: 'V', height: 150 });
  lineChart($('#c-p'), [{ name: 'Pressure', color: c1, points: pts('pressure_bar') }], { unit: 'bar', height: 160 });
  lineChart($('#c-tds'), [{ name: 'TDS in', color: c1, points: pts('tds_in_ppm') }, { name: 'TDS out', color: c2, points: pts('tds_out_ppm') }], { unit: 'ppm', height: 160, digits: 0 });
}

function jobCard(j, detail = false) {
  const rep = j.report;
  const techSel = `<select data-act-change="assign" data-arg="${j.id}" class="btn" aria-label="Technician">${S.technicians.map((t) => `<option value="${t.id}" ${j.technician?.id === t.id ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select>`;
  return `<div class="card" style="box-shadow:none;margin-top:10px"><div class="row between"><b>#${j.id} · ${esc(j.hypothesis_label)}</b><span class="pill ${['resolved', 'no_fault', 'cancelled'].includes(j.status) ? 'normal' : j.status === 'confirmed' ? 'alert' : 'service'}">${JOB[j.status]}</span></div>
    <div class="small muted">${esc(j.device?.name || j.device_id)} · ${esc(j.device?.customer?.name || '')} · ${j.source === 'quick_check' ? 'raised by quick check' : j.source === 'customer' ? 'booked by customer' : 'created by service desk'} · ${hhmm(j.created_at)}</div>
    ${j.note ? `<div class="small">“${esc(j.note)}”</div>` : ''}
    ${j.estimate ? `<div class="small">Estimate: ${esc(j.estimate.part)} ₹${j.estimate.parts} + labour ₹${j.estimate.labour} + visit ₹${j.estimate.visit} = <b>₹${j.estimate.total}</b></div>` : ''}
    <div class="row small" style="margin-top:6px">Technician: ${detail || j.status === 'new' ? techSel : esc(j.technician?.name || '—')}</div>
    ${rep ? `<div class="${rep.verdict === 'no_fault' ? 'okbox' : 'alertbox'}" style="margin-top:8px"><b>On-site result: ${rep.verdict === 'confirmed' ? 'Confirmed' : rep.verdict === 'different' ? 'Different fault' : 'No fault found'}</b>
      ${rep.fault_label ? ` · ${esc(rep.fault_label)}` : ''} ${rep.confidence ? `(${Math.round(rep.confidence * 100)}%)` : ''}
      <div class="small">${esc(rep.reason || '')}</div>${rep.quote ? `<div class="small">Quote on site: <b>₹${rep.quote.total}</b> (${esc((rep.quote.replace || []).join(', ') || 'no parts')})</div>` : ''}
      <div class="small muted">${rep.readings || 0} Bluetooth readings · ${esc(rep.collected_at || '')}</div></div>` : ''}
    ${detail && !['resolved', 'cancelled'].includes(j.status) ? `<div class="row" style="margin-top:8px"><button class="btn" data-act="jobstatus" data-arg="${j.id}:resolved">Mark resolved</button><button class="btn" data-act="jobstatus" data-arg="${j.id}:cancelled">Cancel</button></div>` : ''}</div>`;
}

// ------------------------------------------------------------------ jobs board / people
function boardLive() {
  const cols = ['new', 'assigned', 'en_route', 'on_site', 'confirmed', 'resolved'];
  const js = Object.values(S.jobs).sort((a, b) => b.id - a.id);
  $('#board').innerHTML = `<div class="board">${cols.map((c) => `<div class="col"><h3>${JOB[c]} · ${js.filter((j) => j.status === c || (c === 'resolved' && ['no_fault', 'cancelled'].includes(j.status))).length}</h3>
    ${js.filter((j) => j.status === c || (c === 'resolved' && ['no_fault', 'cancelled'].includes(j.status))).map((j) => jobCard(j)).join('')}</div>`).join('')}</div>`;
}
function renderPeople() {
  const ds = Object.values(S.devices);
  const js = Object.values(S.jobs);
  $('#view').innerHTML = `<h1>Team & customers</h1><p class="sub">Sign-in for the AquaSense app uses these usernames and PINs.</p>
    <div class="grid g2"><div class="card"><h2>Technicians</h2><table class="tbl"><thead><tr><th>Name</th><th>Username</th><th>Area</th><th>Active jobs</th></tr></thead><tbody>
      ${S.technicians.map((t) => `<tr><td><b>${esc(t.name)}</b><div class="small muted">${esc(t.phone)}</div></td><td class="mono">${esc(t.username)}</td><td>${esc(t.area)}</td>
        <td>${js.filter((j) => j.technician?.id === t.id && ['assigned', 'en_route', 'on_site', 'confirmed'].includes(j.status)).length}</td></tr>`).join('')}</tbody></table></div>
    <div class="card"><h2>Customers</h2><table class="tbl"><thead><tr><th>Name</th><th>Username</th><th>Devices</th></tr></thead><tbody>
      ${S.customers.map((c) => `<tr><td><b>${esc(c.name)}</b><div class="small muted">${esc(c.area)}</div></td><td class="mono">${esc(c.username)}</td>
        <td>${ds.filter((d) => d.customer?.id === c.id).map((d) => `<a href="#device/${d.id}">${d.id}</a>`).join(', ') || '—'}</td></tr>`).join('')}</tbody></table></div></div>`;
}

// ------------------------------------------------------------------ live loop + actions
function live() {
  renderTop();
  if (!S.user) return;
  if (S.route === 'device') deviceLive(S.arg);
  else if (S.route === 'jobs') boardLive();
  else if (S.route === 'fleet' || !S.route) fleetLive();
  S.dirty = false;
}
let pointerDown = false;
document.addEventListener('pointerdown', () => { pointerDown = true; });
document.addEventListener('pointerup', () => setTimeout(() => { pointerDown = false; }, 120));
setInterval(() => {
  const busy = pointerDown || (document.activeElement && ['SELECT', 'INPUT'].includes(document.activeElement.tagName));
  if (S.dirty && !busy) live();
}, 700);

const ACTIONS = {
  logout: () => logout(),
  ov: async (arg) => { const did = S.arg; await api(`/api/devices/${did}/overlay`, 'POST', JSON.parse(arg)); },
  rebase: async () => { await api(`/api/devices/${S.arg}/baseline/reset`, 'POST'); toast('Re-learning the healthy baseline'); },
  newjob: async (did) => { const j = await api('/api/jobs', 'POST', { device_id: did, note: 'Created by the service desk' }); toast(`Job #${j.id} sent to ${j.technician?.name || 'the queue'}`); },
  jobstatus: async (arg) => { const [jid, st] = arg.split(':'); await api(`/api/jobs/${jid}`, 'PATCH', { status: st }); },
};
document.addEventListener('click', async (e) => {
  const t = e.target.closest('[data-act]');
  if (!t) return;
  try { await ACTIONS[t.dataset.act]?.(t.dataset.arg, t); } catch (err) { toast(err.message, 'alert'); }
});
document.addEventListener('change', async (e) => {
  const t = e.target.closest('[data-act-change="assign"]');
  if (!t) return;
  try { await api(`/api/jobs/${t.dataset.arg}`, 'PATCH', { technician_id: Number(t.value) }); toast('Technician reassigned'); } catch (err) { toast(err.message, 'alert'); }
});
window.addEventListener('hashchange', render);
render();
