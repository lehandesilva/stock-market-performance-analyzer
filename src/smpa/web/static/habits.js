// The Habits page (#/habits): your decisions, judged from your own trades (docs/05). Every figure shows how many
// trades it rests on; with tens of trades, not thousands, a range matters more than the point.

import { $, box, dataTable, day, empty, esc, isNum, money, ms, pct, plural, share, signed } from "./lib.js";

const view = { p: null, sort: {} };
const fix = (v, dp = 2) => (isNum(v) ? v.toFixed(dp) : "—");
const range = (r) => (r ? `${share(r[0], 0)}–${share(r[1], 0)}` : "—");

export function renderHabits(el, p) {
  view.p = p;
  if (p.empty) { el.innerHTML = empty("No trades yet"); return; }
  el.innerHTML = `
    <section class="ticker-title"><h2>Habits</h2><div class="muted">Your decisions, judged from your own ${plural(p.trades, "trade")} ·
      every figure with how many it rests on · to ${day(ms(p.as_of))}</div></section>
    <section class="readouts" id="h-readouts"></section>
    <div class="row r11">
      <section class="panel"><div class="sec"><span class="n">H1</span><span class="t">Selling winners too early</span><span class="r">On the days you sold</span></div>
        <div id="h-disp"></div></section>
      <section class="panel"><div class="sec"><span class="n">H2</span><span class="t">Round trips</span><span class="r">Each sale matched to its buys, oldest first</span></div>
        <div id="h-trips"></div></section>
    </div>
    <section class="panel"><div class="sec"><span class="n">H3</span><span class="t">What fees cost you</span><span class="r">Per calendar year</span></div>
      <div class="table-wrap" id="h-fees"></div>
      <p class="note">Fee drag: fees ÷ the year's average portfolio value. Turnover: the smaller of buys and sells ÷ that value; at 100% you
        replaced the whole portfolio once, which costs about 2.26% of it in fees. "Not broken down": trades only a statement showed, or typed in.</p>
      <div class="table-wrap" id="h-fee-stock"></div></section>
    <div class="row r11">
      <section class="panel"><div class="sec"><span class="n">H4</span><span class="t">Did trading beat holding?</span><span class="r">1 January's holdings, left alone</span></div>
        <div class="table-wrap" id="h-frozen"></div><div id="h-switch"></div><div id="h-never"></div></section>
      <section class="panel"><div class="sec"><span class="n">H5</span><span class="t">Timing</span><span class="r">Each buy, to date</span></div>
        <div id="h-timing"></div></section>
    </div>
    <div class="row r11">
      <section class="panel"><div class="sec"><span class="n">H6</span><span class="t">A rule instead of you</span><span class="r">Trailing stop on every buy</span></div>
        <div id="h-stop"></div></section>
      <section class="panel"><div class="sec"><span class="n">H7</span><span class="t">Why you bought</span><span class="r">data/journal.csv</span></div>
        <div class="table-wrap" id="h-journal"></div></section>
    </div>
    <section class="panel"><div class="sec"><span class="n">H8</span><span class="t">Cash left idle</span><span class="r" id="h-cash-note"></span></div>
      <div id="h-cash"></div></section>`;
  readouts();
  dispositionPanel();
  tripsPanel();
  feesPanel();
  holdingPanel();
  timingPanel();
  stopPanel();
  journalPanel();
  cashPanel();
}

function readouts() {
  const p = view.p, d = p.disposition, rt = p.round_trips, b = p.buy_timing, n = p.never_sold;
  const full = p.fees_by_year.filter((y) => isNum(y.drag)), fy = full.at(-1);
  const alpha = p.frozen.filter((y) => isNum(y.alpha)), ay = alpha.at(-1);
  const sw = p.switch_summary.filter((s) => s.n).at(-1);
  $("#h-readouts").innerHTML = [
    box("Gains realized vs losses", isNum(d.pgr.value) && isNum(d.plr.value) ? `${fix(d.pgr.value)} · ${fix(d.plr.value)}` : "—",
      isNum(d.difference) ? (d.difference > 0 ? "Quicker to sell winners than losers" : "Not quicker to sell winners") : "Needs a sale while holding others",
      d.difference > 0),
    box("Round trips won", rt.n ? esc(share(rt.win_rate, 0)) : "—", rt.n ? `${rt.wins} of ${rt.n} · 95% range ${range(rt.win_range)}` : "No sales yet", false),
    box("Profit factor", esc(fix(rt.profit_factor)), rt.n ? `Expectancy ${signed(rt.expectancy)} a trip` : "Wins ÷ losses", isNum(rt.profit_factor) && rt.profit_factor < 1),
    box(fy ? `Fee drag · ${fy.year}` : "Fee drag", esc(share(fy?.drag, 2)), fy ? `Turnover ${share(fy.turnover, 0)} · ${plural(fy.trades, "trade")}` : "Needs 20 days with a value in a year", false),
    box(ay ? `Trading alpha · ${ay.year}` : "Trading alpha", esc(pct(ay?.alpha, 1)), ay ? `Yours ${pct(ay.twr, 1)} · left alone ${pct(ay.frozen, 1)}` : "Needs a year of prices", ay?.alpha < 0),
    box(sw ? `Switches · ${sw.months} months` : "Switches", esc(pct(sw?.gain, 1)), sw ? `New over old, after fees · n ${sw.n}` : "A sale, then a buy within a week", sw?.gain < 0),
    box("Never sold", isNum(n.difference) ? esc(signed(n.difference)) : "—", n.sales ? `Shares from ${plural(n.sales, "sale")}, today, after fees` : "No sales yet", n.difference > 0),
    box("Buy timing · 20 days", esc(pct(b.excess, 1)), b.n ? `vs the ASPI · n ${b.n} · ${share(b.positive, 0)} beat it` : "Needs ASPI history", b.excess < 0),
  ].join("");
}

function dispositionPanel() {
  const d = view.p.disposition, c = d.counts, rt = view.p.round_trips;
  if (!c.days) { $("#h-disp").innerHTML = empty("No market sales yet"); return; }
  $("#h-disp").innerHTML = `<div class="scroll-x"><table class="compare"><thead><tr><th></th><th class="num">Up</th><th class="num">Down</th></tr></thead><tbody>
    <tr><th>Sold that day</th><td class="num">${c.rg}</td><td class="num">${c.rl}</td></tr>
    <tr><th>Kept</th><td class="num">${c.pg}</td><td class="num">${c.pl}</td></tr>
    <tr><th>Share sold</th><td class="num">${fix(d.pgr.value)}<span class="muted"> · ${range(d.pgr.range)}</span></td>
      <td class="num">${fix(d.plr.value)}<span class="muted"> · ${range(d.plr.range)}</span></td></tr></tbody></table></div>
    <p class="note">On each of ${plural(c.days, "selling day")}, every stock held was up or down against its average cost, and sold or kept.
      Odean (1998) found individual investors sold 0.15 of their gains and 0.10 of their losses: the disposition effect.
      ${c.unpriced ? `${plural(c.unpriced, "holding")} had no close within a week and ${c.unpriced === 1 ? "is" : "are"} left out. ` : ""}
      Median days held: winners ${isNum(rt.days_winners) ? money(rt.days_winners, 0) : "—"}, losers ${isNum(rt.days_losers) ? money(rt.days_losers, 0) : "—"}.
      Holding losers much longer is the same habit from another side.</p>`;
}

function tripsPanel() {
  const r = view.p.round_trips;
  if (!r.n) { $("#h-trips").innerHTML = empty("No market sales matched to a buy yet"); return; }
  const rows = [["Win rate", `${share(r.win_rate, 0)} · ${r.wins} of ${r.n}`, `95% range ${range(r.win_range)}`],
    ["Average win · loss", `${signed(r.average_win)} · ${signed(r.average_loss)}`, "Fees on both legs included"],
    ["Payoff ratio", fix(r.payoff), "Average win ÷ average loss"], ["Profit factor", fix(r.profit_factor), "Total wins ÷ total losses; above 1 makes money"],
    ["Expectancy", signed(r.expectancy), "The average result per round trip"], ["Total · return", `${signed(r.total)} · ${pct(r.return, 1)}`, `On ${money(r.n, 0)} trips' cost`],
    ["Fees in them", money(r.fees, 0), "Buy and sell legs"]];
  $("#h-trips").innerHTML = `<div class="scroll-x"><table class="compare wide"><tbody>${rows.map(([k, v, w]) =>
    `<tr><th>${esc(k)}</th><td class="num">${esc(v)}</td><td class="what">${esc(w)}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">A high win rate with a payoff ratio well below 1 is the disposition effect's usual fingerprint; a low one with a high payoff can be a fine strategy.</p>
    <div class="table-wrap" id="h-trip-list"></div>`;
  const draw = () => dataTable($("#h-trip-list"), [["symbol", "Symbol", "sym"], ["bought", "Bought", "date"], ["sold", "Sold", "date"],
    ["quantity", "Qty", "int"], ["cost", "Cost", "money"], ["proceeds", "Proceeds", "money"], ["pnl", "P&L", "money", true], ["days", "Days", "days"]],
  view.p.trips, view.sort.trips || { key: "sold", dir: -1 }, (s) => { view.sort.trips = s; draw(); });
  draw();
}

function feesPanel() {
  const years = view.p.fees_by_year.map((y) => ({ ...y, market: y.cds + y.cse + y.sec + y.clearing + y.foreign_brokerage }));
  dataTable($("#h-fees"), [["year", "Year", "text"], ["trades", "Trades", "int"], ["fees", "Fees", "money"], ["average_value", "Avg value", "money"],
    ["drag", "Fee drag", "share"], ["turnover", "Turnover", "share"], ["twr", "Return (TWR)", "pct1", true], ["brokerage", "Brokerage", "money"],
    ["stl", "Levy (STL)", "money"], ["market", "CSE, SEC, CDS, clearing", "money"], ["unknown", "Not broken down", "money"]],
  years, view.sort.fees, (s) => { view.sort.fees = s; feesPanel(); }, "No trades yet");
  const stocks = view.p.fees_by_stock;
  const draw = () => dataTable($("#h-fee-stock"), [["symbol", "Symbol", "sym"], ["trades", "Trades", "int"], ["fees", "Fees", "money"],
    ["lifetime", "Lifetime P&L", "money", true], ["fees_share", "Fees ÷ P&L", "share"]],
  stocks, view.sort.stock, (s) => { view.sort.stock = s; draw(); });
  draw();
}

function holdingPanel() {
  const p = view.p;
  dataTable($("#h-frozen"), [["year", "Year", "text"], ["holdings", "Held 1 Jan", "int"], ["frozen", "Left alone", "pct1", true],
    ["twr", "What you did (TWR)", "pct1", true], ["alpha", "Trading alpha", "pct1", true], ["missing", "No price for", "text"]],
  p.frozen, view.sort.frozen, (s) => { view.sort.frozen = s; holdingPanel(); }, "Needs a year with prices at both ends");
  const s = p.switch_summary, sw = p.switches;
  $("#h-switch").innerHTML = `<p class="note">Trading alpha: your time-weighted return for the year minus what 1 January's holdings would have done
    untouched (prices and dividends). Positive: your trades that year added value.</p>` +
    (sw.length ? `<div class="scroll-x"><table class="compare"><thead><tr><th>Switches · ${plural(sw.length, "pair")}</th>${s.map((x) => `<th class="num">${x.months}M</th>`).join("")}</tr></thead>
      <tbody><tr><th>New over old, after fees</th>${s.map((x) => `<td class="num ${x.gain < 0 ? "alert" : ""}">${pct(x.gain, 1)}</td>`).join("")}</tr>
      <tr><th>New one did better</th>${s.map((x) => `<td class="num">${x.n ? `${share(x.better, 0)} of ${x.n}` : "—"}</td>`).join("")}</tr></tbody></table></div>
      <p class="note">A sale followed within a week by a buy of something else. Each compares the stock bought with the one sold, from the trade
        prices, less the fees of both trades (about 2.26%).</p>` : "");
  const n = p.never_sold;
  $("#h-never").innerHTML = n.sales ? `<p class="note">Never sold: the shares from your ${plural(n.sales, "market sale")} would be worth
    ${money(n.worth_today, 0)} today after selling fees, against the ${money(n.proceeds, 0)} the sales brought in
    (${signed(n.difference)}; dividends since left out${n.unpriced ? `; ${plural(n.unpriced, "sale")} without a close today left out` : ""}).</p>` : "";
}

function timingPanel() {
  const p = view.p, a = p.adding, b = p.buy_timing;
  const row = (label, g) => `<tr><th>${esc(label)}</th><td class="num">${g.n}</td><td class="num ${g.return < 0 ? "alert" : ""}">${pct(g.return, 1)}</td>
    <td class="num">${g.priced ? `${share(g.made_money, 0)}<span class="muted"> · ${range(g.made_money_range)}</span>` : "—"}</td>
    <td class="num">${fix(g.ks_pme)}</td></tr>`;
  $("#h-timing").innerHTML = `<div class="scroll-x"><table class="compare"><thead><tr><th>Buys</th><th class="num">n</th><th class="num">Return</th>
    <th class="num">Made money</th><th class="num">KS-PME</th></tr></thead><tbody>
    ${row("A new holding", a.new)}${row("Averaging down", a.down)}${row("Adding to a winner", a.up)}</tbody></table></div>
    <p class="note">Each buy as its own lot, to date: its sales and dividends plus the shares left at today's close after fees, against what it cost.
      Averaging down: a buy below your average cost then; adding to a winner: above it. KS-PME grows every amount at the ASPI, so time held
      counts (median; above 1 beat the index). Averaging down isn't wrong in itself: this shows whether it works for you.</p>
    <p class="note">Buy timing: over the 20 trading days after each buy, the stock did ${pct(b.excess, 1)} against the ASPI on average
      (${plural(b.n, "buy")}${b.n ? `; ${share(b.positive, 0)} beat it, range ${range(b.positive_range)}` : ""}). Positive: you tend to buy just before a rise.</p>`;
}

function stopPanel() {
  const t = view.p.trailing_stop;
  if (!t.length || !t[0].lots) { $("#h-stop").innerHTML = empty("Needs buys with daily closes from the day you bought"); return; }
  $("#h-stop").innerHTML = `<div class="scroll-x"><table class="compare"><thead><tr><th>Stop</th>
    <th class="num">Fired</th><th class="num">The rule</th><th class="num">You</th><th class="num">Rule − you</th></tr></thead><tbody>
    ${t.map((r) => `<tr><th>${share(r.stop, 0)}</th><td class="num">${r.fired} of ${r.lots}</td><td class="num">${money(r.rule, 0)}</td>
      <td class="num">${money(r.actual, 0)}</td><td class="num ${r.difference > 0 ? "alert" : ""}">${signed(r.difference)}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">Hindsight can't be a strategy, but a rule can be tested. Each buy is sold at the first close the stop's distance below the highest
      close since (or kept to today), fees and dividends included, against what you did with the same shares. Only buys made while price
      history was being kept count. Money from an earlier exit isn't reinvested on either side.</p>`;
}

function journalPanel() {
  const p = view.p, r = p.reasons;
  if (!p.journal_entries) {
    $("#h-journal").innerHTML = `<p class="note">Add data/journal.csv with contract_no and reason (optional target, stop and note), one line
      when you buy. After a year, group your buys by reason ("results play", "dividend", "a tip") and see which reasons make you money: no
      statistic can tell you that unless the reasons were written down at the time. The stock pages show each reason next to its trade.</p>`;
    return;
  }
  dataTable($("#h-journal"), [["reason", "Reason", "text"], ["n", "Buys", "int"], ["cost", "Cost", "money"], ["return", "Return", "pct1", true],
    ["made_money", "Made money", "share"], ["ks_pme", "KS-PME", "ratio"]], r, view.sort.reason,
  (s) => { view.sort.reason = s; journalPanel(); }, "No journal entry matches a buy");
  if (p.journal_unmatched.length) {
    $("#h-journal").insertAdjacentHTML("beforeend", `<p class="note alert">No buy has contract ${esc(p.journal_unmatched.join(", "))}: check journal.csv.</p>`);
  }
}

// The account (shares plus cash at the broker) against the shares alone, over an unbroken run of statements.
function cashPanel() {
  const c = view.p.cash_idle;
  if (!c || !c.since) {
    $("#h-cash").innerHTML = empty(c ? "Needs price history from the start of a statement period" : "Needs statements: their closing balances are the cash series");
    return;
  }
  const a = c.annualized ? " a year" : "";
  $("#h-cash-note").textContent = `${day(ms(c.since))} → ${day(ms(c.until))} · ${plural(c.used, "statement")}`;
  $("#h-cash").innerHTML = `<div class="stats">
    <div><span class="k">Shares alone${a}</span><span class="v">${pct(c.shares, 1)}</span><span class="s">Money-weighted, same days</span></div>
    <div><span class="k">Whole account${a}</span><span class="v">${pct(c.account, 1)}</span><span class="s">Shares plus broker cash</span></div>
    <div><span class="k">Cash drag</span><span class="v ${c.drag > 0 ? "alert" : ""}">${pct(c.drag, 1)}</span><span class="s">${c.drag > 0 ? "What idle cash cost" : "Cash softened a fall"}</span></div>
    <div><span class="k">Average cash</span><span class="v">${money(c.average_cash, 0)}</span><span class="s">${isNum(c.cash_share) ? `${share(c.cash_share, 0)} of the account` : ""}</span></div></div>
    <p class="note">Deposits and withdrawals are the account's money in and out, along with what bypasses the broker: dividends and buyouts paid
      to your bank, rights paid to the company. Cash is each statement's closing balance (negative while a purchase awaits settlement).
      ${isNum(c.tbill_forgone) ? `In T-bills, the average balance would have earned about ${money(c.tbill_forgone, 0)}.` : "Add data/tbill.csv to see what the cash would have earned in T-bills."}
      ${c.gaps ? "Earlier statements are missing or don't follow on, so they're left out: no deposit can be missed." : ""}</p>`;
}
