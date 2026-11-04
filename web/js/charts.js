// Small SVG charts with a hover crosshair + tooltip. One y-axis per chart.
const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs = {}) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };

function niceTicks(min, max, n = 4) {
  if (max - min < 1e-9) { max = min + 1; }
  const step0 = (max - min) / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const out = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Number(v.toFixed(6)));
  return out;
}
const hhmm = (ms) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
const dmy = (ms) => new Date(ms).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });

/**
 * series: [{ name, color, points: [[x, y], ...], dash?, kind?: 'line'|'dots' }]
 * opts: { height, unit, digits, yMin, yMax, xFormat: 'time'|'date', band, hline }
 */
export function lineChart(host, series, opts = {}) {
  const W = Math.max(host.clientWidth || 300, 200), H = opts.height || 180;
  const m = { l: 40, r: 10, t: 10, b: 24 };
  const xs = series.flatMap((s) => s.points.map((p) => p[0]));
  const ys = series.flatMap((s) => s.points.map((p) => p[1])).concat(opts.band ? opts.band.flatMap((b) => [b[1]]) : []);
  if (!xs.length) { host.innerHTML = '<div class="empty">No data yet</div>'; return; }
  let x0 = Math.min(...xs, ...(opts.band ? opts.band.map((b) => b[0]) : [])), x1 = Math.max(...xs, ...(opts.band ? opts.band.map((b) => b[0]) : []));
  if (x1 === x0) x1 = x0 + 1;
  let y0 = opts.yMin ?? Math.min(...ys), y1 = opts.yMax ?? Math.max(...ys);
  if (opts.yMin === undefined) {
    const nonNeg = y0 >= 0, pad = (y1 - y0) * 0.12 || Math.abs(y1) * 0.05 || 1;
    y0 -= pad; y1 += pad;
    if (nonNeg) y0 = Math.max(0, y0);
  }
  const ticks = niceTicks(y0, y1);
  if (opts.yMin === undefined) { y0 = ticks[0]; y1 = ticks[ticks.length - 1]; }
  if (opts.yMin === undefined && y0 < 0 && Math.min(...ys) >= 0) y0 = 0;
  const sx = (x) => m.l + (x - x0) / (x1 - x0) * (W - m.l - m.r);
  const sy = (y) => H - m.b - (y - y0) / (y1 - y0) * (H - m.t - m.b);
  const svg = el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img', 'aria-label': opts.label || 'chart' });
  for (const t of ticks) {
    if (t < y0 - 1e-9 || t > y1 + 1e-9) continue;
    svg.append(el('line', { x1: m.l, x2: W - m.r, y1: sy(t), y2: sy(t), class: 'grid' }));
    const tx = el('text', { x: m.l - 6, y: sy(t) + 4, class: 'tick', 'text-anchor': 'end' });
    tx.textContent = Number(t.toFixed(opts.digits ?? 2)).toString();
    svg.append(tx);
  }
  const fmt = opts.xFormat === 'date' ? dmy : hhmm;
  for (let k = 0; k <= 3; k++) {
    const xv = x0 + (x1 - x0) * k / 3;
    const tx = el('text', { x: sx(xv), y: H - 6, class: 'tick', 'text-anchor': k === 0 ? 'start' : k === 3 ? 'end' : 'middle' });
    tx.textContent = fmt(xv);
    svg.append(tx);
  }
  if (opts.band) {
    svg.append(el('path', { d: 'M' + opts.band.map((b) => `${sx(b[0])},${sy(b[1])}`).join('L') + 'Z', class: 'band' }));
  }
  if (opts.hline !== undefined) {
    svg.append(el('line', { x1: m.l, x2: W - m.r, y1: sy(opts.hline.y), y2: sy(opts.hline.y), class: 'hline' }));
    const tx = el('text', { x: m.l + 4, y: sy(opts.hline.y) - 5, class: 'tick hline-label' });
    tx.textContent = opts.hline.label;
    svg.append(tx);
  }
  for (const s of series) {
    if (s.kind === 'dots') {
      for (const p of s.points) svg.append(el('circle', { cx: sx(p[0]), cy: sy(p[1]), r: 3, fill: s.color }));
    } else if (s.points.length) {
      svg.append(el('path', { d: 'M' + s.points.map((p) => `${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join('L'),
        fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-dasharray': s.dash || '' }));
    }
  }
  // hover layer
  const cross = el('line', { y1: m.t, y2: H - m.b, class: 'cross', visibility: 'hidden' });
  svg.append(cross);
  const dots = series.map((s) => { const c = el('circle', { r: 4, fill: s.color, class: 'hdot', visibility: 'hidden' }); svg.append(c); return c; });
  const hit = el('rect', { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: 'transparent' });
  svg.append(hit);
  host.innerHTML = '';
  host.style.position = 'relative';
  host.append(svg);
  const tip = document.createElement('div');
  tip.className = 'tip';
  host.append(tip);
  const show = (px) => {
    const xv = x0 + (px - m.l) / (W - m.l - m.r) * (x1 - x0);
    const lines = [];
    let cx = null;
    series.forEach((s, k) => {
      if (!s.points.length) return;
      let best = s.points[0];
      for (const p of s.points) if (Math.abs(p[0] - xv) < Math.abs(best[0] - xv)) best = p;
      dots[k].setAttribute('cx', sx(best[0])); dots[k].setAttribute('cy', sy(best[1])); dots[k].setAttribute('visibility', 'visible');
      cx = cx ?? best[0];
      lines.push(`<span class="sw" style="background:${s.color}"></span>${s.name}: <b>${best[1].toFixed(opts.digits ?? 2)}</b>${opts.unit ? ' ' + opts.unit : ''}`);
    });
    if (cx === null) return;
    cross.setAttribute('x1', sx(cx)); cross.setAttribute('x2', sx(cx)); cross.setAttribute('visibility', 'visible');
    tip.innerHTML = `<div class="tip-h">${opts.xFormat === 'date' ? dmy(cx) : new Date(cx).toLocaleTimeString('en-IN')}</div>${lines.join('<br>')}`;
    tip.style.display = 'block';
    const left = Math.min(sx(cx) + 12, W - tip.offsetWidth - 4);
    tip.style.left = `${Math.max(4, left)}px`;
    tip.style.top = '6px';
  };
  const hide = () => { tip.style.display = 'none'; cross.setAttribute('visibility', 'hidden'); dots.forEach((d) => d.setAttribute('visibility', 'hidden')); host._hoverX = null; };
  hit.addEventListener('mousemove', (e) => { const r = svg.getBoundingClientRect(); host._hoverX = e.clientX - r.left; show(host._hoverX); });
  hit.addEventListener('touchstart', (e) => { const r = svg.getBoundingClientRect(); host._hoverX = e.touches[0].clientX - r.left; show(host._hoverX); }, { passive: true });
  hit.addEventListener('mouseleave', hide);
  if (host._hoverX != null) show(host._hoverX); // keep the tooltip through live re-renders
}
