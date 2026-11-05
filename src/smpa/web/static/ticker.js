// The per-stock page (#/t/SYMBOL): how one stock has done for you, and why.

import {
  $, DAY, amount, box, dataTable, day, empty, esc, frame, hideTip, isNum, linear, money, moveTip, ms, niceTicks,
  pathOf, pct, plural, share, showTip, signed, svgOpen, svgPoint, timeTicks, xAxis, yGrid,
} from "./lib.js";

const view = { t: null, tab: "lots", sort: {}, load: null };
const cur = () => view.t.currency;
const rate = (r, period, days) => (!isNum(r) ? "—" : days < 365 ? `${pct(period, 1)} in ${plural(days, "day")}` : `${pct(r, 1)} a year`);

export function renderTicker(el, t, load) {
  view.t = t;
  view.load = load;
  const p = t.position, l = t.lifetime;
  el.innerHTML = `
    <nav class="crumbs"><a href="#/">← Overview</a><span>/</span><span>${esc(t.symbol)}</span></nav>
    <section class="ticker-title"><h2>${esc(t.symbol)}</h2>
      <div class="muted">${[t.company, t.isin, `as of ${day(ms(t.as_of))}`].filter(Boolean).map(esc).join(" · ")}</div></section>
    <section class="readouts" id="t-readouts"></section>
    ${t.warnings.length ? `<div class="flags open"><div class="list">${t.warnings.map((w) => `<div class="flag"><span class="id">CHECK</span><span>${esc(w)}</span></div>`).join("")}</div></div>` : ""}
    <section class="panel" id="t-ifsold"></section>
    <section class="panel"><div class="sec"><span class="n">A</span><span class="t">Price and your trades</span><span class="r">${esc(cur())} a share · closes as traded</span></div>
      <div class="legend wrap"><span class="item"><span class="key line"></span>Close</span><span class="item"><span class="key line grey"></span>Average cost</span>
        <span class="item"><span class="key line dash"></span>Break-even after selling fees</span><span class="item"><span class="mk up"></span>Buy</span><span class="item"><span class="mk down"></span>Sale</span></div>
      <div class="plot" id="t-price"></div></section>
    <section class="panel"><div class="sec"><span class="n">B</span><span class="t">Gain if sold on each day</span><span class="r" id="t-curve-note"></span></div>
      <div class="legend wrap"><span class="item"><span class="key line"></span>Total gain if all sold at that close, after fees</span>
        <span class="item"><span class="mk best"></span>Best and worst day, in hindsight</span><span class="item"><span class="mk down"></span>Your sales</span></div>
      <div class="plot" id="t-curve"></div></section>
    <section class="panel"><div class="sec"><span class="n">C</span><span class="t">Shares and money</span><span class="r">${esc(cur())}</span></div>
      <div class="legend wrap"><span class="item"><span class="key line"></span>Market value</span><span class="item"><span class="key line grey"></span>Cost basis</span>
        <span class="item"><span class="key ink"></span>Shares held</span></div>
      <div class="plot" id="t-money"></div></section>
    <section class="records" aria-label="Records"><nav class="tabs" id="t-tabs" role="tablist"></nav><div class="table-wrap" id="t-table"></div>
      <p class="note" id="t-table-note"></p></section>`;
  readouts(p, l);
  ifSold();
  draw();
  tabs();
}

export function redrawTicker() {
  if (view.t) draw();
}

function draw() {
  priceChart($("#t-price"));
  curveChart($("#t-curve"));
  moneyChart($("#t-money"));
}

function readouts(p, l) {
  const t = view.t, c = cur(), cell = (label, v, sub, alert) => box(`${label} · ${c}`, amount(v), sub, alert);
  const r = t.risk, liq = t.liquidity, rg = t.range, y = t.yields, vs = t.vs_index, approx = l.complete ? "" : "≈";
  const short = l.days < 365;
  const held = p.quantity
    ? box("Held · shares", esc(money(p.quantity, 0)), `Avg cost ${money(p.avg_cost)} · break-even ${money(p.break_even)}`, false)
    : box("Held · shares", "0", "Position closed", false);
  $("#t-readouts").innerHTML = [
    held,
    box(`Price · ${c}`, amount(p.price), rg ? `52-week ${money(rg.low)}–${money(rg.high)}${isNum(rg.from_high) ? ` · ${pct(rg.from_high)} from high` : ""}` : "No 52-week range yet", isNum(rg?.from_high) && rg.from_high < -0.2),
    cell("Market value", p.quantity ? p.value : 0, p.priced ? `If sold ${money(p.liquidation_value, 0)} after fees` : p.quantity ? "No price: counted at cost" : "—", p.quantity && !p.priced),
    box("Weight", esc(share(p.weight)), "Of the portfolio's value", false),
    box(`Lifetime P&L · ${c}`, `${approx}${esc(signed(l.total))}`, `Realized ${signed(l.realized)} · unrealized ${signed(l.unrealized)} · dividends ${money(l.dividends, 0)}`, l.total < 0),
    box(short ? "Return · under a year" : "Annual return · XIRR", `${approx}${esc(pct(short ? l.period_return : l.xirr, 2))}`,
      `${pct(short ? l.period_return_if_sold : l.xirr_if_sold, 2)} after selling fees`, (short ? l.period_return : l.xirr) < 0),
    box("vs the ASPI · direct alpha", vs && isNum(vs.direct_alpha) ? `${esc(pct(vs.direct_alpha, 1))}<span class="dec"> a yr</span>` : "—",
      vs ? `KS-PME ${isNum(vs.ks_pme) ? vs.ks_pme.toFixed(2) : "—"} · since ${day(ms(vs.since))}` : "Needs ASPI history", vs && vs.direct_alpha < 0),
    cell("Fees paid", l.fees, isNum(l.fees_share) ? `${share(l.fees_share)} of the gain` : "On this stock's trades", l.fees_share > 0.25),
    box("Shares held for", isNum(t.holding.average_age) ? `${esc(money(t.holding.average_age, 0))}<span class="dec"> days</span>` : "—",
      l.first_buy ? `Average age (FIFO) · first bought ${day(ms(l.first_buy))}` : "—", false),
    box("Yield on cost", esc(share(y.on_cost, 2)), `Trailing ${share(y.trailing, 2)} · ${money(y.income_12m, 0)} in 12 months`, false),
    box("Volatility · a year", r ? esc(share(r.volatility)) : "—",
      r ? `Max drawdown ${pct(r.drawdown?.depth)} · beta ${fix(r.beta)} (thin-trading ${fix(r.dimson_beta)}) · CSE ${fix(t.betas.beta_tri_aspi)}` : "Needs price history", false),
    box("Days to sell", isNum(liq.days_to_exit) ? esc(liq.days_to_exit < 10 ? liq.days_to_exit.toFixed(1) : money(liq.days_to_exit, 0)) : "—",
      `At 15% of daily turnover${isNum(liq.no_trade_days) ? ` · ${liq.no_trade_days} of ${liq.trading_days} days untraded` : ""}`, liq.days_to_exit > 1),
  ].join("");
}
const fix = (v) => (isNum(v) ? v.toFixed(2) : "—");

// "If sold on [date]": the whole position sold at that day's close, against what actually happened.
function ifSold() {
  const t = view.t, el = $("#t-ifsold"), s = t.if_sold, c = cur();
  const min = t.lifetime.first_buy || t.as_of;
  let body = `<p class="note">Pick a day to see what selling all of ${esc(t.symbol)} at that day's close would have made, after that day's fees, against what actually happened.</p>`;
  if (s && !s.held) body = `<p class="note">Nothing was held at the close on ${day(ms(s.day))}.</p>`;
  else if (s && !isNum(s.price)) body = `<p class="note">No close within 31 days before ${day(ms(s.day))}, so no price (none is guessed).</p>`;
  else if (s) {
    const h = s.cohort, row = (k, a, b, cls = "") => `<tr><th>${esc(k)}</th><td class="num ${cls}">${a}</td><td class="num">${b}</td></tr>`;
    const better = s.better_by;
    body = `<div class="scroll-x"><table class="compare"><thead><tr><th></th><th class="num">If sold on ${day(ms(s.day))}</th><th class="num">What happened · if sold now</th></tr></thead><tbody>
      ${row("Shares", `${money(s.quantity, 0)} @ ${money(s.price)}`, `${money(t.position.quantity, 0)} held`)}
      ${row("Proceeds after fees", money(s.proceeds), isNum(t.position.liquidation_value) ? money(t.position.liquidation_value) : "—")}
      ${row("Gain on the shares", `${signed(s.gain, 2)} (${pct(s.gain_pct)})`, signed(t.lifetime.total_if_sold - t.lifetime.dividends, 2), s.gain < 0 ? "alert" : "")}
      ${row("Dividends", money(s.dividends), money(t.lifetime.dividends))}
      ${row("Lifetime gain", signed(s.lifetime, 2), signed(s.actual, 2), s.lifetime < 0 ? "alert" : "")}
      ${row("Money-weighted return", rate(s.xirr, s.xirr, 9999), rate(s.actual_xirr, s.actual_xirr, 9999))}
      </tbody></table></div>
      <p class="verdict ${better > 0 ? "alert" : ""}">${isNum(better) ? `Selling everything on ${day(ms(s.day))} would have left you ${c} ${money(Math.abs(better))} ${better > 0 ? "better" : "worse"} off.` : ""}</p>
      ${h && isNum(h.difference) ? `<p class="note">The ${money(h.shares, 0)} shares held that day: since then they brought in ${c} ${money(h.sales + h.dividends)} (sales and dividends) and the ${money(h.shares_left, 0)} left are worth ${c} ${money(h.value_now)} after fees, ${c} ${money(h.holding)} in all, against ${c} ${money(h.selling)} for selling them then: holding them ${h.difference >= 0 ? "made" : "cost"} you ${c} ${money(Math.abs(h.difference))}.${isNum(h.difference_fair) ? ` Counting what the money could have earned in the ASPI meanwhile, holding ${h.difference_fair >= 0 ? "was better" : "was worse"} by ${c} ${money(Math.abs(h.difference_fair))}.` : ""}</p>` : ""}`;
  }
  el.innerHTML = `<div class="sec"><span class="n">↺</span><span class="t">If sold on</span><span class="r">Fee rate ${s ? share(s.fee_rate, 3) : share(t.fee_rate, 3)}</span></div>
    <div class="controls inline"><label class="field"><span>Day</span><input type="date" id="t-day" min="${min}" max="${t.as_of}" value="${s ? s.day : ""}"></label>
    ${t.excursion ? `<span class="hint">Best day in hindsight: <button class="link" type="button" data-day="${t.excursion.best.date}">${day(ms(t.excursion.best.date))}</button> · worst: <button class="link" type="button" data-day="${t.excursion.worst.date}">${day(ms(t.excursion.worst.date))}</button></span>` : ""}</div>${body}`;
  $("#t-day").addEventListener("change", (e) => { if (e.target.value) view.load(t.symbol, e.target.value); });
  el.querySelectorAll("[data-day]").forEach((b) => b.addEventListener("click", () => view.load(t.symbol, b.dataset.day)));
}

// --- Charts ----------------------------------------------------------------------------------------
function span(points) {
  const t = view.t, t1 = ms(t.as_of);
  let t0 = points.length ? points[0].t : t1 - 365 * DAY;
  if (t1 - t0 < 120 * DAY) t0 = t1 - 120 * DAY;
  return [t0, t1];
}
const ticksFor = (f, t0, t1) => timeTicks(t0, t1, Math.max(3, Math.floor((f.x1 - f.x0) / 90)));
// A buy points up at its price from below the line, a sale down at it from above.
const tri = (x, y, up, cls) => `<path class="mk-svg ${cls}" d="M${x - 5},${up ? y + 11 : y - 11}L${x + 5},${up ? y + 11 : y - 11}L${x},${up ? y + 2 : y - 2}Z"/>`;

// [A] Closes as traded, your buys and sales, the average cost (a step: it changes on buys and corporate
// actions) and the price at which selling would get back the cost.
function priceChart(el) {
  const t = view.t, pts = t.prices.map((p) => ({ t: ms(p.date), v: p.price }));
  if (!pts.length && !t.trades.length) { el.innerHTML = empty("No price history yet: run smpa prices"); return; }
  const [t0, t1] = span(pts.length ? pts : t.trades.map((x) => ({ t: ms(x.date) })));
  const steps = t.steps.map((s) => ({ t: ms(s.date), a: s.avg_cost, b: s.break_even }));
  const stepPath = (key) => {
    let d = "";
    steps.forEach((s, i) => {
      const next = i + 1 < steps.length ? steps[i + 1].t : t1;
      if (!isNum(s[key]) || next < t0) return;
      const a = Math.max(s.t, t0);
      d += `M${x(a)},${y(s[key])}H${x(next)}`;
    });
    return d;
  };
  const shown = pts.filter((p) => p.t >= t0);
  const vals = [...shown.map((p) => p.v), ...steps.filter((s) => s.t <= t1).flatMap((s) => [s.a, s.b]).filter(isNum), ...t.trades.map((x) => x.price)];
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const f = frame(el, 300), ticks = niceTicks(lo - (hi - lo) * 0.05, hi + (hi - lo) * 0.05);
  const x = linear([t0, t1], [f.x0, f.x1]), y = linear([ticks[0], ticks.at(-1)], [f.y0, f.y1]);
  const marks = t.trades.filter((m) => ms(m.date) >= t0).map((m, i) =>
    `<g class="trade" data-i="${i}">${tri(x(ms(m.date)), y(m.price), m.side === "BUY", m.side === "BUY" ? "up" : "down")}</g>`).join("");
  el.innerHTML = svgOpen(f, `${t.symbol} price with your trades`) + yGrid(f, y, ticks) + xAxis(f, x, ticksFor(f, t0, t1)) +
    `<path class="series dash" d="${stepPath("b")}"/><path class="series grey" d="${stepPath("a")}"/>` +
    `<path class="series" d="${pathOf(shown, "v", x, y)}"/>` + hover(f) + marks + "</svg>";
  track(el, f, t0, t1, shown, (p) => y(p.v), (p) => {
    const s = [...steps].reverse().find((q) => q.t <= p.t);
    const done = t.trades.filter((m) => ms(m.date) === p.t);
    return [`${day(p.t)}`, [["Close", money(p.v)], ...(s && isNum(s.a) ? [["Average cost", money(s.a)], ["Break-even", money(s.b)]] : []),
      ...done.map((m) => [m.side === "BUY" ? "Bought" : "Sold", `${money(m.quantity, 0)} @ ${money(m.price)}`])]];
  });
}

// [B] The total gain had everything been sold at each close: realized and dividends so far plus the
// unrealized part after fees. The best and worst points are hindsight, not a strategy.
function curveChart(el) {
  const t = view.t, pts = t.curve.map((p) => ({ t: ms(p.date), v: p.gain, l: p.realized + p.dividends }));
  const ex = t.excursion;
  $("#t-curve-note").textContent = ex ? `Best ${signed(ex.best.gain)} on ${day(ms(ex.best.date))} · worst ${signed(ex.worst.gain)} on ${day(ms(ex.worst.date))}` : cur();
  if (!pts.length) { el.innerHTML = empty("Needs price history while you held it: run smpa prices"); return; }
  const [t0, t1] = span(pts);
  const vals = pts.map((p) => p.v);
  const f = frame(el, 260), ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
  const x = linear([t0, t1], [f.x0, f.x1]), y = linear([ticks[0], ticks.at(-1)], [f.y0, f.y1]);
  const at = (d) => [...pts].reverse().find((p) => p.t <= ms(d));
  const sales = t.sales.map((s) => at(s.date)).filter(Boolean).map((p) => tri(x(p.t), y(p.v), false, "down")).join("");
  const dia = (d, cls) => { const p = at(d); return p ? `<path class="mk-svg ${cls}" d="M${x(p.t)},${y(p.v) - 5}l5,5l-5,5l-5,-5Z"/>` : ""; };
  const picked = t.if_sold && t.if_sold.held ? `<line class="cross picked" x1="${x(ms(t.if_sold.day))}" x2="${x(ms(t.if_sold.day))}" y1="${f.y1}" y2="${f.y0}"/>` : "";
  el.innerHTML = svgOpen(f, `${t.symbol}: total gain if sold on each day`) + yGrid(f, y, ticks) + xAxis(f, x, ticksFor(f, t0, t1)) +
    `<line class="zero" x1="${f.x0}" x2="${f.x1}" y1="${y(0)}" y2="${y(0)}"/>${picked}<path class="series" d="${pathOf(pts, "v", x, y)}"/>` +
    hover(f) + sales + (ex ? dia(ex.best.date, "best") + dia(ex.worst.date, ex.worst.gain < 0 ? "worst" : "best") : "") + "</svg>";
  track(el, f, t0, t1, pts, (p) => y(p.v), (p) => [`If sold on ${day(p.t)}`, [["Total gain", `${cur()} ${money(p.v)}`, p.v < 0 ? "alert" : ""],
    ["Realized + dividends", `${cur()} ${money(p.l)}`]]], (p) => view.load(t.symbol, new Date(p.t).toISOString().slice(0, 10)));
}

// [C] Market value against cost basis (the gap is the unrealized P&L), and the shares held below.
function moneyChart(el) {
  const t = view.t, pts = t.curve.map((p) => ({ t: ms(p.date), v: p.value, c: p.cost }));
  const steps = t.steps.map((s) => ({ t: ms(s.date), q: s.quantity, c: s.cost }));
  if (!steps.length) { el.innerHTML = empty("No trades"); return; }
  const [t0, t1] = span(pts.length ? pts : steps);
  const W = frame(el, 300), f = { ...W, y0: 210 }, g = { ...W, y1: 236, y0: 274 };
  const vals = [...pts.map((p) => p.v), ...steps.map((s) => s.c)];
  const ticks = niceTicks(0, Math.max(1, ...vals)), qmax = Math.max(1, ...steps.map((s) => s.q));
  const x = linear([t0, t1], [W.x0, W.x1]), y = linear([ticks[0], ticks.at(-1)], [f.y0, f.y1]), yq = linear([0, qmax], [g.y0, g.y1]);
  let cost = "", qty = `M${x(Math.max(steps[0].t, t0))},${yq(0)}`;
  steps.forEach((s, i) => {
    const a = Math.max(s.t, t0), b = i + 1 < steps.length ? steps[i + 1].t : t1;
    if (b < t0) return;
    cost += `M${x(a)},${y(s.c)}H${x(b)}`;
    qty += `L${x(a)},${yq(s.q)}L${x(b)},${yq(s.q)}`;
  });
  qty += `L${x(t1)},${yq(0)}Z`;
  el.innerHTML = `<svg viewBox="0 0 ${W.w} ${W.h}" height="${W.h}" role="img" aria-label="${esc(t.symbol)} market value, cost basis and shares held">` +
    yGrid(f, y, ticks) + xAxis(W, x, ticksFor(W, t0, t1)) +
    `<path class="area" d="${qty}"/><text x="${W.x0 - 8}" y="${g.y1 + 8}" text-anchor="end">${money(qmax, 0)}</text>` +
    `<path class="series grey" d="${cost}"/><path class="series" d="${pathOf(pts.filter((p) => p.t >= t0), "v", x, y)}"/>` + hover(f) + "</svg>";
  track(el, f, t0, t1, pts.filter((p) => p.t >= t0), (p) => y(p.v), (p) => {
    const s = [...steps].reverse().find((q) => q.t <= p.t);
    return [day(p.t), [["Market value", `${cur()} ${money(p.v)}`], ["Cost basis", `${cur()} ${money(p.c)}`],
      ["Unrealized", `${cur()} ${money(p.v - p.c)}`, p.v < p.c ? "alert" : ""], ["Shares", money(s ? s.q : 0, 0)]]];
  });
}

const hover = (f) => `<g class="hover" visibility="hidden"><line class="cross vx" y1="${f.y1}" y2="${f.y0}"/><rect class="reticle" width="7" height="7"/></g>` +
  `<rect class="hitbox" x="${f.x0}" y="${f.y1}" width="${f.x1 - f.x0}" height="${f.y0 - f.y1}"/>`;

// Crosshair and tooltip on the nearest point at or before the pointer; `tipFor(p)` gives [title, rows].
function track(el, f, t0, t1, pts, yOf, tipFor, onClick) {
  const svg = el.querySelector("svg"), g = svg.querySelector(".hover"), hit = svg.querySelector(".hitbox");
  const near = (ev) => {
    const px = svgPoint(svg, f, ev).x, tt = t0 + ((px - f.x0) / (f.x1 - f.x0)) * (t1 - t0);
    let p = null;
    for (const q of pts) { if (q.t <= tt) p = q; else break; }
    return p && tt - p.t <= 10 * DAY ? p : null;
  };
  const x = linear([t0, t1], [f.x0, f.x1]);
  hit.addEventListener("mousemove", (ev) => {
    const p = near(ev);
    if (!p) { g.setAttribute("visibility", "hidden"); hideTip(); return; }
    g.setAttribute("visibility", "visible");
    g.querySelector(".vx").setAttribute("x1", x(p.t)); g.querySelector(".vx").setAttribute("x2", x(p.t));
    g.querySelector(".reticle").setAttribute("x", x(p.t) - 3.5); g.querySelector(".reticle").setAttribute("y", yOf(p) - 3.5);
    const [title, rows] = tipFor(p);
    showTip(title, rows, ev);
  });
  hit.addEventListener("mouseleave", () => { g.setAttribute("visibility", "hidden"); hideTip(); });
  if (onClick) { hit.style.cursor = "pointer"; hit.addEventListener("click", (ev) => { const p = near(ev); if (p) onClick(p); }); }
}

// --- Records -------------------------------------------------------------------------------------
const TABLES = {
  lots: { label: "Lots", rows: () => view.t.lots, note: "FIFO lots, for how old your shares are. Their cost is not the average-cost basis the rest of the page uses, so don't add them up.",
    cols: [["bought", "Bought", "date"], ["quantity", "Shares", "int"], ["cost_per_share", "Cost / share", "money"], ["cost", "Cost", "money"], ["age_days", "Age · days", "days"]] },
  fills: { label: "Trades and fees", rows: () => view.t.fills, note: "Each fee as the Contract Note or Trade Confirmation shows it; statement-only trades have the total only.",
    cols: [["trade_date", "Date", "date"], ["side", "Side", "text"], ["quantity", "Qty", "int"], ["price", "Price", "money"], ["gross_amount", "Gross", "money"],
      ["brokerage", "Brokerage", "cash"], ["stl", "STL", "cash"], ["cse", "CSE", "cash"], ["sec", "SEC", "cash"], ["cds", "CDS", "cash"], ["clearing", "Clearing", "cash"],
      ["fees", "All fees", "money"], ["net_amount", "Net", "money"], ["contract_no", "Contract", "text"]] },
  actions: { label: "Dividends and actions", rows: () => view.t.actions, note: "From data/corporate_actions.csv.",
    cols: [["date", "Date", "date"], ["action", "Action", "text"], ["detail", "Detail", "text"], ["held", "Held", "qty"], ["shares", "Shares", "qty"], ["amount", "Cash", "cash", true], ["note", "Note", "text"]] },
  sales: { label: "After each sale", rows: () => view.t.sales, note: "The close 1, 3 and 12 months after each sale against the price you sold at, and against the ASPI over the same months. Left on the table is negative for a loss avoided. Efficiency: how much of the best move while held a sale at a gain captured, in hindsight.",
    cols: [["date", "Sold", "date"], ["quantity", "Qty", "int"], ["price", "Price", "money"], ["avg_cost", "Avg cost", "money"], ["gain_pct", "Gain", "pct1", true],
      ["efficiency", "Efficiency", "share"], ["after_1", "+1M", "pct1"], ["after_3", "+3M", "pct1"], ["after_12", "+12M", "pct1"],
      ["vs_12", "+12M vs ASPI", "pct1"], ["left_12", "Left on table · 12M", "money"]] },
};

// Reasons from data/journal.csv, and how a journaled buy's own shares did (FIFO, after fees, dividends included).
const JOURNAL = [["reason", "Reason", "text"], ["target", "Target", "money"], ["stop", "Stop", "money"],
  ["outcome", "Made", "money", true], ["return", "Return", "pct1", true]];

function tabs() {
  $("#t-tabs").innerHTML = Object.entries(TABLES).map(([k, x]) =>
    `<button type="button" role="tab" data-tab="${k}" aria-selected="${view.tab === k}">${x.label}<span class="count">${x.rows().length}</span></button>`).join("");
  $("#t-tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { view.tab = b.dataset.tab; tabs(); }));
  const x = TABLES[view.tab];
  const why = view.tab === "fills" && x.rows().some((r) => r.reason) ? JOURNAL : [];  // only with data/journal.csv
  dataTable($("#t-table"), [...x.cols, ...why], x.rows(), view.sort[view.tab], (s) => { view.sort[view.tab] = s; tabs(); });
  $("#t-table-note").textContent = x.note;
}

