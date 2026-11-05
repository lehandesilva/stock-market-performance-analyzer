// Portfolio Monitor front end: vanilla JS + SVG, no dependencies.

import {
  $, DAY, F, MON, NUMERIC, addMonths, box, compact, day, empty, esc, frame, hideTip, isNum, linear, localTime, money, monthLabel, moveTip, ms, niceTicks, pad2, pathOf, pct, plural, share, showTip, signed, svgOpen, svgPoint, symLink, timeTicks, tip, when, xAxis, yGrid,
} from "./lib.js";
import { redrawTicker, renderTicker } from "./ticker.js";
import { drawPerformance, renderPerformance } from "./views.js";
import { renderHabits } from "./habits.js";

const state = { data: null, asOf: null, filter: "", tab: "holdings", sort: {}, hl: null, range: null, valueMode: "value" };

const cur = () => state.data.meta.currency;

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
    return box(`${label} · ${c}`, v, sub, alert);
  };
  const unpriced = r.positions - r.priced, up = r.unrealized_pct;
  $("#readouts").innerHTML = [
    cell("Net invested", r.net_invested, "Buys − sell proceeds", false),
    cell("Open cost basis", r.cost_basis, plural(r.positions, "position"), false),
    cell("Market value", r.market_value, `${r.priced}/${r.positions} priced`, unpriced > 0),
    cell("Unrealized P&L", r.unrealized, isNum(up) ? `${up >= 0 ? "▲" : "▼"} ${pct(up, 2)} vs cost` : "—", isNum(up) && up < 0),
    cell("Realized P&L", r.realized, plural(r.sells, "sell"), r.realized < 0),
    cell("Dividends", r.dividends, r.dividend_count ? plural(r.dividend_count, "payment") : "None recorded", false),
    returnCell(r, box, unpriced),
    cell("Fees paid", r.fees, r.turnover ? `${share(r.fees / r.turnover, 2)} of turnover` : "—", false),
    cell("If sold", r.liquidation_value, `After ${share(feeRate(), 3)} selling fees`, false),
    gainIfSoldCell(r, box, unpriced),
    twrCell(),
    alphaCell(),
  ].join("");
}

// Time-weighted: the return of your picks with the timing of your money taken out (docs/04 §1).
function twrCell() {
  const t = state.data.twr;
  if (!t) return box("Time-weighted", "—", "Needs a daily value: run smpa prices", false);
  return box(t.annualized ? "Time-weighted · a year" : "Time-weighted · not annualized", esc(pct(t.return, 2)),
    `Your picks since ${day(ms(t.start))}`, t.return < 0);
}

// Direct alpha against the ASPI over chart 03's window: a yearly return over the index (docs/04 §5).
function alphaCell() {
  const b = state.data.benchmark;
  if (!b || !isNum(b.direct_alpha)) return box("vs the ASPI · direct alpha", "—", "Needs ASPI history", false);
  const short = b.days < 365, v = short ? b.alpha_period : b.direct_alpha;
  return box(short ? "vs the ASPI · not annualized" : "vs the ASPI · a year", esc(pct(v, 2)),
    isNum(b.ks_pme) ? `KS-PME ${b.ks_pme.toFixed(2)} · ${b.ks_pme >= 1 ? "beat it" : "behind it"}` : `Since ${day(ms(b.start))}`, v < 0);
}

const feeRate = () => state.data.meta.fee_rate.rate;

// Total gain and return as if everything had been sold on the valuation day, after selling fees.
function gainIfSoldCell(r, box, unpriced) {
  const short = r.return_days < 365, rate = short ? r.period_return_if_sold : r.xirr_if_sold;
  const v = `${r.gain_if_sold < 0 ? "−" : "+"}${money(Math.abs(r.gain_if_sold), 0)}`;
  const sub = isNum(rate) ? `${pct(rate, 2)}${short ? "" : " a year"} after fees` : "Realized + dividends + if sold";
  return box(`Gain if sold · ${cur()}`, `${unpriced ? "≈" : ""}${esc(v)}`, sub, r.gain_if_sold < 0 || unpriced > 0);
}

// Money-weighted return (XIRR). Under a year an annual rate exaggerates, so show the plain return instead.
function returnCell(r, box, unpriced) {
  if (!isNum(r.xirr)) return box("Annual return · XIRR", "—", "Needs trades and a value", false);
  const short = r.return_days < 365, rate = short ? r.period_return : r.xirr;
  const gain = `${r.total_gain < 0 ? "−" : "+"}${money(Math.abs(r.total_gain), 0)} ${cur()} incl. dividends`;
  return box(short ? "Return · under a year" : "Annual return · XIRR",
    `${unpriced ? "≈" : ""}${esc(pct(rate, 2))}`, short ? `${gain} · not annualized` : gain, rate < 0 || unpriced > 0);
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
    g.addEventListener("click", () => openTicker(h.symbol));
  });
}

function holdingTip(h, ev) {
  showTip(h.symbol, [
    ["Qty", money(h.quantity, 0)], ["Avg cost", money(h.avg_cost)], ["Break-even", money(h.break_even)],
    ["Price", money(h.price)], ...(h.price_at ? [["Price at", when(h.price_at)]] : []),
    ["Value", `${cur()} ${money(h.value)}`], ...(h.priced ? [["If sold", `${cur()} ${money(h.liquidation_value)}`]] : []),
    ["Weight", share(h.weight)],
    ["Unrealized", pct(h.unrealized_pct), h.unrealized_pnl < 0 ? "alert" : ""],
    ["Today", pct(h.day_change, 2), h.day_change < 0 ? "alert" : ""],
    ...(h.dividends > 0 ? [["Dividends", `${cur()} ${money(h.dividends)}`]] : []),
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
    tr.addEventListener("click", () => openTicker(h.symbol));
  });
}

// --- 03 Value vs invested ------------------------------------------------------------------
// Net invested (buys − sale proceeds) as a step line; market value from daily price history; and what the
// same money would be worth in the ASPI over the stretch where both are known.
function rateText(rate, period, days) {
  if (!isNum(rate)) return "—";
  return days < 365 ? pct(period, 1) : `${pct(rate, 1)} a year`;
}

// Your money-weighted return over the window, the ASPI's own move, and direct alpha: the fair comparison
// (every flow grown at the index), which the dashed line can't give once a big sale has pushed it to zero.
function valueNote() {
  const b = state.data.benchmark, n = $("#val-note"), foot = $("#val-foot");
  foot.hidden = !(b && b.stopped && state.valueMode === "value");
  if (b && b.stopped) {
    foot.textContent = `The ASPI line stops on ${day(ms(b.stopped))}: sales took out more than the same money had in the index, ` +
      "so it would go below zero (a known flaw of replaying your cash flows in the index). Direct alpha above stays fair.";
  }
  if (!b) {
    n.textContent = state.data.history.length ? `${cur()} · ASPI comparison needs index history` : `${cur()} · market value needs price history: run smpa prices`;
    return;
  }
  const idx = b.days < 365 ? pct(b.index_return, 1) : `${pct((1 + b.index_return) ** (365 / b.days) - 1, 1)} a year`;
  n.textContent = `Since ${day(ms(b.start))} · you ${rateText(b.xirr, b.period_return, b.days)} · ASPI ${idx}` +
    (isNum(b.direct_alpha) ? ` · direct alpha ${rateText(b.direct_alpha, b.alpha_period, b.days)}` : "");
}

const KEYS = {
  value: [["line", "Market value"], ["line grey", "Net invested"], ["line dash", "Same money in the ASPI"]],
  gain: [["line", "Total gain if all sold that day"], ["line grey", "Realized + dividends so far"]],
};

function valueChart() {
  const el = $("#value"), d = state.data, mode = state.valueMode;
  const inv = d.capital.map((p) => ({ t: ms(p.date), v: p.value }));
  const val = d.history.map((p) => ({ t: ms(p.date), v: p.value, b: p.benchmark, g: p.gain_if_sold, l: p.locked }));
  valueNote();
  $("#value-keys").innerHTML = KEYS[mode].map(([k, label]) => `<span class="item"><span class="key ${k}"></span>${label}</span>`).join("");
  document.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  if (!inv.length) { el.innerHTML = empty("No trades yet"); return; }
  const last = Math.max(inv.at(-1).t, val.length ? val.at(-1).t : 0);
  const range = state.range ?? (val.length && val[0].t - inv[0].t > 400 * DAY ? "1y" : "all");
  document.querySelectorAll("[data-range]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.range === range)));
  let t0 = range === "1y" ? Math.max(inv[0].t, last - 365 * DAY) : inv[0].t, t1 = last;
  if (t1 - t0 < 180 * DAY) { t0 = addMonths(t0, -2); t1 = addMonths(t1, 3); }
  if (mode === "gain") { gainChart(el, val, t0, t1); return; }
  const stepAt = (t) => { let v = null; for (const p of inv) { if (p.t <= t) v = p.v; else break; } return v; };
  const from = Math.max(t0, inv[0].t), shown = val.filter((p) => p.t >= t0 && p.t <= t1);
  const steps = [{ t: from, v: stepAt(from) }, ...inv.filter((p) => p.t > from), { t: last, v: inv.at(-1).v }];
  const vals = [...steps.map((p) => p.v), ...shown.map((p) => p.v), ...shown.filter((p) => isNum(p.b)).map((p) => p.b)];
  const f = frame(el, 320), ticks = niceTicks(Math.min(0, ...vals), Math.max(...vals));
  const x = linear([t0, t1], [f.x0, f.x1]), y = linear([ticks[0], ticks.at(-1)], [f.y0, f.y1]);
  let step = `M${x(steps[0].t)},${y(steps[0].v)}`;
  for (const p of steps.slice(1)) step += `H${x(p.t)}V${y(p.v)}`;
  const line = (pts, key) => {
    let out = "", prev = null;
    for (const p of pts) {
      if (!isNum(p[key])) { prev = null; continue; }
      out += `${prev === null || p.t - prev > 31 * DAY ? "M" : "L"}${x(p.t)},${y(p[key])}`;
      prev = p.t;
    }
    return out;
  };
  el.innerHTML = svgOpen(f, "Market value, net invested and the same money in the ASPI over time") + yGrid(f, y, ticks) +
    xAxis(f, x, timeTicks(t0, t1, Math.max(3, Math.floor((f.x1 - f.x0) / 90)))) +
    `<path class="series grey" d="${step}"/><path class="series dash" d="${line(shown, "b")}"/>` +
    `<path class="series" d="${line(shown, "v")}"/><g class="hover" visibility="hidden"><line class="cross vx" y1="${f.y1}" y2="${f.y0}"/>` +
    `<rect class="reticle" width="7" height="7"/></g>` +
    `<rect class="hitbox" x="${f.x0}" y="${f.y1}" width="${f.x1 - f.x0}" height="${f.y0 - f.y1}"/></svg>`;
  const svg = el.querySelector("svg"), g = svg.querySelector(".hover"), hit = svg.querySelector(".hitbox");
  hit.addEventListener("mousemove", (ev) => {
    const px = svgPoint(svg, f, ev).x, t = t0 + ((px - f.x0) / (f.x1 - f.x0)) * (t1 - t0);
    if (t < inv[0].t || t > last) { g.setAttribute("visibility", "hidden"); hideTip(); return; }
    let p = null;
    for (const q of shown) { if (q.t <= t) p = q; else break; }
    if (p && t - p.t > 7 * DAY) p = null;
    const invested = stepAt(t), at = p ? p.v : invested;
    g.setAttribute("visibility", "visible");
    g.querySelector(".vx").setAttribute("x1", px); g.querySelector(".vx").setAttribute("x2", px);
    g.querySelector(".reticle").setAttribute("x", px - 3.5); g.querySelector(".reticle").setAttribute("y", y(at) - 3.5);
    const rows = [["Market value", p ? `${cur()} ${money(p.v)}` : "—"], ["Net invested", `${cur()} ${money(invested)}`]];
    if (p && isNum(p.b)) rows.push(["In the ASPI", `${cur()} ${money(p.b)}`, p.b > p.v ? "alert" : ""]);
    showTip(day(p ? p.t : t), rows, ev);
  });
  hit.addEventListener("mouseleave", () => { g.setAttribute("visibility", "hidden"); hideTip(); });
}


// Total gain had everything been sold that day, after that day's selling fees: realized P&L and dividends so
// far plus the unrealized gain net of fees. The grey line is the part already locked in.
function gainChart(el, val, t0, t1) {
  const shown = val.filter((p) => p.t >= t0 && p.t <= t1 && isNum(p.g));
  if (!shown.length) { el.innerHTML = empty("Needs price history: run smpa prices"); return; }
  const vals = shown.flatMap((p) => [p.g, p.l]);
  const f = frame(el, 320), ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
  const x = linear([t0, t1], [f.x0, f.x1]), y = linear([ticks[0], ticks.at(-1)], [f.y0, f.y1]);
  el.innerHTML = svgOpen(f, "Total gain if everything had been sold on each day") + yGrid(f, y, ticks) +
    xAxis(f, x, timeTicks(t0, t1, Math.max(3, Math.floor((f.x1 - f.x0) / 90)))) +
    `<line class="zero" x1="${f.x0}" x2="${f.x1}" y1="${y(0)}" y2="${y(0)}"/>` +
    `<path class="series grey" d="${pathOf(shown, "l", x, y)}"/><path class="series" d="${pathOf(shown, "g", x, y)}"/>` +
    `<g class="hover" visibility="hidden"><line class="cross vx" y1="${f.y1}" y2="${f.y0}"/><rect class="reticle" width="7" height="7"/></g>` +
    `<rect class="hitbox" x="${f.x0}" y="${f.y1}" width="${f.x1 - f.x0}" height="${f.y0 - f.y1}"/></svg>`;
  const svg = el.querySelector("svg"), g = svg.querySelector(".hover");
  svg.querySelector(".hitbox").addEventListener("mousemove", (ev) => {
    const px = svgPoint(svg, f, ev).x, t = t0 + ((px - f.x0) / (f.x1 - f.x0)) * (t1 - t0);
    let p = null;
    for (const q of shown) { if (q.t <= t) p = q; else break; }
    if (!p || t - p.t > 7 * DAY) { g.setAttribute("visibility", "hidden"); hideTip(); return; }
    g.setAttribute("visibility", "visible");
    g.querySelector(".vx").setAttribute("x1", x(p.t)); g.querySelector(".vx").setAttribute("x2", x(p.t));
    g.querySelector(".reticle").setAttribute("x", x(p.t) - 3.5); g.querySelector(".reticle").setAttribute("y", y(p.g) - 3.5);
    showTip(`If sold on ${day(p.t)}`, [["Total gain", `${cur()} ${money(p.g)}`, p.g < 0 ? "alert" : ""],
      ["Realized + dividends", `${cur()} ${money(p.l)}`], ["Unrealized, after fees", `${cur()} ${money(p.g - p.l)}`, p.g < p.l ? "alert" : ""]], ev);
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
  monthBars(el, rows, [{ key: "pnl", label: "Realized", cls: (v) => (v < 0 ? "loss" : "ink") }], 260, "Realized profit and loss per month");
}

function turnoverChart() {
  const el = $("#turnover"), rows = state.data.turnover_monthly;
  $("#tur-note").textContent = `Gross value traded per month · ${cur()}`;
  if (!rows.length) { el.innerHTML = empty("No trades yet"); return; }
  monthBars(el, rows, [{ key: "buy", label: "Buys", cls: () => "ink" }, { key: "sell", label: "Sells", cls: () => "grey" }],
    260, "Monthly turnover, buys and sells");
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

// Things worth a look today: below break-even, 52-week lows, big moves, announcements (with a row to add).
function alertsPanel() {
  const el = $("#alerts"), al = state.data.alerts;
  el.hidden = !al.length;
  if (!al.length) return;
  const open = el.dataset.drawn ? el.open : true;
  el.innerHTML = `<summary>Alerts · ${al.length} — below break-even, 52-week lows, big moves, announcements</summary>` +
    `<div class="list">${al.map((a) => `<div class="flag"><span class="id">${esc(a.kind)}</span><span>${esc(a.message).replace(esc(a.symbol), symLink(a.symbol))}</span></div>`).join("")}</div>`;
  el.open = open;
  el.dataset.drawn = "1";
}

// --- Records ------------------------------------------------------------------------------------
const TABS = {
  holdings: { label: "Holdings", rows: () => state.data.holdings, cols: [
    ["symbol", "Symbol", "sym"], ["quantity", "Qty", "int"], ["avg_cost", "Avg cost", "money"],
    ["break_even", "Break-even", "money"], ["cost_basis", "Cost basis", "money"],
    ["price", "Price", "money"], ["day_change", "Today", "pct2", true], ["market_value", "Mkt value", "money"],
    ["liquidation_value", "If sold", "money"],
    ["unrealized_pnl", "Unrealized", "money", true], ["unrealized_pct", "Unreal. %", "pct1", true],
    ["dividends", "Dividends", "cash"], ["weight", "Weight", "share"],
    ["price_at", "Price at", "when"], ["source", "Price source", "text"]] },
  transactions: { label: "Transactions", rows: () => state.data.transactions, cols: [
    ["trade_date", "Date", "date"], ["side", "Side", "text"], ["symbol", "Symbol", "sym"], ["quantity", "Qty", "int"],
    ["price", "Price", "money"], ["gross_amount", "Gross", "money"], ["fees", "Fees", "money"], ["net_amount", "Net", "money"],
    ["contract_no", "Contract", "text"], ["sources", "Sources", "text"]] },
  realized: { label: "Realized", rows: () => state.data.realized, cols: [
    ["trade_date", "Date", "date"], ["symbol", "Symbol", "sym"], ["quantity", "Qty", "int"], ["proceeds", "Proceeds", "money"],
    ["cost", "Cost", "money"], ["pnl", "P&L", "money", true]] },
  sales: { label: "Sales", rows: () => state.data.sales, summary: salesSummary,
    empty: "No market sales yet", cols: [
    ["date", "Sold", "date"], ["symbol", "Symbol", "sym"], ["quantity", "Qty", "int"], ["price", "Price", "money"],
    ["avg_cost", "Avg cost", "money"], ["gain_pct", "Gain", "pct1", true], ["high", "High held", "money"],
    ["efficiency", "Exit eff.", "share", true], ["after_1", "+1M", "pct1", true], ["after_3", "+3M", "pct1", true],
    ["after_6", "+6M", "pct1", true], ["after_12", "+12M", "pct1", true], ["vs_3", "vs ASPI 3M", "pct1", true],
    ["vs_12", "vs ASPI 12M", "pct1", true], ["left_12", "Left · 12M", "money", true]] },
  cash: { label: "Cash", rows: () => state.data.cash, cols: [
    ["date", "Date", "date"], ["kind", "Kind", "text"], ["ref", "Reference", "text"], ["amount", "Amount", "money"],
    ["description", "Description", "text"], ["sources", "Sources", "text"]] },
  actions: { label: "Corp. actions", rows: () => state.data.actions,
    empty: "No corporate actions — add dividends, bonus issues, splits and rights to data/corporate_actions.csv", cols: [
    ["date", "Date", "date"], ["symbol", "Symbol", "sym"], ["action", "Action", "text"], ["detail", "Detail", "text"],
    ["shares", "Shares", "qty"], ["amount", "Cash", "cash", true], ["note", "Note", "text"], ["recorded_on", "Recorded", "date"]] },
  documents: { label: "Documents", rows: () => state.data.documents, cols: [
    ["email_date", "Email date", "date"], ["attachment_name", "File", "text"], ["doc_type", "Type", "text"],
    ["layout", "Layout", "text"], ["parse_count", "Records", "int"], ["error", "Error", "text"]] },
};

// What the stocks you sold did next, averaged over every sale (docs/03 §4: judge from the average, never one trade).
function salesSummary() {
  const s = state.data.sales_summary;
  if (!s.sales) return "";
  const rows = s.windows.map((w) => `<tr><th>${w.months} month${w.months > 1 ? "s" : ""} later</th><td class="num">${w.n}</td>
    <td class="num ${w.after < 0 ? "alert" : ""}">${pct(w.after)}</td><td class="num ${w.vs > 0 ? "alert" : ""}">${pct(w.vs)}</td>
    <td class="num">${isNum(w.beat) ? share(w.beat, 0) : "—"}</td><td class="num ${w.left > 0 ? "alert" : ""}">${isNum(w.left) ? signed(w.left) : "—"}</td></tr>`).join("");
  return `<div class="tab-summary"><div class="scroll-x"><table class="data compact"><thead><tr><th>After the sale · averages</th><th class="num">Sales</th>
    <th class="num">Its return</th><th class="num">vs the ASPI</th><th class="num">Beat the ASPI</th><th class="num">Left on the table</th></tr></thead>
    <tbody>${rows}</tbody></table></div><p class="note">Positive "vs the ASPI" means what you sold went on to beat the market: on average you'd have
    done better keeping it. Left on the table is shares sold × (later close − sale price); negative is a loss you avoided.
    Median exit efficiency ${share(s.efficiency, 0)} over ${plural(s.efficiency_n, "sale")} at a gain: the share of the best move while held that
    each sale kept, in hindsight. Nobody sells at the top, so read it as a pattern, not a grade. Small samples: ${plural(s.sales, "sale")} in all.</p></div>`;
}

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
  $("#table").innerHTML = (t.summary && !q ? t.summary() : "") +
    (rows.length ? `<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>` : empty(t.empty && !t.rows().length ? t.empty : "Nothing to show"));
  $("#table").querySelectorAll("th[data-key]").forEach((th) => th.addEventListener("click", () => {
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
  header(); readouts(); network(); positionIndex(); valueChart(); realizedChart(); turnoverChart(); alertsPanel(); flags(); tabs(); table();
  const m = state.data.meta, asof = $("#asof");
  if (m.first_trade) { asof.min = m.first_trade; asof.max = m.max_date; asof.value = m.as_of; }
  $("#asof-note").textContent = m.past ? `Valued at each stock's last close on or before ${day(ms(m.as_of))}; ` +
    "prices.csv overrides apply to today only" : "";
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

// --- Views: #/ is the overview, #/t/SYMBOL a stock's page (?sold=YYYY-MM-DD for "if sold on") ----------
const openTicker = (sym, sold) => { location.hash = `#/t/${encodeURIComponent(sym)}${sold ? `?sold=${sold}` : ""}`; };

const PAGES = { performance: { url: "/api/performance", render: renderPerformance },
  habits: { url: "/api/habits", render: renderHabits } };

function show(name) {
  for (const id of ["overview", "ticker", ...Object.keys(PAGES)]) $(`#${id}`).hidden = id !== name;
  document.querySelectorAll("#views a").forEach((a) => a.setAttribute("aria-current", String(a.dataset.view === name)));
}

async function showPage(name) {
  const el = $(`#${name}`), page = PAGES[name];
  show(name);
  if (!el.dataset.loaded) { el.innerHTML = empty("Working it out…"); scrollTo(0, 0); }
  try {
    const r = await fetch(page.url);
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    page.render(el, await r.json());
    el.dataset.loaded = "1";
  } catch (err) {
    el.innerHTML = `<div class="empty alert">Could not load this page: ${esc(err.message)}</div>`;
  }
}

async function route() {
  const m = location.hash.match(/^#\/t\/([^?]+)(?:\?sold=(\d{4}-\d{2}-\d{2}))?$/);
  const page = location.hash.match(/^#\/([a-z]+)$/);
  if (page && !PAGES[page[1]]) { location.hash = "#/"; return; }
  hideTip();
  if (page) { showPage(page[1]); return; }
  show(m ? "ticker" : "overview");
  if (!m) { if (state.data) render(); return; }
  const sym = decodeURIComponent(m[1]), el = $("#ticker"), same = el.dataset.sym === sym;
  if (!same) { el.innerHTML = empty(`Loading ${sym}…`); scrollTo(0, 0); }
  try {
    const r = await fetch(`/api/ticker/${encodeURIComponent(sym)}${m[2] ? `?sold_on=${m[2]}` : ""}`);
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    const y = scrollY;
    renderTicker(el, await r.json(), openTicker);
    el.dataset.sym = sym;
    if (same) scrollTo(0, y);
  } catch (err) {
    el.innerHTML = `<nav class="crumbs"><a href="#/">← Overview</a></nav><div class="empty alert">Could not load ${esc(sym)}: ${esc(err.message)}</div>`;
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
document.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => {
  state.range = b.dataset.range;
  if (state.data) valueChart();
}));
document.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
  state.valueMode = b.dataset.mode;
  if (state.data) valueChart();
}));
let lastWidth = 0, raf = 0;
new ResizeObserver(([entry]) => {
  const w = Math.round(entry.contentRect.width);
  if (!state.data || w === lastWidth) return;
  lastWidth = w;
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(() => {
    if (!$("#ticker").hidden) { redrawTicker(); return; }
    if (!$("#performance").hidden) { drawPerformance(); return; }
    network(); valueChart(); realizedChart(); turnoverChart();
  });
}).observe(document.querySelector(".page"));
window.addEventListener("hashchange", route);
load().then(() => { if (location.hash.length > 2) route(); else show("overview"); });
