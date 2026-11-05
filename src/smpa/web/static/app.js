// Portfolio Monitor front end: vanilla JS + SVG, no dependencies.

const $ = (s) => document.querySelector(s);
const state = { data: null, asOf: null, filter: "", tab: "holdings", sort: {}, hl: null };
const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const DAY = 864e5;

// --- Formatting ---------------------------------------------------------------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const money = (v, dp = 2) => (isNum(v) ? v.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp }) : "—");
const pct = (v, dp = 1) => (isNum(v) ? `${v < 0 ? "−" : "+"}${Math.abs(v * 100).toFixed(dp)}%` : "—");
const share = (v, dp = 1) => (isNum(v) ? `${(v * 100).toFixed(dp)}%` : "—");
function compact(v) {
  const a = Math.abs(v), sign = v < 0 ? "−" : "";
  const f = (d, s) => `${sign}${+(a / d).toFixed(a / d < 10 && a % d ? 1 : 0)}${s}`;
  return a >= 1e9 ? f(1e9, "B") : a >= 1e6 ? f(1e6, "M") : a >= 1e3 ? f(1e3, "K") : `${sign}${+a.toFixed(0)}`;
}
const ms = (iso) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
const pad2 = (n) => String(n).padStart(2, "0");
const day = (t) => { const d = new Date(t); return `${pad2(d.getUTCDate())} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const monthLabel = (t) => { const d = new Date(t); return `${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const addMonths = (t, n) => { const d = new Date(t); return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1); };
const localTime = (iso) => { const d = new Date(iso); return `${pad2(d.getDate())} ${MON[d.getMonth()]} ${d.getFullYear()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`; };
const cur = () => state.data.meta.currency;
const empty = (text) => `<div class="empty">${esc(text)}</div>`;

// --- Tooltip -------------------------------------------------------------------
const tip = $("#tip");
function showTip(title, rows, ev) {
  tip.innerHTML = `<div class="h">${esc(title)}</div>` +
    rows.map(([k, v, cls]) => `<div class="r"><span>${esc(k)}</span><span class="${cls || ""}">${esc(v)}</span></div>`).join("");
  tip.hidden = false;
  moveTip(ev);
}
function moveTip(ev) {
  const r = tip.getBoundingClientRect(), gap = 14;
  let x = ev.clientX + gap, y = ev.clientY + gap;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - gap;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - gap;
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
}
const hideTip = () => { tip.hidden = true; };

// --- Scales and axes -------------------------------------------------------------
const linear = ([d0, d1], [r0, r1]) => { const k = (r1 - r0) / (d1 - d0 || 1); return (v) => r0 + (v - d0) * k; };
function niceTicks(lo, hi, n = 5) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw)), e = raw / mag;
  const step = (e >= 7.5 ? 10 : e >= 3.5 ? 5 : e >= 1.5 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) out.push(Math.round(v / step) * step);
  return out;
}
function timeTicks(t0, t1, maxN) {
  const months = (t1 - t0) / (30.44 * DAY);
  const step = [1, 2, 3, 6, 12, 24, 60].find((s) => months / s <= maxN) ?? 120;
  const d = new Date(t0);
  let idx = d.getUTCFullYear() * 12 + d.getUTCMonth() + (d.getUTCDate() > 1 ? 1 : 0);
  idx = Math.ceil(idx / step) * step;
  const ticks = [];
  for (; ; idx += step) { const t = Date.UTC(Math.floor(idx / 12), idx % 12, 1); if (t > t1) break; ticks.push(t); }
  return { ticks, fmt: step >= 12 ? (t) => String(new Date(t).getUTCFullYear()) : monthLabel };
}
function frame(el, height, m = { left: 52, right: 10, top: 10, bottom: 26 }) {
  const w = Math.max(280, Math.round(el.clientWidth));
  return { w, h: height, x0: m.left, x1: w - m.right, y0: height - m.bottom, y1: m.top };
}
const yGrid = (f, y, ticks) => ticks.map((v) =>
  `<line class="gridline" x1="${f.x0}" x2="${f.x1}" y1="${y(v)}" y2="${y(v)}"/>` +
  `<text x="${f.x0 - 8}" y="${y(v) + 3}" text-anchor="end">${compact(v)}</text>`).join("");
const xAxis = (f, x, { ticks, fmt }) => `<g class="axis"><line x1="${f.x0}" x2="${f.x1}" y1="${f.y0}" y2="${f.y0}"/>` +
  ticks.map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="${f.y0}" y2="${f.y0 + 4}"/>` +
    `<text x="${x(t)}" y="${f.y0 + 17}" text-anchor="middle">${fmt(t)}</text>`).join("") + "</g>";
const svgOpen = (f, label) => `<svg viewBox="0 0 ${f.w} ${f.h}" height="${f.h}" role="img" aria-label="${esc(label)}">`;
const svgPoint = (svg, f, ev) => { const r = svg.getBoundingClientRect(); return { x: (ev.clientX - r.left) * (f.w / r.width), y: (ev.clientY - r.top) * (f.h / r.height) }; };

// --- Header and readouts -----------------------------------------------------------
const MARK = `<svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true"><circle cx="15" cy="15" r="9" fill="none" stroke="currentColor"/>
<circle cx="15" cy="15" r="2" fill="currentColor"/><path d="M15 0v9M15 21v9M0 15h9M21 15h9" stroke="currentColor"/></svg>`;

function header() {
  const m = state.data.meta, n = state.data.flags.length;
  $("#top").innerHTML = `<div class="brand">${MARK}<div><h1 class="title">Portfolio Monitor</h1>
    <div class="subtitle">NDB Securities · Account ${esc(m.account || "—")} · ${esc(m.currency)}</div></div></div>
    <div class="meta">
      <div><span class="k">Trades through</span><span class="v">${m.last_trade ? day(ms(m.last_trade)) : "—"}</span></div>
      <div><span class="k">Prices</span><span class="v"><span id="price-time" class="${m.prices_fetched ? "" : "alert"}">${m.prices_fetched ? localTime(m.prices_fetched) : "Not fetched"}</span>
        <button class="link" id="refresh" type="button">Refresh</button></span></div>
      <div><span class="k">Status</span><span class="v ${n ? "alert" : ""}">● ${n ? `${n} flag${n === 1 ? "" : "s"}` : "Nominal"}</span></div>
      <div><span class="k">Display</span><span class="v"><button class="link" id="theme" type="button"></button></span></div>
    </div>`;
  $("#refresh").addEventListener("click", refreshPrices);
  const theme = $("#theme"), label = (m) => { theme.textContent = `Theme · ${m}`; };
  label(window.smpaTheme?.mode() ?? "auto");
  theme.addEventListener("click", () => label(window.smpaTheme.cycle()));
}

function readouts() {
  const r = state.data.readouts, c = cur();
  const cell = (label, value, sub, alert) => {
    let v = "—";
    if (isNum(value)) {
      const [whole, dec] = money(value).split(".");
      v = Math.abs(value) >= 1e6 ? esc(money(value, 0)) : `${esc(whole)}<span class="dec">.${dec}</span>`;
    }
    return `<div class="cell"><div class="k">${esc(label)} · ${esc(c)}</div><div class="v">${v}</div><div class="s${alert ? " alert" : ""}">${esc(sub)}</div></div>`;
  };
  const unpriced = r.positions - r.priced, up = r.unrealized_pct;
  $("#readouts").innerHTML = [
    cell("Net invested", r.net_invested, "Buys − sell proceeds", false),
    cell("Open cost basis", r.cost_basis, `${r.positions} position${r.positions === 1 ? "" : "s"}`, false),
    cell("Market value", r.market_value, `${r.priced}/${r.positions} priced`, unpriced > 0),
    cell("Unrealized P&L", r.unrealized, isNum(up) ? `${up >= 0 ? "▲" : "▼"} ${pct(up, 2)} vs cost` : "—", isNum(up) && up < 0),
    cell("Realized P&L", r.realized, `${r.sells} sell${r.sells === 1 ? "" : "s"}`, r.realized < 0),
    cell("Fees paid", r.fees, r.turnover ? `${share(r.fees / r.turnover, 2)} of turnover` : "—", false),
  ].join("");
}

// --- 01 Holdings network --------------------------------------------------------------
function vdc(i) { // base-2 radical inverse: 0, .5, .25, .75, ... spreads the largest holdings evenly
  let out = 0, d = 1;
  while (i) { d *= 2; out += (i & 1) / d; i >>= 1; }
  return out;
}
const nodeKind = (h) => (!h.priced ? "unpriced" : h.unrealized_pnl < 0 ? "loss" : "");

function network() {
  const el = $("#network"), hs = state.data.holdings, unpriced = hs.filter((h) => !h.priced).length;
  $("#net-note").textContent = `Node area = market value${unpriced ? ` · ${unpriced} unpriced, sized by cost` : ""}`;
  if (!hs.length) { el.innerHTML = empty("No open positions"); return; }
  const W = Math.max(300, Math.round(el.clientWidth)), H = Math.round(Math.min(560, Math.max(340, W * 0.72)));
  const cx = W / 2, cy = H / 2, R = Math.min(W * 0.27, H * 0.34);
  const wmax = Math.max(...hs.map((h) => h.weight || 0)) || 1;
  const nodes = hs.map((h, i) => {
    const a = -Math.PI / 2 + 2 * Math.PI * vdc(i), r = 4 + 16 * Math.sqrt((h.weight || 0) / wmax);
    return { h, i, a, r, x: cx + R * Math.cos(a), y: cy + R * Math.sin(a), kind: nodeKind(h) };
  });
  let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Holdings network: ${hs.length} positions">`;
  s += `<line class="hair" x1="${cx - R * 1.3}" x2="${cx + R * 1.3}" y1="${cy}" y2="${cy}"/>`;
  s += `<line class="hair" x1="${cx}" x2="${cx}" y1="${cy - R * 1.3}" y2="${cy + R * 1.3}"/>`;
  s += `<circle class="orbit" cx="${cx}" cy="${cy}" r="${R}"/><circle class="inner" cx="${cx}" cy="${cy}" r="${R * 0.42}"/>`;
  for (let k = 0; k < 72; k++) {
    const a = (2 * Math.PI * k) / 72, r1 = R * 1.1, r2 = k % 6 === 0 ? R * 1.17 : R * 1.13;
    s += `<line class="dial" x1="${cx + r1 * Math.cos(a)}" y1="${cy + r1 * Math.sin(a)}" x2="${cx + r2 * Math.cos(a)}" y2="${cy + r2 * Math.sin(a)}"/>`;
  }
  for (const n of nodes) s += `<line class="spoke" data-sym="${esc(n.h.symbol)}" x1="${cx}" y1="${cy}" x2="${n.x}" y2="${n.y}"/>`;
  if (W >= 520) {
    for (const n of nodes.slice(0, 16)) {
      const c = Math.cos(n.a), sn = Math.sin(n.a), dir = c >= 0 ? 1 : -1, L = n.i < 8 ? R * 0.16 : R * 0.36;
      const p1 = [cx + (R + n.r + 2) * c, cy + (R + n.r + 2) * sn], p2 = [cx + (R + n.r + L) * c, cy + (R + n.r + L) * sn];
      const p3 = [p2[0] + dir * 12, p2[1]], tx = p3[0] + dir * 4, anchor = dir > 0 ? "start" : "end";
      s += `<g class="callout ${n.kind}" data-sym="${esc(n.h.symbol)}"><polyline points="${p1} ${p2} ${p3}"/>` +
        `<text x="${tx}" y="${p3[1] - 2}" text-anchor="${anchor}" class="sym">${esc(n.h.symbol)}</text>` +
        `<text x="${tx}" y="${p3[1] + 10}" text-anchor="${anchor}">${share(n.h.weight)} · ${pct(n.h.unrealized_pct)}</text></g>`;
    }
  }
  for (const n of nodes) {
    s += `<g class="node ${n.kind}" data-sym="${esc(n.h.symbol)}"><circle class="ring" cx="${n.x}" cy="${n.y}" r="${n.r}"/>` +
      `<circle class="core" cx="${n.x}" cy="${n.y}" r="1.6"/></g>`;
  }
  const total = hs.reduce((a, h) => a + (h.value || 0), 0);
  s += `<rect class="hub" x="${cx - 5}" y="${cy - 5}" width="10" height="10"/>`;
  s += `<rect class="hub-plate" x="${cx - 62}" y="${cy + 12}" width="124" height="30"/>`;
  s += `<text class="hub-label" x="${cx}" y="${cy + 24}">PORTFOLIO</text>`;
  s += `<text class="hub-label big" x="${cx}" y="${cy + 38}">${esc(cur())} ${money(total, 0)}</text></svg>`;
  el.innerHTML = s;
  el.querySelectorAll(".node").forEach((g) => {
    const h = hs.find((x) => x.symbol === g.dataset.sym);
    g.addEventListener("mouseenter", (ev) => { highlight(h.symbol); holdingTip(h, ev); });
    g.addEventListener("mousemove", moveTip);
    g.addEventListener("mouseleave", () => { highlight(null); hideTip(); });
  });
}

function holdingTip(h, ev) {
  showTip(h.symbol, [
    ["Qty", money(h.quantity, 0)], ["Avg cost", money(h.avg_cost)], ["Price", money(h.price)],
    ["Value", `${cur()} ${money(h.value)}`], ["Weight", share(h.weight)],
    ["Unrealized", pct(h.unrealized_pct), h.unrealized_pnl < 0 ? "alert" : ""],
    ["Today", pct(h.day_change, 2), h.day_change < 0 ? "alert" : ""],
  ], ev);
}

function highlight(sym) {
  state.hl = sym;
  document.querySelectorAll("[data-sym]").forEach((e) => e.classList.toggle("hl", sym !== null && e.dataset.sym === sym));
}

// --- 02 Position index ------------------------------------------------------------------
function positionIndex() {
  const el = $("#index"), hs = state.data.holdings, limit = 15;
  if (!hs.length) { el.innerHTML = empty("No open positions"); return; }
  const top = hs[0].value || 1;
  const rows = hs.slice(0, limit).map((h, i) => `<tr data-sym="${esc(h.symbol)}"><td class="i">${pad2(i + 1)}</td>
    <td class="sym">${esc(h.symbol)}</td><td class="bar-cell"><div style="width:${((h.value / top) * 100).toFixed(1)}%"></div></td>
    <td class="num">${share(h.weight)}</td><td class="num ${h.unrealized_pnl < 0 ? "alert" : ""}">${pct(h.unrealized_pct)}</td></tr>`).join("");
  const more = hs.length - limit;
  el.innerHTML = `<table class="index">${rows}</table>${more > 0 ? `<div class="more">+ ${more} more in the holdings table</div>` : ""}`;
  el.querySelectorAll("tr").forEach((tr) => {
    const h = hs.find((x) => x.symbol === tr.dataset.sym);
    tr.addEventListener("mouseenter", (ev) => { highlight(h.symbol); holdingTip(h, ev); });
    tr.addEventListener("mousemove", moveTip);
    tr.addEventListener("mouseleave", () => { highlight(null); hideTip(); });
  });
}

// --- 03 Capital deployed ------------------------------------------------------------------
function capital() {
  const el = $("#capital"), pts = state.data.capital.map((d) => ({ t: ms(d.date), v: d.value }));
  $("#cap-note").textContent = `Cumulative buys − sell proceeds · ${cur()}`;
  if (!pts.length) { el.innerHTML = empty("No trades yet"); return; }
  const f = frame(el, 300);
  let t0 = pts[0].t, t1 = pts.at(-1).t;
  if (t1 - t0 < 180 * DAY) { t0 = addMonths(t0, -2); t1 = addMonths(t1, 3); }
  const vals = pts.map((p) => p.v), ticks = niceTicks(Math.min(0, ...vals), Math.max(...vals));
  const x = linear([t0, t1], [f.x0, f.x1]), y = linear([ticks[0], ticks.at(-1)], [f.y0, f.y1]);
  let d = `M${x(pts[0].t)},${y(pts[0].v)}`;
  for (const p of pts.slice(1)) d += `H${x(p.t)}V${y(p.v)}`;
  el.innerHTML = svgOpen(f, "Capital deployed over time") + yGrid(f, y, ticks) +
    xAxis(f, x, timeTicks(t0, t1, Math.max(3, Math.floor((f.x1 - f.x0) / 90)))) +
    `<path class="series" d="${d}"/><g class="hover" visibility="hidden"><line class="cross vx" y1="${f.y1}" y2="${f.y0}"/>` +
    `<line class="cross hy" x1="${f.x0}" x2="${f.x1}"/><rect class="reticle" width="7" height="7"/></g>` +
    `<rect class="hitbox" x="${f.x0}" y="${f.y1}" width="${f.x1 - f.x0}" height="${f.y0 - f.y1}"/></svg>`;
  const svg = el.querySelector("svg"), g = svg.querySelector(".hover");
  svg.querySelector(".hitbox").addEventListener("mousemove", (ev) => {
    const px = svgPoint(svg, f, ev).x, t = t0 + ((px - f.x0) / (f.x1 - f.x0)) * (t1 - t0);
    let p = pts[0];
    for (const q of pts) { if (q.t <= t) p = q; else break; }
    if (t < pts[0].t) { g.setAttribute("visibility", "hidden"); hideTip(); return; }
    g.setAttribute("visibility", "visible");
    g.querySelector(".vx").setAttribute("x1", px); g.querySelector(".vx").setAttribute("x2", px);
    g.querySelector(".hy").setAttribute("y1", y(p.v)); g.querySelector(".hy").setAttribute("y2", y(p.v));
    g.querySelector(".reticle").setAttribute("x", px - 3.5); g.querySelector(".reticle").setAttribute("y", y(p.v) - 3.5);
    showTip(day(t), [["Net invested", `${cur()} ${money(p.v)}`]], ev);
  });
  svg.querySelector(".hitbox").addEventListener("mouseleave", () => { g.setAttribute("visibility", "hidden"); hideTip(); });
}

// --- 04 / 05 Monthly bars ---------------------------------------------------------------------
function monthBars(el, rows, series, height, label) {
  const months = rows.map((r) => ms(r.month));
  const f = frame(el, height);
  let t0 = months[0], t1 = addMonths(months.at(-1), 1);
  if (t1 - t0 < 180 * DAY) { t0 = addMonths(t0, -2); t1 = addMonths(t1, 2); }
  const vals = rows.flatMap((r) => series.map((s) => r[s.key] || 0));
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
  const x = linear([t0, t1], [f.x0, f.x1]), y = linear([ticks[0], ticks.at(-1)], [f.y0, f.y1]);
  let bars = "";
  rows.forEach((r, i) => {
    const b0 = x(months[i]), bw = x(addMonths(months[i], 1)) - b0, gap = Math.max(1, bw * 0.18);
    const w = Math.max(1, (bw - gap) / series.length);
    series.forEach((s, j) => {
      const v = r[s.key] || 0, top = Math.min(y(v), y(0)), hgt = Math.max(v ? 1 : 0, Math.abs(y(v) - y(0)));
      bars += `<rect class="bar ${s.cls(v)}" data-r="${i}" data-s="${j}" x="${b0 + gap / 2 + j * w}" y="${top}" width="${w}" height="${hgt}"/>`;
    });
  });
  el.innerHTML = svgOpen(f, label) + yGrid(f, y, ticks) + xAxis(f, x, timeTicks(t0, t1, Math.max(3, Math.floor((f.x1 - f.x0) / 90)))) +
    bars + `<line class="zero" x1="${f.x0}" x2="${f.x1}" y1="${y(0)}" y2="${y(0)}"/></svg>`;
  el.querySelectorAll(".bar").forEach((b) => {
    const r = rows[+b.dataset.r];
    b.addEventListener("mouseenter", (ev) => showTip(monthLabel(ms(r.month)),
      series.map((x) => [x.label, `${cur()} ${money(r[x.key] || 0)}`, (r[x.key] || 0) < 0 ? "alert" : ""]), ev));
    b.addEventListener("mousemove", moveTip);
    b.addEventListener("mouseleave", hideTip);
  });
}

function realizedChart() {
  const el = $("#realized-chart"), rows = state.data.realized_monthly;
  $("#rea-note").textContent = `Per month · ${cur()}`;
  if (!rows.length || !rows.some((r) => Math.abs(r.pnl) > 0.005)) { el.innerHTML = empty("No realized gains or losses yet"); return; }
  monthBars(el, rows, [{ key: "pnl", label: "Realized", cls: (v) => (v < 0 ? "loss" : "ink") }], 300, "Realized profit and loss per month");
}

function turnoverChart() {
  const el = $("#turnover"), rows = state.data.turnover_monthly;
  $("#tur-note").textContent = `Gross value traded per month · ${cur()}`;
  if (!rows.length) { el.innerHTML = empty("No trades yet"); return; }
  monthBars(el, rows, [{ key: "buy", label: "Buys", cls: () => "ink" }, { key: "sell", label: "Sells", cls: () => "grey" }],
    240, "Monthly turnover, buys and sells");
}

// --- Flags ------------------------------------------------------------------------------------
function flags() {
  const el = $("#flags"), fl = state.data.flags;
  el.hidden = !fl.length;
  if (!fl.length) return;
  const open = el.open;
  el.innerHTML = `<summary>Flags · ${fl.length} — positions that don't add up, trades missing a document</summary>` +
    `<div class="list">${fl.map((f) => `<div class="flag"><span class="id">${esc(f.id)}</span><span>${esc(f.message)}</span></div>`).join("")}</div>`;
  el.open = open;
}

// --- Records ------------------------------------------------------------------------------------
const F = {
  text: (v) => esc(v ?? ""), int: (v) => money(v, 0), money: (v) => money(v), pct1: (v) => pct(v), pct2: (v) => pct(v, 2),
  share: (v) => share(v), date: (v) => esc(v ?? ""), datetime: (v) => (v ? localTime(v) : "—"),
};
const TABS = {
  holdings: { label: "Holdings", rows: () => state.data.holdings, cols: [
    ["symbol", "Symbol", "text"], ["quantity", "Qty", "int"], ["avg_cost", "Avg cost", "money"], ["cost_basis", "Cost basis", "money"],
    ["price", "Price", "money"], ["day_change", "Today", "pct2", true], ["market_value", "Mkt value", "money"],
    ["unrealized_pnl", "Unrealized", "money", true], ["unrealized_pct", "Unreal. %", "pct1", true], ["weight", "Weight", "share"],
    ["traded_at", "Last trade", "datetime"], ["source", "Price source", "text"]] },
  transactions: { label: "Transactions", rows: () => state.data.transactions, cols: [
    ["trade_date", "Date", "date"], ["side", "Side", "text"], ["symbol", "Symbol", "text"], ["quantity", "Qty", "int"],
    ["price", "Price", "money"], ["gross_amount", "Gross", "money"], ["fees", "Fees", "money"], ["net_amount", "Net", "money"],
    ["contract_no", "Contract", "text"], ["sources", "Sources", "text"]] },
  realized: { label: "Realized", rows: () => state.data.realized, cols: [
    ["trade_date", "Date", "date"], ["symbol", "Symbol", "text"], ["quantity", "Qty", "int"], ["proceeds", "Proceeds", "money"],
    ["cost", "Cost", "money"], ["pnl", "P&L", "money", true]] },
  cash: { label: "Cash", rows: () => state.data.cash, cols: [
    ["date", "Date", "date"], ["kind", "Kind", "text"], ["ref", "Reference", "text"], ["amount", "Amount", "money"],
    ["description", "Description", "text"], ["sources", "Sources", "text"]] },
  documents: { label: "Documents", rows: () => state.data.documents, cols: [
    ["email_date", "Email date", "date"], ["attachment_name", "File", "text"], ["doc_type", "Type", "text"],
    ["layout", "Layout", "text"], ["parse_count", "Records", "int"], ["error", "Error", "text"]] },
};
const NUMERIC = new Set(["int", "money", "pct1", "pct2", "share"]);

function tabs() {
  $("#tabs").innerHTML = Object.entries(TABS).map(([k, t]) =>
    `<button type="button" role="tab" data-tab="${k}" aria-selected="${state.tab === k}">${t.label}<span class="count">${t.rows().length}</span></button>`).join("");
  $("#tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { state.tab = b.dataset.tab; tabs(); table(); }));
}

function table() {
  const t = TABS[state.tab], q = state.filter.trim().toUpperCase();
  let rows = t.rows();
  if (q && t.cols.some(([k]) => k === "symbol")) rows = rows.filter((r) => String(r.symbol).toUpperCase().includes(q));
  const sort = state.sort[state.tab];
  if (sort) {
    rows = [...rows].sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      const c = x == null ? 1 : y == null ? -1 : typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return sort.dir * c;
    });
  }
  const head = t.cols.map(([k, label, fmt]) => `<th class="${NUMERIC.has(fmt) ? "num" : ""}" data-key="${k}"
    aria-sort="${sort?.key === k ? (sort.dir > 0 ? "ascending" : "descending") : "none"}">${esc(label)}</th>`).join("");
  const body = rows.map((r, i) => `<tr data-i="${i}" class="${state.tab === "documents" && r.has_text ? "clickable" : ""}">` +
    t.cols.map(([k, , fmt, signed]) => `<td class="${NUMERIC.has(fmt) ? "num" : ""} ${signed && r[k] < 0 ? "alert" : ""}">${F[fmt](r[k])}</td>`).join("") +
    "</tr>").join("");
  $("#table").innerHTML = rows.length ? `<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>` : empty("Nothing to show");
  $("#table").querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
    const prev = state.sort[state.tab];
    state.sort[state.tab] = { key: th.dataset.key, dir: prev?.key === th.dataset.key ? -prev.dir : -1 };
    table();
  }));
  if (state.tab === "documents") {
    $("#table").querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", () => showDocument(rows[+tr.dataset.i])));
  }
}

async function showDocument(doc) {
  const r = await fetch(`/api/documents/${doc.id}/text`);
  $("#doc-title").textContent = `${doc.email_date} · ${doc.attachment_name}`;
  $("#doc-text").textContent = r.ok ? await r.text() : "No extracted text.";
  $("#doc").hidden = false;
  $("#doc").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// --- Data loading -------------------------------------------------------------------------------
function render() {
  header(); readouts(); network(); positionIndex(); capital(); realizedChart(); turnoverChart(); flags(); tabs(); table();
  const m = state.data.meta, asof = $("#asof");
  if (m.first_trade) { asof.min = m.first_trade; asof.max = m.last_trade; asof.value = m.as_of; }
  if (!m.documents) $("#readouts").innerHTML = empty("No statements yet — run `smpa run`, then reload this page.");
}

async function load(asOf) {
  try {
    const r = await fetch(`/api/portfolio${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`);
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    state.data = await r.json();
    state.asOf = state.data.meta.as_of;
    render();
  } catch (err) {
    $("#readouts").innerHTML = `<div class="empty alert">Could not load data: ${esc(err.message)}</div>`;
  }
}

async function refreshPrices() {
  const btn = $("#refresh");
  btn.disabled = true;
  btn.textContent = "Fetching…";
  try {
    const r = await fetch("/api/prices", { method: "POST", headers: { "X-SMPA": "1" } });
    const body = await r.json();
    if (!r.ok) throw new Error(body.error || r.statusText);
    await load(state.asOf);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Refresh";
    const t = $("#price-time");
    t.textContent = `Refresh failed: ${err.message}`;
    t.className = "alert";
  }
}

$("#asof").addEventListener("change", (e) => { if (e.target.value) load(e.target.value); });
$("#filter").addEventListener("input", (e) => { state.filter = e.target.value; table(); });
$("#doc-close").addEventListener("click", () => { $("#doc").hidden = true; });
let lastWidth = 0, raf = 0;
new ResizeObserver(([entry]) => {
  const w = Math.round(entry.contentRect.width);
  if (!state.data || w === lastWidth) return;
  lastWidth = w;
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(() => { network(); capital(); realizedChart(); turnoverChart(); });
}).observe(document.querySelector(".page"));
load();
