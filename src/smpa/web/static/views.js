// The Performance page (#/performance): how well, against what, and for how much risk.

import {
  $, DAY, MON, box, dataTable, day, empty, esc, frame, hideTip, isNum, linear, money, ms, niceTicks, pathOf, pct,
  plural, share, showTip, signed, svgOpen, svgPoint, timeTicks, xAxis, yGrid,
} from "./lib.js";

const view = { p: null, sort: {} };
const fix = (v, dp = 2) => (isNum(v) ? v.toFixed(dp) : "—");
const yr = (v, annual = true) => (isNum(v) ? `${pct(v, 1)}${annual ? "" : ""}` : "—");

export function renderPerformance(el, p) {
  view.p = p;
  if (p.empty) { el.innerHTML = empty("No trades yet"); return; }
  if (!p.window) {
    el.innerHTML = `<section class="ticker-title"><h2>Performance</h2></section>` +
      empty("Needs a daily value for the portfolio: run smpa prices (it downloads a year of closes), then reload");
    return;
  }
  const w = p.window, files = p.files;
  el.innerHTML = `
    <section class="ticker-title"><h2>Performance</h2><div class="muted">Since ${day(ms(w.start))} · ${plural(w.days, "day")} with a value for every holding · ${fix(p.trading_days_per_year, 0)} trading days a year</div></section>
    <section class="readouts" id="p-readouts"></section>
    <section class="panel"><div class="sec"><span class="n">P1</span><span class="t">Growth of 100</span><span class="r">Your picks (time-weighted) against the benchmarks</span></div>
      <div class="legend wrap" id="p-keys"></div><div class="plot" id="p-growth"></div></section>
    <div class="row r11">
      <section class="panel"><div class="sec"><span class="n">P2</span><span class="t">Against the benchmarks</span><span class="r">Same cash flows, same days</span></div>
        <div class="table-wrap" id="p-bench"></div>
        <p class="note">KS-PME: what you got back (and still hold) over what you put in, every amount grown at the index to the end. Above 1 beat it.
          Direct alpha: the same, as a yearly return over the index. Neither can go negative, unlike the ASPI line on the overview.
          ${files["tbill.csv"] ? "" : "Add data/tbill.csv (date, yield in percent) to compare with T-bills."}
          ${files["indices.csv"] ? "" : "Add data/indices.csv (name, date, value) for the total-return ASTRI: the ASPI leaves dividends out."}</p></section>
      <section class="panel"><div class="sec"><span class="n">P3</span><span class="t">Risk</span><span class="r">On the time-weighted series, daily</span></div>
        <div id="p-risk"></div></section>
    </div>
    <section class="panel"><div class="sec"><span class="n">P4</span><span class="t">Monthly returns</span><span class="r">Modified Dietz · year: months chained</span></div>
      <div class="table-wrap scroll-x" id="p-months"></div></section>
    <div class="row r11">
      <section class="panel"><div class="sec"><span class="n">P5</span><span class="t">Where the risk is</span><span class="r" id="p-conc-note"></span></div>
        <div id="p-conc"></div><div class="table-wrap" id="p-holdings"></div></section>
      <section class="panel"><div class="sec"><span class="n">P6</span><span class="t">Where the return came from</span><span class="r" id="p-attr-note"></span></div>
        <div id="p-attr"></div><div class="table-wrap" id="p-sectors"></div><p class="note" id="p-sector-note"></p></section>
    </div>`;
  readouts();
  benchTable();
  riskTable();
  monthGrid();
  concentrationPanel();
  attributionPanel();
  drawPerformance();
}

export function drawPerformance() {
  if (view.p && view.p.window) growthChart($("#p-growth"));
}

function readouts() {
  const p = view.p, t = p.twr, r = p.risk, m = p.money, a = p.benchmarks.find((b) => b.name === "ASPI");
  const dd = r.drawdown, mk = r.market, v = r.var, b = p.bootstrap;
  const annual = t.annualized;
  $("#p-readouts").innerHTML = [
    box(annual ? "Time-weighted · a year" : "Time-weighted · not annualized", esc(pct(annual ? t.annual : t.return, 2)),
      "Your picks, money timing removed", (annual ? t.annual : t.return) < 0),
    box("Money-weighted · XIRR", esc(pct(t.xirr, 2)), "Your money, over the same days", t.xirr < 0),
    box("Timing effect", esc(pct(t.timing, 2)), isNum(t.timing) ? (t.timing >= 0 ? "Money tended to be in before rises" : "Money tended to arrive before falls") : "Needs a year", t.timing < 0),
    box("vs the ASPI · direct alpha", esc(pct(a?.direct_alpha, 1)), a ? `KS-PME ${fix(a.ks_pme)} · ASPI ${pct(a.return, 1)}${annual ? " a year" : ""}` : "Needs ASPI history", a?.direct_alpha < 0),
    box("Volatility · a year", esc(share(r.volatility)), `Downside ${share(r.downside)}`, false),
    box("Max drawdown", esc(pct(dd?.depth)), dd ? `${day(ms(dd.peak))} → ${day(ms(dd.trough))} · ${dd.recovered ? `back by ${day(ms(dd.recovered))}` : "not recovered"}` : "—", false),
    box("Sharpe", esc(fix(r.sharpe)), isNum(r.risk_free) ? `Sortino ${fix(r.sortino)} · T-bills ${pct(r.risk_free, 1)}` : "Needs data/tbill.csv", r.sharpe < 0),
    box("Beta on the ASPI", esc(fix(mk?.beta)), mk ? `R² ${fix(mk.r2)} · tracking error ${share(mk.tracking_error)}` : "Needs ASPI history", false),
    box("After inflation · XIRR", esc(pct(m.real, 2)), isNum(m.real) ? `Prices ${pct(m.inflation, 0)} since ${day(ms(m.since))}` : "Needs data/ccpi.csv", m.real < 0),
    box("In US dollars · XIRR", esc(pct(m.usd, 2)), isNum(m.usd) ? `Rupee ${pct(m.rupee_vs_usd, 0)} against the dollar` : "Needs data/fx.csv", m.usd < 0),
    box("A bad day · 1 in 20", v ? `${esc(signed(v.var))}` : "—", v ? `Worst 5% average ${signed(v.es)} · ${plural(v.observations, "day")}${v.observations < 500 ? " (short history)" : ""}` : "Needs 100 days", false),
    box("A year from now · range", b ? `${esc(pct(b.p5, 0))} … ${esc(pct(b.p95, 0))}` : "—",
      b ? `5th–95th percentile · ${share(b.loss_chance, 0)} of paths lose · not a forecast` : "Needs 250 days", false),
  ].join("");
}

const KEY = { You: "line", ASPI: "line dash", "T-bills": "line dot" };
const CLS = { You: "series", ASPI: "series dash", "T-bills": "series dot" };

// Growth of 100 in your picks (time-weighted, so new money doesn't move it) against each benchmark, rebased to
// 100 on the first day both have a value; the strip below is how far under its high the portfolio was.
function growthChart(el) {
  const g = view.p.growth, names = Object.keys(g[0]).filter((k) => k !== "date" && k !== "drawdown");
  $("#p-keys").innerHTML = names.map((n) => `<span class="item"><span class="key ${KEY[n] || "line grey"}"></span>${esc(n)}</span>`).join("") +
    `<span class="item"><span class="key loss"></span>Under the high</span>`;
  const pts = g.map((r) => ({ t: ms(r.date), ...r }));
  const t0 = pts[0].t, t1 = pts.at(-1).t;
  const W = frame(el, 360), f = { ...W, y0: 262 }, s = { ...W, y1: 284, y0: 334 };
  const vals = pts.flatMap((p) => names.map((n) => p[n])).filter(isNum);
  const ticks = niceTicks(Math.min(...vals), Math.max(...vals));
  const x = linear([t0, t1], [W.x0, W.x1]), y = linear([ticks[0], ticks.at(-1)], [f.y0, f.y1]);
  const ddMin = Math.min(-0.05, ...pts.map((p) => p.drawdown)), yd = linear([ddMin, 0], [s.y0, s.y1]);
  let area = `M${x(t0)},${yd(0)}`;
  for (const p of pts) area += `L${x(p.t)},${yd(p.drawdown)}`;
  area += `L${x(t1)},${yd(0)}Z`;
  el.innerHTML = `<svg viewBox="0 0 ${W.w} ${W.h}" height="${W.h}" role="img" aria-label="Growth of 100 against benchmarks, and drawdown">` +
    yGrid(f, y, ticks) + xAxis(W, x, timeTicks(t0, t1, Math.max(3, Math.floor((W.x1 - W.x0) / 90)))) +
    `<line class="gridline" x1="${W.x0}" x2="${W.x1}" y1="${y(100)}" y2="${y(100)}"/>` +
    names.filter((n) => n !== "You").map((n) => `<path class="${CLS[n] || "series grey"}" d="${pathOf(pts, n, x, y)}"/>`).join("") +
    `<path class="series" d="${pathOf(pts, "You", x, y)}"/>` +
    `<path class="dd" d="${area}"/><text x="${W.x0 - 8}" y="${yd(ddMin) + 3}" text-anchor="end">${pct(ddMin, 0)}</text>` +
    `<g class="hover" visibility="hidden"><line class="cross vx" y1="${f.y1}" y2="${s.y0}"/><rect class="reticle" width="7" height="7"/></g>` +
    `<rect class="hitbox" x="${W.x0}" y="${f.y1}" width="${W.x1 - W.x0}" height="${s.y0 - f.y1}"/></svg>`;
  const svg = el.querySelector("svg"), hov = svg.querySelector(".hover");
  svg.querySelector(".hitbox").addEventListener("mousemove", (ev) => {
    const tt = t0 + ((svgPoint(svg, W, ev).x - W.x0) / (W.x1 - W.x0)) * (t1 - t0);
    let p = null;
    for (const q of pts) { if (q.t <= tt) p = q; else break; }
    if (!p) { hov.setAttribute("visibility", "hidden"); hideTip(); return; }
    hov.setAttribute("visibility", "visible");
    hov.querySelector(".vx").setAttribute("x1", x(p.t)); hov.querySelector(".vx").setAttribute("x2", x(p.t));
    hov.querySelector(".reticle").setAttribute("x", x(p.t) - 3.5); hov.querySelector(".reticle").setAttribute("y", y(p.You) - 3.5);
    showTip(day(p.t), [...names.map((n) => [n, isNum(p[n]) ? fix(p[n], 1) : "—", n !== "You" && p[n] > p.You ? "alert" : ""]),
      ["Under the high", pct(p.drawdown), p.drawdown < 0 ? "alert" : ""]], ev);
  });
  svg.querySelector(".hitbox").addEventListener("mouseleave", () => { hov.setAttribute("visibility", "hidden"); hideTip(); });
}

function benchTable() {
  const rows = view.p.benchmarks.map((b) => ({ ...b, beat: isNum(b.ks_pme) ? (b.ks_pme >= 1 ? "Yes" : "No") : "—" }));
  const annual = view.p.twr.annualized;
  dataTable($("#p-bench"), [["name", "Benchmark", "text"], ["since", "Since", "date"], ["return", annual ? "Its return · a year" : "Its return", "pct1", true],
    ["ks_pme", "KS-PME", "ratio"], ["direct_alpha", "Direct alpha · a year", "pct1", true], ["beat", "Beat it?", "text"]],
  rows, view.sort.bench, (s) => { view.sort.bench = s; benchTable(); }, "No index history yet");
}

function riskTable() {
  const r = view.p.risk, mk = r.market || {}, dd = r.drawdown || {};
  const rows = [
    ["Volatility", share(r.volatility), "Typical size of the swings, a year"],
    ["Downside deviation", share(r.downside), "Swings that hurt only"],
    ["Max drawdown", pct(dd.depth), "Worst fall from a high: the number you actually feel"],
    ["Drawdown duration", isNum(dd.days) ? plural(dd.days, "day") : "—", dd.recovered ? "From the peak until back above it" : "Since the peak; not back yet"],
    ["Ulcer index", share(dd.ulcer), "Depth and length of drawdowns, as one number"],
    ["Sharpe", fix(r.sharpe), "Return above T-bills per unit of volatility"],
    ["Sortino", fix(r.sortino), "The same, counting only bad swings"],
    ["Calmar", fix(r.calmar), "Yearly return per unit of worst drawdown"],
    ["Beta · alpha · R²", `${fix(mk.beta)} · ${pct(mk.alpha)} · ${fix(mk.r2)}`, "Market exposure; return the ASPI doesn't explain (a year)"],
    ["Tracking error", share(mk.tracking_error), "How differently from the ASPI you move"],
    ["Information ratio", fix(r.information_ratio), "Return over the ASPI per unit of being different"],
  ];
  $("#p-risk").innerHTML = `<div class="scroll-x"><table class="compare wide"><tbody>${rows.map(([k, v, what]) =>
    `<tr><th>${esc(k)}</th><td class="num">${esc(v)}</td><td class="what">${esc(what)}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">${plural(r.observations, "daily return")} · annualized with ${fix(r.trading_days_per_year, 0)} trading days, counted from the ASPI's own calendar (US formulas assume 252).</p>`;
}

function monthGrid() {
  const rows = view.p.monthly;
  if (!rows.length) { $("#p-months").innerHTML = empty("Needs at least two months of values"); return; }
  const years = {};
  for (const r of rows) { const d = new Date(ms(r.month)); (years[d.getUTCFullYear()] ||= {})[d.getUTCMonth()] = r.return; }
  const cell = (v) => `<td class="num ${v < 0 ? "alert" : ""}">${isNum(v) ? pct(v, 1) : ""}</td>`;
  const body = Object.entries(years).map(([y, m]) => {
    const vals = Object.values(m).filter(isNum), total = vals.reduce((a, v) => a * (1 + v), 1) - 1;
    return `<tr><th>${y}</th>${MON.map((_, i) => cell(m[i])).join("")}${cell(vals.length ? total : null).replace("<td", '<td class="total"')}</tr>`;
  }).join("");
  $("#p-months").innerHTML = `<table class="data months"><thead><tr><th></th>${MON.map((m) => `<th class="num">${m}</th>`).join("")}<th class="num">Year</th></tr></thead><tbody>${body}</tbody></table>`;
}

function concentrationPanel() {
  const h = view.p.holdings, c = h.concentration, r = h.risk;
  $("#p-conc-note").textContent = r ? `Last year · ${plural(r.days, "day")}` : "";
  if (!c) { $("#p-conc").innerHTML = empty("No open positions"); return; }
  $("#p-conc").innerHTML = `<div class="stats">
    <div><span class="k">Effective holdings</span><span class="v">${fix(c.effective, 1)}</span><span class="s">of ${c.count} · HHI ${fix(c.hhi)}</span></div>
    <div><span class="k">Top 1 · top 3</span><span class="v">${share(c.top1, 0)} · ${share(c.top3, 0)}</span><span class="s">of the value</span></div>
    <div><span class="k">Average correlation</span><span class="v">${fix(r?.average_correlation)}</span><span class="s">between pairs; near 1: one bet</span></div>
    <div><span class="k">Diversification ratio</span><span class="v">${fix(r?.diversification_ratio)}</span><span class="s">above 1: it lowers your risk</span></div></div>`;
  const risk = Object.fromEntries((r?.holdings || []).map((x) => [x.symbol, x]));
  const rows = h.betas.map((b) => ({ ...b, volatility: risk[b.symbol]?.volatility, risk_share: risk[b.symbol]?.risk_share }));
  const draw = () => dataTable($("#p-holdings"), [["symbol", "Symbol", "sym"], ["weight", "Weight", "share"], ["risk_share", "Share of risk", "share"],
    ["volatility", "Volatility", "share"], ["beta", "Beta", "ratio"], ["dimson_beta", "Thin-trading beta", "ratio"], ["cse_beta", "CSE beta", "ratio"], ["r2", "R²", "ratio"]],
  rows, view.sort.risk, (s) => { view.sort.risk = s; draw(); });
  draw();
}

function attributionPanel() {
  const a = view.p.attribution;
  if (!a || !a.stocks) {
    $("#p-attr").innerHTML = empty(a && a.unpriced.length ? `Needs a price for ${a.unpriced.join(", ")} at both ends` : "Needs price history");
    return;
  }
  const rows = a.stocks, total = rows.reduce((s, r) => s + r.contribution, 0), top = Math.max(...rows.map((r) => Math.abs(r.contribution))) || 1;
  $("#p-attr-note").textContent = `${day(ms(a.start))} → ${day(ms(a.end))} · total ${pct(total, 1)}`;
  $("#p-attr").innerHTML = `<table class="index">${rows.map((r) => `<tr><td class="sym"><a class="sym-link" href="#/t/${encodeURIComponent(r.symbol)}">${esc(r.symbol)}</a></td>
    <td class="bar-cell"><div class="${r.contribution < 0 ? "neg" : ""}" style="width:${(Math.abs(r.contribution) / top * 100).toFixed(1)}%"></div></td>
    <td class="num">${signed(r.gain)}</td><td class="num ${r.contribution < 0 ? "alert" : ""}">${pct(r.contribution, 2)}</td></tr>`).join("")}</table>
    <p class="note">Each stock's gain over the period (dividends included) divided by the portfolio's Modified Dietz base, so the column adds up to the portfolio's return.</p>`;
  const s = a.sectors;
  if (!s) {
    $("#p-sector-note").textContent = view.p.files["sectors.csv"]
      ? "By sector: needs sector index levels at both ends (smpa prices stores them daily)."
      : "Add data/sectors.csv (symbol, sector: an S&P/CSE industry group such as Banks) to split this into allocation and selection.";
    return;
  }
  dataTable($("#p-sectors"), [["sector", "Sector", "text"], ["weight", "Your weight", "share"], ["index_weight", "Index weight", "share"],
    ["return", "Your return", "pct1", true], ["index_return", "Sector index", "pct1", true], ["allocation", "Allocation", "pct2", true], ["selection", "Selection", "pct2", true]],
  s, view.sort.sec, (x) => { view.sort.sec = x; attributionPanel(); });
  $("#p-sector-note").textContent = `Brinson–Fachler against the ASPI (${pct(a.market_return, 1)}). Allocation: did you overweight sectors that beat it? Selection: did you pick well within each? ` +
    `Index weights come from the market caps of the stocks in sectors.csv: ${share(a.index_coverage, 0)} of the market's value.`;
}
