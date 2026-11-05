// Shared front-end helpers: formatting, tooltip, scales, axes and tables. No dependencies.

export const $ = (s) => document.querySelector(s);
export const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
export const DAY = 864e5;

// --- Formatting ---------------------------------------------------------------
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const isNum = (v) => typeof v === "number" && Number.isFinite(v);
export const money = (v, dp = 2) => (isNum(v) ? v.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp }) : "—");
export const pct = (v, dp = 1) => (isNum(v) ? `${v < 0 ? "−" : "+"}${Math.abs(v * 100).toFixed(dp)}%` : "—");
export const share = (v, dp = 1) => (isNum(v) ? `${(v * 100).toFixed(dp)}%` : "—");
export function compact(v) {
  const a = Math.abs(v), sign = v < 0 ? "−" : "";
  const f = (d, s) => `${sign}${+(a / d).toFixed(a / d < 10 && a % d ? 1 : 0)}${s}`;
  return a >= 1e9 ? f(1e9, "B") : a >= 1e6 ? f(1e6, "M") : a >= 1e3 ? f(1e3, "K") : `${sign}${+a.toFixed(0)}`;
}
export const ms = (iso) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
export const pad2 = (n) => String(n).padStart(2, "0");
export const day = (t) => { const d = new Date(t); return `${pad2(d.getUTCDate())} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
export const monthLabel = (t) => { const d = new Date(t); return `${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
export const addMonths = (t, n) => { const d = new Date(t); return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1); };
export const when = (v) => (!v ? "—" : v.includes("T") ? localTime(v) : day(ms(v)));
export const localTime = (iso) => { const d = new Date(iso); return `${pad2(d.getDate())} ${MON[d.getMonth()]} ${d.getFullYear()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`; };
export const empty = (text) => `<div class="empty">${esc(text)}</div>`;

// --- Tooltip -------------------------------------------------------------------
export const tip = $("#tip");
export function showTip(title, rows, ev) {
  tip.innerHTML = `<div class="h">${esc(title)}</div>` +
    rows.map(([k, v, cls]) => `<div class="r"><span>${esc(k)}</span><span class="${cls || ""}">${esc(v)}</span></div>`).join("");
  tip.hidden = false;
  moveTip(ev);
}
export function moveTip(ev) {
  const r = tip.getBoundingClientRect(), gap = 14;
  let x = ev.clientX + gap, y = ev.clientY + gap;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - gap;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - gap;
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
}
export const hideTip = () => { tip.hidden = true; };

// --- Scales and axes -------------------------------------------------------------
export const linear = ([d0, d1], [r0, r1]) => { const k = (r1 - r0) / (d1 - d0 || 1); return (v) => r0 + (v - d0) * k; };
export function niceTicks(lo, hi, n = 5) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw)), e = raw / mag;
  const step = (e >= 7.5 ? 10 : e >= 3.5 ? 5 : e >= 1.5 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) out.push(Math.round(v / step) * step);
  return out;
}
export function timeTicks(t0, t1, maxN) {
  const months = (t1 - t0) / (30.44 * DAY);
  const step = [1, 2, 3, 6, 12, 24, 60].find((s) => months / s <= maxN) ?? 120;
  const d = new Date(t0);
  let idx = d.getUTCFullYear() * 12 + d.getUTCMonth() + (d.getUTCDate() > 1 ? 1 : 0);
  idx = Math.ceil(idx / step) * step;
  const ticks = [];
  for (; ; idx += step) { const t = Date.UTC(Math.floor(idx / 12), idx % 12, 1); if (t > t1) break; ticks.push(t); }
  return { ticks, fmt: step >= 12 ? (t) => String(new Date(t).getUTCFullYear()) : monthLabel };
}
export function frame(el, height, m = { left: 52, right: 10, top: 10, bottom: 26 }) {
  const w = Math.max(280, Math.round(el.clientWidth));
  return { w, h: height, x0: m.left, x1: w - m.right, y0: height - m.bottom, y1: m.top };
}
export const yGrid = (f, y, ticks) => ticks.map((v) =>
  `<line class="gridline" x1="${f.x0}" x2="${f.x1}" y1="${y(v)}" y2="${y(v)}"/>` +
  `<text x="${f.x0 - 8}" y="${y(v) + 3}" text-anchor="end">${compact(v)}</text>`).join("");
export const xAxis = (f, x, { ticks, fmt }) => `<g class="axis"><line x1="${f.x0}" x2="${f.x1}" y1="${f.y0}" y2="${f.y0}"/>` +
  ticks.map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="${f.y0}" y2="${f.y0 + 4}"/>` +
    `<text x="${x(t)}" y="${f.y0 + 17}" text-anchor="middle">${fmt(t)}</text>`).join("") + "</g>";
export const svgOpen = (f, label) => `<svg viewBox="0 0 ${f.w} ${f.h}" height="${f.h}" role="img" aria-label="${esc(label)}">`;
export const svgPoint = (svg, f, ev) => { const r = svg.getBoundingClientRect(); return { x: (ev.clientX - r.left) * (f.w / r.width), y: (ev.clientY - r.top) * (f.h / r.height) }; };

// A line for each day with a value for every holding (gaps where one is missing), drawn on scales x and y.
export function pathOf(pts, key, x, y) {
  let out = "", prev = null;
  for (const p of pts) {
    if (!isNum(p[key])) { prev = null; continue; }
    out += `${prev === null || p.t - prev > 31 * DAY ? "M" : "L"}${x(p.t)},${y(p[key])}`;
    prev = p.t;
  }
  return out;
}

export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export const F = {
  text: (v) => esc(v ?? ""), int: (v) => money(v, 0), money: (v) => money(v), pct1: (v) => pct(v), pct2: (v) => pct(v, 2),
  share: (v) => share(v), date: (v) => esc(v ?? ""), datetime: (v) => (v ? localTime(v) : "—"), when: (v) => esc(when(v)),
  qty: (v) => (v ? money(v, 0) : ""), cash: (v) => (v ? money(v) : ""),  // blank instead of 0
  sym: (v) => (v ? symLink(v) : ""), days: (v) => (isNum(v) ? money(v, 0) : "—"),
};
export const NUMERIC = new Set(["int", "money", "pct1", "pct2", "share", "qty", "cash", "days"]);

// A readout cell: label, value (HTML), sub-line; `alert` colours the sub-line.
export const box = (label, v, sub, alert) =>
  `<div class="cell"><div class="k">${esc(label)}</div><div class="v">${v}</div><div class="s${alert ? " alert" : ""}">${esc(sub)}</div></div>`;
// A money amount for a readout: small decimals under a million.
export function amount(value) {
  if (!isNum(value)) return "—";
  const [whole, dec] = money(value).split(".");
  return Math.abs(value) >= 1e6 ? esc(money(value, 0)) : `${esc(whole)}<span class="dec">.${dec}</span>`;
}
export const signed = (v, dp = 0) => (isNum(v) ? `${v < 0 ? "−" : "+"}${money(Math.abs(v), dp)}` : "—");
export const symLink = (s) => `<a class="sym-link" href="#/t/${encodeURIComponent(s)}">${esc(s)}</a>`;

// A sortable table in `el`: cols are [key, label, format, signed?]; `sort` ({key, dir}) is kept by the caller,
// which re-renders when `onSort(newSort)` is called.
export function dataTable(el, cols, rows, sort, onSort, emptyText = "Nothing to show") {
  if (sort) {
    rows = [...rows].sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      const c = x == null ? 1 : y == null ? -1 : typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return sort.dir * c;
    });
  }
  const head = cols.map(([k, label, fmt]) => `<th class="${NUMERIC.has(fmt) ? "num" : ""}" data-key="${k}"
    aria-sort="${sort?.key === k ? (sort.dir > 0 ? "ascending" : "descending") : "none"}">${esc(label)}</th>`).join("");
  const body = rows.map((r) => "<tr>" + cols.map(([k, , fmt, sgn]) =>
    `<td class="${NUMERIC.has(fmt) ? "num" : ""} ${sgn && r[k] < 0 ? "alert" : ""}">${F[fmt](r[k])}</td>`).join("") + "</tr>").join("");
  el.innerHTML = rows.length ? `<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>` : empty(emptyText);
  el.querySelectorAll("th").forEach((th) => th.addEventListener("click", () =>
    onSort({ key: th.dataset.key, dir: sort?.key === th.dataset.key ? -sort.dir : -1 })));
}
