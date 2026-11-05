// The Habits page (#/habits): your decisions rather than the portfolio (docs/05). Every figure comes with how many
// it rests on, and a share with its 95% range: tens of trades can't tell skill from luck on their own.

import {
  $, box, dataTable, day, empty, esc, frame, hideTip, isNum, linear, money, moveTip, ms, niceTicks, pct, plural, share,
  showTip, signed, svgOpen, svgPoint, timeTicks, xAxis, yGrid,
} from "./lib.js";

const view = { h: null, sort: {} };
const cur = () => view.h.currency;
const fix = (v, dp = 2) => (isNum(v) ? v.toFixed(dp) : "—");
const range = (s) => (s && s.range ? `${share(s.range[0], 0)}\u2060–\u2060${share(s.range[1], 0)}` : "—");  // no line break inside
const rupees = (v) => (isNum(v) ? `${cur()} ${signed(v)}` : "—");
const days = (v) => (isNum(v) ? plural(Math.round(v), "day") : "—");
const count = (v) => (isNum(v) ? money(v, 0) : "—");
const months = (k) => plural(k, "month");
const switchCount = (n) => `${n} switch${n === 1 ? "" : "es"}`;
const KIND = { all: "All buys", new: "New positions", winner: "Adding to a winner", down: "Averaging down" };
const ONE = { new: "New position", winner: "Adding to a winner", down: "Averaging down" };  // the kind of one buy

export function renderHabits(el, h) {
  view.h = h;
  if (h.empty) { el.innerHTML = empty("No trades yet"); return; }
  el.innerHTML = `
    <section class="ticker-title"><h2>Habits</h2><div class="muted">Your decisions, not the portfolio · each figure with how many it rests on</div></section>
    <section class="readouts" id="h-readouts"></section>
    <div class="row r11">
      <section class="panel"><div class="sec"><span class="n">H1</span><span class="t">Selling winners too early?</span><span class="r" id="h-disp-note"></span></div>
        <div id="h-disp"></div></section>
      <section class="panel"><div class="sec"><span class="n">H2</span><span class="t">Round trips</span><span class="r" id="h-trips-note"></span></div>
        <div class="legend"><span class="key dot"></span>Made money<span class="key dot loss"></span>Lost money</div>
        <div class="plot" id="h-trips"></div><div id="h-trip-stats"></div></section>
    </div>
    <section class="panel"><div class="sec"><span class="n">H3</span><span class="t">What fees cost you</span><span class="r" id="h-fees-note"></span></div>
      <div class="legend" id="h-fee-keys"></div><div class="plot" id="h-fees"></div>
      <div class="table-wrap scroll-x" id="h-years"></div>
      <p class="note">Fee drag: the year's fees over the average value of the portfolio that year. Turnover: the smaller of the
        year's buys and sells over that average, as funds count it; at 100% you replaced everything once, which costs about
        2.26% in fees. The average and the return need prices for every holding, so years before your price history have none.</p>
      <div class="table-wrap" id="h-stocks"></div></section>
    <div class="row r11">
      <section class="panel"><div class="sec"><span class="n">H4</span><span class="t">Did trading beat holding?</span><span class="r">1 January's shares, left alone</span></div>
        <div class="table-wrap" id="h-frozen"></div>
        <p class="note">Frozen: the shares you held on 1 January with no trade all year (prices and their dividends). Yours: the
          time-weighted return you actually got, so new money doesn't count. The difference is what that year's trades added or
          cost. Only dividends in corporate_actions.csv count, usually only those you received, which flatters trading a little.</p></section>
      <section class="panel"><div class="sec"><span class="n">H5</span><span class="t">Switches</span><span class="r" id="h-sw-note"></span></div>
        <div id="h-switch"></div><div class="table-wrap" id="h-switches"></div><p class="verdict" id="h-never"></p></section>
    </div>
    <section class="panel"><div class="sec"><span class="n">H6</span><span class="t">Timing your buys</span><span class="r" id="h-buy-note"></span></div>
      <div class="table-wrap" id="h-buy-kinds"></div><div class="table-wrap" id="h-buys"></div>
      <p class="note" id="h-timing"></p></section>
    <section class="panel"><div class="sec"><span class="n">H7</span><span class="t">Your reasons</span><span class="r">data/journal.csv</span></div>
      <div id="h-journal"></div></section>
    <section class="panel"><div class="sec"><span class="n">H8</span><span class="t">Cash left idle</span><span class="r" id="h-cash-note"></span></div>
      <div id="h-cash"></div><div class="plot" id="h-cash-chart"></div><p class="note" id="h-cash-foot"></p></section>`;
  readouts();
  dispositionPanel();
  tripStats();
  feeTables();
  frozenTable();
  switchPanel();
  buyPanel();
  journalPanel();
  cashPanel();
  drawHabits();
}

export function drawHabits() {
  if (!view.h || view.h.empty) return;
  tripsChart();
  feesChart();
  cashChart();
}

// --- Headline numbers --------------------------------------------------------------------------------------------
function readouts() {
  const h = view.h, d = h.disposition, t = h.trips.stats, never = h.never_sold;
  const year = [...h.fees.years].reverse().find((y) => isNum(y.fee_drag));
  const alpha = [...h.frozen].reverse().find((y) => isNum(y.alpha));
  const sw = [...h.switches.summary].reverse().find((s) => s.n);
  $("#h-readouts").innerHTML = [
    box("Winners sold · PGR", esc(share(d.pgr.share, 0)), d.pgr.n ? `Losers sold (PLR) ${share(d.plr.share, 0)} · ${plural(d.days, "day")} of selling` : "Needs a sale", false),
    box("Round trips · won", esc(share(t.win_rate.share, 0)), t.n ? `${t.win_rate.k} of ${t.n} · range ${range(t.win_rate)}` : "Needs a sale", false),
    box("Profit factor", esc(fix(t.profit_factor)), t.n ? `Wins ÷ losses · payoff ${fix(t.payoff)}` : "—", isNum(t.profit_factor) && t.profit_factor < 1),
    box("Held · winners vs losers", isNum(t.median_days_win) || isNum(t.median_days_loss) ? `${esc(count(t.median_days_win))} · ${esc(count(t.median_days_loss))}` : "—",
      "Median days, won · lost", isNum(t.median_days_loss) && t.median_days_loss > (t.median_days_win ?? Infinity)),
    box(year ? `Fee drag · ${year.year}` : "Fee drag", esc(share(year?.fee_drag, 2)), year ? `${cur()} ${money(year.fees, 0)} in fees · turnover ${share(year.turnover, 0)}` : "Needs price history", false),
    box(alpha ? `Trading alpha · ${alpha.year}` : "Trading alpha", esc(pct(alpha?.alpha, 1)), alpha ? `Yours ${pct(alpha.twr, 1)} · left alone ${pct(alpha.frozen, 1)}` : "Needs a year of prices", alpha?.alpha < 0),
    box("Switches · " + (sw ? `+${months(sw.months)}` : "average"), esc(pct(sw?.average, 1)), sw ? `After fees · ${switchCount(sw.n)} · ${sw.ahead.k} ahead` : "None with prices yet", sw?.average < 0),
    box("If you'd never sold", never.priced ? esc(signed(never.difference)) : "—",
      never.priced ? `${cur()} · ${plural(never.priced, "sale")} · in the ASPI ${isNum(never.in_aspi) ? signed(never.in_aspi - never.proceeds) : "—"}` : "Needs a sale with a price today", false),
  ].join("");
}

// --- H1 The disposition effect ---------------------------------------------------------------------------------------
function dispositionPanel() {
  const d = view.h.disposition, c = d.counts, t = view.h.trips.stats;
  $("#h-disp-note").textContent = d.days ? `${plural(d.days, "day")} you sold` : "";
  if (!d.days) { $("#h-disp").innerHTML = empty("Needs a sale"); return; }
  const both = d.pgr.n && d.plr.n;
  const overlap = both && d.pgr.range && d.plr.range && d.pgr.range[0] <= d.plr.range[1] && d.plr.range[0] <= d.pgr.range[1];
  const verdict = !both ? "Needs a day when you sold while holding both a winner and a loser." :
    `On the days you sold, you cashed in ${share(d.pgr.share, 0)} of the stocks you held at a gain, and ${share(d.plr.share, 0)} of those at a loss. ` +
    (d.difference > 0 ? "Quicker to sell winners than to cut losers: the disposition effect. " : "No sign of selling winners too soon. ") +
    (overlap ? "The two ranges overlap, so with this few it could be chance." : "The ranges don't overlap.");
  $("#h-disp").innerHTML = `<div class="stats">
      <div><span class="k">Gains realized · PGR</span><span class="v">${share(d.pgr.share, 0)}</span><span class="s">${d.pgr.k} of ${d.pgr.n} · ${range(d.pgr)}</span></div>
      <div><span class="k">Losses realized · PLR</span><span class="v">${share(d.plr.share, 0)}</span><span class="s">${d.plr.k} of ${d.plr.n} · ${range(d.plr)}</span></div>
      <div><span class="k">Winners held</span><span class="v">${count(t.median_days_win)}</span><span class="s">median days · ${plural(t.n_win, "trip")}</span></div>
      <div><span class="k">Losers held</span><span class="v">${count(t.median_days_loss)}</span><span class="s">median days · ${plural(t.n_loss, "trip")}</span></div></div>
    <p class="verdict">${esc(verdict)}</p>
    <div class="scroll-x"><table class="compare"><thead><tr><th>Against your cost</th><th class="num">Sold</th><th class="num">Kept</th></tr></thead><tbody>
      <tr><th>Up</th><td class="num">${c.realized_gains}</td><td class="num">${c.paper_gains}</td></tr>
      <tr><th>Down</th><td class="num">${c.realized_losses}</td><td class="num">${c.paper_losses}</td></tr></tbody></table></div>
    <p class="note">Each day you sold, every stock you held is counted once: up or down against your average cost, sold or kept (Odean 1998;
      his investors: PGR 0.15, PLR 0.10). A stock kept without a close in the week before is left out. Whether the winners you sold
      kept rising is on the overview's Sales tab.</p>`;
}

// --- H2 Round trips ------------------------------------------------------------------------------------------------------
function tripStats() {
  const t = view.h.trips.stats, c = cur();
  $("#h-trips-note").textContent = t.n ? `${plural(t.n, "trip")} · FIFO` : "";
  if (!t.n) { $("#h-trip-stats").innerHTML = ""; return; }
  $("#h-trip-stats").innerHTML = `<div class="stats">
      <div><span class="k">Won</span><span class="v">${share(t.win_rate.share, 0)}</span><span class="s">${t.win_rate.k} of ${t.n} · ${range(t.win_rate)}</span></div>
      <div><span class="k">Average win · loss</span><span class="v">${pct(t.average_win_return, 0)} · ${pct(t.average_loss_return, 0)}</span><span class="s">${c} ${signed(t.average_win)} · ${signed(t.average_loss)}</span></div>
      <div><span class="k">Payoff · profit factor</span><span class="v">${fix(t.payoff)} · ${fix(t.profit_factor)}</span><span class="s">above 1: wins outweigh losses</span></div>
      <div><span class="k">Expectancy</span><span class="v">${signed(t.expectancy)}</span><span class="s">${c} per trip · ${signed(t.total)} in all</span></div></div>
    <p class="note">Each sale matched to the buys it came from, oldest first, after fees both ways. A low win rate with a high payoff
      can be a fine strategy; a high win rate with a payoff well below 1 is the disposition effect's usual fingerprint.</p>
    <div class="table-wrap" id="h-trip-rows"></div>`;
  const draw = () => dataTable($("#h-trip-rows"), [["symbol", "Symbol", "sym"], ["bought", "Bought", "date"], ["sold", "Sold", "date"],
    ["quantity", "Qty", "int"], ["cost", "Cost", "money"], ["proceeds", "Proceeds", "money"], ["pnl", "P&L", "money", true],
    ["return", "Return", "pct1", true], ["days", "Days", "days"]], view.h.trips.rows, view.sort.trips, (s) => { view.sort.trips = s; draw(); });
  draw();
}

// Return on each round trip against how long it was held: losers far to the right are losses held on to.
function tripsChart() {
  const el = $("#h-trips"), rows = view.h.trips.rows.filter((r) => isNum(r.return) && isNum(r.days));
  if (!rows.length) { el.innerHTML = empty("No round trips yet: a sale matched to the buys it came from"); return; }
  const f = frame(el, 240, { left: 52, right: 12, top: 10, bottom: 30 });
  const xt = niceTicks(0, Math.max(30, ...rows.map((r) => r.days)));
  const yt = niceTicks(Math.min(0, ...rows.map((r) => r.return)), Math.max(0, ...rows.map((r) => r.return)));
  const x = linear([0, xt.at(-1)], [f.x0, f.x1]), y = linear([yt[0], yt.at(-1)], [f.y0, f.y1]);
  const grid = yt.map((v) => `<line class="gridline" x1="${f.x0}" x2="${f.x1}" y1="${y(v)}" y2="${y(v)}"/>` +
    `<text x="${f.x0 - 8}" y="${y(v) + 3}" text-anchor="end">${v === 0 ? "0%" : pct(v, 0)}</text>`).join("");
  const dots = rows.map((r, i) => `<circle class="dot ${r.pnl > 0 ? "" : "loss"}" data-i="${i}" cx="${x(r.days)}" cy="${y(r.return)}" r="4"/>`).join("");
  el.innerHTML = svgOpen(f, `Return on each of ${rows.length} round trips against the days held`) + grid +
    xAxis(f, x, { ticks: xt, fmt: (v) => String(v) }) + `<text x="${f.x1}" y="${f.y0 + 28}" text-anchor="end">days held</text>` +
    `<line class="zero" x1="${f.x0}" x2="${f.x1}" y1="${y(0)}" y2="${y(0)}"/>${dots}` +
    `<rect class="hitbox" x="${f.x0 - 12}" y="${f.y1 - 12}" width="${f.x1 - f.x0 + 24}" height="${f.y0 - f.y1 + 24}"/></svg>`;
  const svg = el.querySelector("svg"), marks = [...svg.querySelectorAll(".dot")];
  svg.querySelector(".hitbox").addEventListener("mousemove", (ev) => {
    const p = svgPoint(svg, f, ev);
    let best = null, bd = 24 * 24;
    rows.forEach((r, i) => { const dd = (x(r.days) - p.x) ** 2 + (y(r.return) - p.y) ** 2; if (dd < bd) { bd = dd; best = i; } });
    marks.forEach((m, i) => m.classList.toggle("hl", i === best));
    if (best === null) { hideTip(); return; }
    const r = rows[best];
    showTip(r.symbol, [["Bought", r.bought], ["Sold", r.sold], ["Held", days(r.days)], ["Shares", money(r.quantity, 0)],
      ["P&L", `${cur()} ${signed(r.pnl, 2)}`, r.pnl < 0 ? "alert" : ""], ["Return", pct(r.return, 1), r.return < 0 ? "alert" : ""]], ev);
  });
  svg.querySelector(".hitbox").addEventListener("mouseleave", () => { marks.forEach((m) => m.classList.remove("hl")); hideTip(); });
}

// --- H3 Fees ---------------------------------------------------------------------------------------------------------------
const OTHER = ["cds", "cse", "sec", "stl", "clearing", "foreign_brokerage"];
const feeParts = (y) => ({ brokerage: y.brokerage, other: OTHER.reduce((a, k) => a + (y[k] || 0), 0), unknown: y.unknown });
const PARTS = [["brokerage", "Brokerage", "ink"], ["other", "Exchange, SEC, CDS, levy and clearing", "grey"], ["unknown", "Breakdown not known", "faint"]];

// Fees per year, stacked by who they went to; the tooltip adds that year's drag and turnover.
function feesChart() {
  const el = $("#h-fees"), ys = view.h.fees.years;
  if (!ys.length || !ys.some((y) => y.fees > 0)) { el.innerHTML = empty("No fees yet"); $("#h-fee-keys").innerHTML = ""; return; }
  const used = PARTS.filter(([k]) => ys.some((y) => feeParts(y)[k] > 0.005));
  $("#h-fee-keys").innerHTML = used.map(([, label, cls]) => `<span class="key ${cls}"></span>${esc(label)}`).join("");
  const f = frame(el, 220), ticks = niceTicks(0, Math.max(...ys.map((y) => y.fees)));
  const y = linear([0, ticks.at(-1)], [f.y0, f.y1]), band = (f.x1 - f.x0) / ys.length, w = Math.min(24, band * 0.6);
  let bars = "", labels = "";
  ys.forEach((yr, i) => {
    const cx = f.x0 + band * (i + 0.5), p = feeParts(yr);
    let base = f.y0;
    used.forEach(([k, , cls], j) => {
      const hgt = f.y0 - y(p[k] || 0);
      if (hgt <= 0) return;
      const last = used.slice(j + 1).every(([kk]) => !(p[kk] > 0.005)), gap = !last && hgt > 3 ? 2 : 0;
      bars += `<rect class="bar ${cls}" x="${cx - w / 2}" y="${base - hgt + gap}" width="${w}" height="${hgt - gap}"/>`;
      base -= hgt;
    });
    labels += `<text x="${cx}" y="${f.y0 + 17}" text-anchor="middle">${yr.year}</text>`;
    bars += `<rect class="hitbox" data-i="${i}" x="${cx - band / 2}" y="${f.y1}" width="${band}" height="${f.y0 - f.y1}"/>`;
  });
  el.innerHTML = svgOpen(f, "Fees paid each year, by kind") + yGrid(f, y, ticks) +
    `<g class="axis"><line x1="${f.x0}" x2="${f.x1}" y1="${f.y0}" y2="${f.y0}"/>${labels}</g>${bars}</svg>`;
  el.querySelectorAll(".hitbox").forEach((b) => {
    const yr = ys[+b.dataset.i], p = feeParts(yr);
    b.addEventListener("mouseenter", (ev) => showTip(String(yr.year), [["Fees", `${cur()} ${money(yr.fees)}`],
      ...used.map(([k, label]) => [label.split(",")[0], money(p[k])]),
      ["Fee drag", share(yr.fee_drag, 2)], ["Turnover", share(yr.turnover, 0)], ["Return that year", pct(yr.twr, 1), yr.twr < 0 ? "alert" : ""]], ev));
    b.addEventListener("mousemove", moveTip);
    b.addEventListener("mouseleave", hideTip);
  });
}
function feeTables() {
  const h = view.h, ys = h.fees.years, total = ys.reduce((a, y) => a + y.fees, 0);
  const brok = ys.reduce((a, y) => a + y.brokerage, 0), known = total - ys.reduce((a, y) => a + y.unknown, 0);
  $("#h-fees-note").textContent = total ? `${cur()} ${money(total, 0)} in all${known > 0 ? ` · brokerage ${share(brok / known, 0)} of fees with a breakdown` : ""}` : "";
  const rows = ys.map((y) => ({ ...y, other: feeParts(y).other }));
  const years = () => dataTable($("#h-years"), [["year", "Year", "text"], ["fees", "Fees", "money"], ["brokerage", "Brokerage", "cash"],
    ["other", "Other fees", "cash"], ["unknown", "Not known", "cash"], ["buys", "Bought", "money"], ["sells", "Sold", "money"],
    ["average_value", "Average value", "money"], ["fee_drag", "Fee drag", "share2"], ["turnover", "Turnover", "share"], ["twr", "Return", "pct1", true]],
  rows, view.sort.years, (s) => { view.sort.years = s; years(); });
  years();
  const stocks = () => dataTable($("#h-stocks"), [["symbol", "Symbol", "sym"], ["trades", "Trades", "int"], ["fees", "Fees", "money"],
    ["lifetime", "Lifetime P&L", "money", true], ["share", "Fees ÷ P&L", "share"]],
  h.fees.stocks, view.sort.stocks, (s) => { view.sort.stocks = s; stocks(); });
  stocks();
}

// --- H4 Frozen portfolio ------------------------------------------------------------------------------------------------
function frozenTable() {
  const rows = view.h.frozen.map((y) => ({ ...y, label: y.end.slice(5) === "12-31" ? String(y.year) : `${y.year} so far` }));
  const draw = () => dataTable($("#h-frozen"), [["label", "Year", "text"], ["holdings", "Holdings", "int"], ["frozen", "Left alone", "pct1", true],
    ["twr", "Yours", "pct1", true], ["alpha", "Trading alpha", "pct1", true]],
  rows, view.sort.frozen, (s) => { view.sort.frozen = s; draw(); }, "Needs a 1 January with shares held");
  draw();
}

// --- H5 Switches and never selling ------------------------------------------------------------------------------------------
function switchPanel() {
  const s = view.h.switches, n = view.h.never_sold, c = cur();
  $("#h-sw-note").textContent = s.rows.length ? `A sale, then within a week a buy of another · ${switchCount(s.rows.length)}` : "";
  $("#h-switch").innerHTML = s.rows.length ? `<div class="stats">${s.summary.map((w) => `<div><span class="k">+${months(w.months)} · average</span>
      <span class="v">${pct(w.average, 1)}</span><span class="s">${w.n ? `${w.ahead.k} of ${w.n} ahead · ${range(w.ahead)}` : "None that late yet"}</span></div>`).join("")}</div>
    <p class="note">What you bought against what you sold, from each one's trade price to the same later close, less both legs' fees
      (about 2.26%). Above zero, the new idea beat the old one.</p>` : empty("No switches: a sale followed within a week by a buy of another stock");
  if (s.rows.length) {
    const draw = () => dataTable($("#h-switches"), [["sold_on", "Sold", "date"], ["sold", "Out", "sym"], ["bought", "In", "sym"],
      ["amount", "Bought for", "money"], ["fees", "Fees", "share2"], ["gain_3", "+3M", "pct1", true], ["gain_6", "+6M", "pct1", true], ["gain_12", "+12M", "pct1", true]],
    s.rows, view.sort.switches, (x) => { view.sort.switches = x; draw(); });
    draw();
  }
  $("#h-never").textContent = !n.sales ? "" : !n.priced ? `None of your ${plural(n.sales, "sale")} has a price today.` :
    `Never sold: the shares from ${plural(n.priced, "sale")} would be worth ${c} ${money(n.value, 0)} at the latest close (before selling fees), ` +
    `against the ${c} ${money(n.proceeds, 0)} they brought in${isNum(n.in_aspi) ? `, or ${c} ${money(n.in_aspi, 0)} had that money gone into the ASPI` : ""}. ` +
    `Blunt: it leaves out the dividends they'd have paid.${n.unpriced.length ? ` No price today for ${n.unpriced.join(", ")}.` : ""}`;
}

// --- H6 Buys -------------------------------------------------------------------------------------------------------------------
function buyPanel() {
  const b = view.h.buys, t = view.h.timing;
  $("#h-buy-note").textContent = b.rows.length ? `${plural(b.rows.length, "buy")} on the market` : "";
  if (!b.rows.length) { $("#h-buy-kinds").innerHTML = empty("No market buys yet"); return; }
  const cell = (v, n) => `<td class="num ${v < 0 ? "alert" : ""}">${isNum(v) ? `${pct(v, 1)} <span class="muted">· ${n}</span>` : "—"}</td>`;
  $("#h-buy-kinds").innerHTML = `<div class="scroll-x"><table class="compare wide"><thead><tr><th></th><th class="num">Buys</th>
      <th class="num">20 days on, vs the ASPI</th><th class="num">+3 months</th><th class="num">+12 months</th></tr></thead><tbody>${b.summary.map((s) =>
    `<tr><th>${esc(KIND[s.kind])}</th><td class="num">${s.n}</td>${cell(s.excess_20, s.n_excess_20)}${cell(s.after_3, s.n_after_3)}${cell(s.after_12, s.n_after_12)}</tr>`).join("")}</tbody></table></div>`;
  const rows = b.rows.map((r) => ({ ...r, tag: ONE[r.kind] }));
  const draw = () => dataTable($("#h-buys"), [["date", "Bought", "date"], ["symbol", "Symbol", "sym"], ["quantity", "Qty", "int"],
    ["price", "Price", "money"], ["tag", "Kind", "text"], ["avg_cost", "Your average then", "money"], ["excess_20", "20 days vs ASPI", "pct1", true],
    ["after_3", "+3M", "pct1", true], ["after_12", "+12M", "pct1", true]], rows, view.sort.buys, (s) => { view.sort.buys = s; draw(); });
  draw();
  const gap = isNum(t.gap) ? ` Your money's timing over the same days (money-weighted ${pct(t.xirr, 1)} a year, against ${pct(t.twr, 1)} time-weighted): ` +
    `${pct(t.gap, 1)} a year, ${t.gap >= 0 ? "so money tended to go in before rises" : "so money tended to go in before falls"}.` : "";
  $("#h-timing").textContent = "The small grey number is how many buys each average covers. 20 days on: the stock's return from the price " +
    "you paid over the next 20 trading days, less the ASPI's; above zero, you tend to buy just before a rise. Averaging down isn't wrong " +
    `in itself: this says whether it works for you.${gap}`;
}

// --- H7 The journal --------------------------------------------------------------------------------------------------------------
function journalPanel() {
  const j = view.h.journal, el = $("#h-journal");
  if (!j.file || !j.rows.length) {
    el.innerHTML = `<p class="verdict">${j.file ? "journal.csv has no buy that matches a trade's contract number yet." :
      "Write one line when you buy, and after a year you'll see which reasons make you money."}</p>
      <p class="note">Add data/journal.csv with the columns contract_no, reason, and optionally target, stop and note: the contract
        number from the Contract Note, and why you bought ("results play", "dividend", "a tip"…). No statistic can tell you that
        without the reasons written down at the time. Each stock's page shows them next to its trades.</p>`;
    return;
  }
  el.innerHTML = `<div class="table-wrap" id="h-reasons"></div><div class="table-wrap" id="h-entries"></div>
    <p class="note">Each journaled buy follows its own shares (FIFO): what they sold for less what they cost, the dividends on them,
      and what's left at today's price after selling fees.</p>`;
  const reasons = j.reasons.map((r) => ({ ...r, won: r.made_money.n ? `${r.made_money.k} of ${r.made_money.n}` : "—", range: range(r.made_money) }));
  const a = () => dataTable($("#h-reasons"), [["reason", "Reason", "text"], ["n", "Buys", "int"], ["outcome", "Made", "money", true],
    ["average_return", "Average return", "pct1", true], ["won", "Made money", "text"], ["range", "Range", "text"]],
  reasons, view.sort.reasons, (s) => { view.sort.reasons = s; a(); });
  a();
  const b = () => dataTable($("#h-entries"), [["date", "Bought", "date"], ["symbol", "Symbol", "sym"], ["reason", "Reason", "text"],
    ["cost", "Cost", "money"], ["target", "Target", "money"], ["stop", "Stop", "money"], ["left", "Left", "qty"],
    ["outcome", "Made", "money", true], ["return", "Return", "pct1", true], ["note", "Note", "text"]],
  j.rows, view.sort.entries, (s) => { view.sort.entries = s; b(); });
  b();
}

// --- H8 Cash left idle ------------------------------------------------------------------------------------------------------
function cashPanel() {
  const c = view.h.cash, el = $("#h-cash"), cu = cur();
  if (!c.statements || c.needs_parse) {
    el.innerHTML = empty(c.needs_parse ? "Run smpa run (or smpa parse) once to read the cash balances from your statements"
      : "Needs your monthly Statements of Accounts: they give the cash at the broker");
    return;
  }
  const short = c.days < 365, rate = (annual, period) => (short ? period : annual);
  const acct = rate(c.account, c.period_account), alone = rate(c.shares, c.period_shares), drag = isNum(acct) && isNum(alone) ? alone - acct : null;
  const span = short ? "not annualized" : "a year";
  $("#h-cash-note").textContent = `${plural(c.statements, "statement")} · since ${dayOf(c.start)}`;
  el.innerHTML = `<div class="stats six">
      <div><span class="k">At the broker now</span><span class="v">${money(c.now, 0)}</span><span class="s">${cu} · last statement ${dayOf(c.last)}: ${money(c.confirmed, 0)}</span></div>
      <div><span class="k">Account · shares and cash</span><span class="v">${pct(acct, 1)}</span><span class="s">money-weighted · ${span}</span></div>
      <div><span class="k">Shares alone</span><span class="v">${pct(alone, 1)}</span><span class="s">over the same days · ${span}</span></div>
      <div><span class="k">Cash drag</span><span class="v">${pct(drag, 1)}</span><span class="s">${isNum(drag) ? (drag > 0 ? "what idle cash cost" : "cash held up while shares fell") : c.shares_then === null ? "needs prices on " + dayOf(c.start) : "—"}</span></div>
      <div><span class="k">Average cash</span><span class="v">${money(c.average_cash, 0)}</span><span class="s">${cu}${isNum(c.cash_share) ? ` · ${share(c.cash_share, 0)} of the account` : ""}</span></div>
      <div><span class="k">In T-bills instead</span><span class="v">${isNum(c.tbill_forgone) ? money(c.tbill_forgone, 0) : "—"}</span><span class="s">${isNum(c.tbill_forgone) ? `${cu} of interest, before tax` : "needs data/tbill.csv"}</span></div></div>`;
  const breaks = c.breaks.map((b) => `From ${dayOf(b.since)} to ${dayOf(b.date)} the trades and receipts in your documents come to ` +
    `${cu} ${money(b.worked_out)}, but the statement ${b.at === "opening" ? "opens" : "closes"} at ${money(b.statement)}, so that stretch isn't drawn.`).join(" ");
  $("#h-cash-foot").textContent = "The cash at the broker from each statement's balances, and in between from your trades and receipts. " +
    "Deposits and withdrawals are the account's money in and out; dividends, rights subscriptions and buyouts go through your bank. " +
    (c.breaks.length ? `Where the documents don't add up, a statement, a deposit or a charge is missing, so the account's return starts after the last of those. ${breaks}` : "") +
    (c.approximate ? " Unpriced holdings count at cost." : "");
}

const dayOf = (iso) => (iso ? day(ms(iso)) : "—");

// The cash balance over time, as a step: it changes on the days money moves.
function cashChart() {
  const el = $("#h-cash-chart"), c = view.h.cash;
  if (!c.series || !c.series.length) { el.innerHTML = ""; return; }
  const pts = c.series.map((p) => ({ t: ms(p.date), v: p.cash })), t0 = pts[0].t, t1 = ms(c.end), vals = pts.filter((p) => isNum(p.v));
  const f = frame(el, 200), ticks = niceTicks(Math.min(0, ...vals.map((p) => p.v)), Math.max(0, ...vals.map((p) => p.v)));
  const x = linear([t0, t1], [f.x0, f.x1]), y = linear([ticks[0], ticks.at(-1)], [f.y0, f.y1]);
  let d = "", prev = null;  // a step line, broken where the balance isn't known
  for (const p of pts) {
    if (!isNum(p.v)) { if (prev) d += `H${x(p.t)}`; prev = null; continue; }
    d += prev ? `H${x(p.t)}V${y(p.v)}` : `M${x(p.t)},${y(p.v)}`;
    prev = p;
  }
  if (prev) d += `H${x(t1)}`;
  const marks = c.breaks.map((b) => `<line class="cross picked" x1="${x(ms(b.date))}" x2="${x(ms(b.date))}" y1="${f.y1}" y2="${f.y0}"/>`).join("");
  el.innerHTML = svgOpen(f, "Cash at the broker over time") + yGrid(f, y, ticks) +
    xAxis(f, x, timeTicks(t0, t1, Math.max(3, Math.floor((f.x1 - f.x0) / 90)))) +
    `<line class="zero" x1="${f.x0}" x2="${f.x1}" y1="${y(0)}" y2="${y(0)}"/>${marks}<path class="series" d="${d}"/>` +
    `<g class="hover" visibility="hidden"><line class="cross vx" y1="${f.y1}" y2="${f.y0}"/><rect class="reticle" width="7" height="7"/></g>` +
    `<rect class="hitbox" x="${f.x0}" y="${f.y1}" width="${f.x1 - f.x0}" height="${f.y0 - f.y1}"/></svg>`;
  const svg = el.querySelector("svg"), g = svg.querySelector(".hover");
  svg.querySelector(".hitbox").addEventListener("mousemove", (ev) => {
    const t = t0 + ((svgPoint(svg, f, ev).x - f.x0) / (f.x1 - f.x0)) * (t1 - t0);
    let p = null;
    for (const q of pts) { if (q.t <= t) p = q; else break; }
    if (!p || !isNum(p.v)) { g.setAttribute("visibility", "hidden"); hideTip(); return; }
    g.setAttribute("visibility", "visible");
    const px = x(t);
    g.querySelector(".vx").setAttribute("x1", px); g.querySelector(".vx").setAttribute("x2", px);
    g.querySelector(".reticle").setAttribute("x", px - 3.5); g.querySelector(".reticle").setAttribute("y", y(p.v) - 3.5);
    showTip(day(t), [["Cash", `${cur()} ${money(p.v)}`, p.v < 0 ? "alert" : ""], ["Since", day(p.t)]], ev);
  });
  svg.querySelector(".hitbox").addEventListener("mouseleave", () => { g.setAttribute("visibility", "hidden"); hideTip(); });
}
