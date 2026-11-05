# 01 · Understanding your numbers

This doc covers what the dashboard already computes, the ideas the other docs build on, and the places
where the numbers are easy to misread. Read it with the dashboard open and work one number out by hand
from the Transactions tab.

## 1. One identity ties everything together

**Foundation.** Every profit figure comes from this:

```
total gain = (money you got back + value of what you still hold) − money you put in
           = realized P&L + unrealized P&L + dividends
```

"Money you got back" means sale proceeds after fees, plus dividends. "Money you put in" means purchase costs,
fees included.

Your **cost method** (average cost, FIFO, …) only decides how that total splits between realized (from sales)
and unrealized (shares still held). It never changes the total. Here's an example taken from
`tests/test_analysis.py`: buy 100 shares at 10, buy 100 at 20, sell 50 at 30, price now 25, no fees.

| | Average cost (what the app uses) | FIFO (oldest shares sold first) |
|---|---|---|
| Cost of the 50 sold | 50 × 15 = 750 | 50 × 10 = 500 |
| Realized | 1,500 − 750 = **750** | 1,500 − 500 = **1,000** |
| Unrealized | 150 × 25 − 2,250 = **1,500** | 150 × 25 − 2,500 = **1,250** |
| Total | **2,250** | **2,250** |

Check: 1,500 back + 3,750 still held − 3,000 put in = 2,250. This identity makes a good automated test
([06 §7](06-data-and-engineering.md#7-tests-that-check-the-maths)).

Your CDS account just holds a count of each share. It doesn't keep separate lots, so average cost is the
natural method. FIFO lots are still useful for showing how long you've held shares ([02](02-per-ticker-page.md#lots)).

## 2. Fees: the 2.3% hurdle

**Foundation.** Every trade pays a stack of percentage fees. The 2026 test fixtures show these rates
(`tests/fixtures/ndb/*2026-09*`, copies of real layouts). Your own Contract Notes are the authority, and the
`trades` table stores each fee in its own column.

| Fee | Rate |
|---|---|
| Brokerage | 0.640% |
| Share Transaction Levy (STL; "GOV CESS" on Trade Confirmations) | 0.300% |
| CSE fee | 0.084% |
| SEC fee | 0.072% |
| CDS fee | 0.024% |
| Clearing fee (layouts first seen in August 2025 onwards) | 0.012% |
| **Total, each way** | **1.132%** (1.12% before the clearing fee) |

Buying costs `price × (1 + f)` and selling brings in `price × (1 − f)`. To break even, a stock has to rise by

```
(1 + f) / (1 − f) − 1 = 1.01132 / 0.98868 − 1 = 2.29%
```

For example, buying 1,000 shares at 50.00 costs 50,566.00. To get that back you need to sell at
50,566 / (1,000 × 0.98868) = **51.14**.

What this means for the app:

- **Unrealized P&L is too optimistic.** Add a *liquidation value*, `market value × (1 − f)`, which is what you'd
  actually receive. Also show a *break-even price*, `average cost / (1 − f)`, on each holding.
- **Frequent trading is expensive.** Turning the whole portfolio over 10 times a year costs about 10 × 2 ×
  1.132% ≈ 23% of it in fees. [05](05-your-trading-habits.md#4-what-fees-cost-you) measures this from your own trades.
- **Don't hard-code `f`.** The rates changed in 2025. Work out the rate on a given day from your nearest
  Contract Note ([03 §6](03-what-if-i-sold.md#6-fees-for-a-sale-that-didnt-happen)).

## 3. Three kinds of return

**Foundation → Intermediate.**

| Return | Question it answers | Where it is |
|---|---|---|
| **Simple**: gain ÷ cost | "How much did I make per rupee?" Ignores time. | Unrealized % on holdings |
| **Money-weighted (XIRR)**: the one yearly rate at which all your cash flows net to zero | "How did *my money* do?" Includes when you happened to add or withdraw it. | "Annual return" readout (`analysis.xirr`) |
| **Time-weighted (TWR)**: returns between cash flows, multiplied together | "How good were my *picks*?" Ignores how much money was in at each moment. This is what funds report and what you compare with an index. | Not built yet ([04 §1](04-performance-and-risk.md#1-time-weighted-return)) |

Here's an example where they disagree even on the sign:

| Date | What happens | Value after |
|---|---|---|
| 2 Jan 2023 | Buy for 1,000 | 1,000 |
| 2 Jan 2024 | Shares have doubled to 2,000. Buy 10,000 more | 12,000 |
| 2 Jan 2025 | Shares halve | 6,000 |

- **TWR**: (1 + 100%) × (1 − 50%) − 1 = **0%**. The stock ended where it started.
- **XIRR**: **−43.1% a year** (worked out with the app's own `xirr`). Most of your money arrived at the top.

Both are correct. The gap between them is the effect of your *timing*. Showing both is more useful than either
one alone.

**Annualizing.** A +10% gain in 6 months is (1.10)² − 1 = **21% a year**, not 20%, because returns compound.
Annualizing short periods magnifies noise, so the dashboard already shows the plain return when you have
under a year of history. Keep that rule for every new return figure.

## 4. What the dashed ASPI line means

**Intermediate.** Chart 03's dashed line is a **public market equivalent (PME)**, built by `analysis.benchmark`.
It replays your exact cash flows into the ASPI: each buy buys index "units" at that day's level, and each sale
or dividend sells some. If your line ends above it, your picks beat simply putting the same rupees, on the same
days, into the market.

Two catches, both covered in [04 §5](04-performance-and-risk.md#5-better-benchmarks):

- The **ASPI is a price index**. It ignores dividends, while your side counts them, so you get a small head start.
  The CSE also calculates a total-return version, the **All Share Total Return Index (ASTRI)**, which is the
  fair comparison.
- This style of PME **can go negative**. If you sell big winners, the index side has to "sell" more units than
  it owns. So the line stops on the day that would happen, and the chart's header gives **direct alpha**
  instead: your yearly return over the ASPI with the same cash flows, which can't break this way.

## 5. Inflation and the rupee

**Intermediate.** Your returns are in rupees, and rupees lost value fast.

- CCPI inflation reached **69.8% year-on-year in September 2022** (Central Bank of Sri Lanka).
- The rupee lost **44.8% against the US dollar in 2022** (CBSL).

```
real return = (1 + nominal return) / (1 + inflation) − 1
```

Say your portfolio rose 60% over a stretch when prices rose 70%. Then 1.60 / 1.70 − 1 = **−5.9%**. You
gained rupees but lost buying power. For a portfolio that started in 2021 this matters more than almost
anything else in these docs. [04 §6](04-performance-and-risk.md#6-real-and-dollar-returns) shows how to
calculate it properly from your cash flows.

## 6. Dividends and tax

**Foundation.**

- **Dividends are income, not a cost reduction.** The app gets this right. Subtracting dividends from cost
  would hide them inside unrealized P&L.
- **Ex-date (XD).** You get the dividend if you own the share at the start of the XD day. The price usually
  drops by about the dividend that day, so a dividend isn't free money: value moves from the share price into
  cash. That's why a total-return benchmark matters.
- **Withholding tax.** Dividends reach you after tax is taken off (15% is the most often quoted rate; check the
  current one). The README says `per_share` entries are counted *before* tax, so they overstate your income.
  Either enter `amount` (the cash you received), or add a setting such as `[tax].dividend_withholding = 0.15`
  and apply it to `per_share` rows in `actions._row` / `analysis._apply_action`.
- **Capital gains tax.** Gains on shares quoted on the CSE have been excluded from CGT since CGT came back in
  2018. The Inland Revenue (Amendment) Act No. 11 of 2026 changed CGT rates. Press coverage says quoted shares
  are still excluded, but check with the IRD or a tax adviser before relying on that. If listed shares ever
  become taxable, the cost method from §1 suddenly matters, because it decides how much gain each sale
  realizes, and so how much tax falls in each year.

## 7. Glossary

| Term | Meaning |
|---|---|
| Cost basis | What you paid for the shares you still hold, fees included |
| Realized / unrealized | Profit locked in by selling / profit on paper on shares you still hold |
| Lot | One purchase (date, quantity, price) that you track separately |
| Ex-date (XD) | First day a share trades without its next dividend |
| T+2 | Trades settle (cash and shares change hands) two trading days after the trade date |
| Benchmark | What you compare against, usually an index such as the ASPI |
| PME | Public market equivalent: your cash flows replayed into an index |
| Drawdown | Fall from the highest value so far ([04](04-performance-and-risk.md#3-risk)) |
| Volatility | How much returns jump around: the standard deviation of returns, annualized |
| Beta | How much a stock tends to move when the market moves 1% |
| Liquidity | How easily you can sell without moving the price ([02](02-per-ticker-page.md#liquidity)) |
| Basis point (bp) | 0.01%. Fees are often quoted this way: 0.30% is 30 bp |
