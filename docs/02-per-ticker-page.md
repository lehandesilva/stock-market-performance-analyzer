# 02 · A page per stock

**Goal:** click any symbol (a holdings row, a node in chart 01, a row in the position index, any table) and get
one page that answers *"how has this stock done for me, and why?"* Today the closest thing is the filter box,
which narrows the tables but doesn't calculate anything per stock.

## What it looks like

All the numbers come from the worked example in [03](03-what-if-i-sold.md#the-worked-example): ABC bought
twice, partly sold, with two dividends. Fees are 1.12% per trade.

```
ABC.N0000 · ABC HOLDINGS PLC · LK0000N00009                         If sold on [2025-03-03 ▾]
──────────────────────────────────────────────────────────────────────────────────────────────
Held 900 · avg cost 47.19 · break-even 47.72 · price 55.00 · 52-week 48.10–66.40 · β 1.00
Lifetime +23,409 = realized 12,029 + unrealized 7,030 + dividends 4,350
XIRR 17.0%/yr (16.6% after selling costs) · vs ASPI ±x.x%/yr · fees 1,241 (5.3% of the gain)
──────────────────────────────────────────────────────────────────────────────────────────────
[A] Price · ▲ your buys ▼ your sells · average cost (step line) · break-even (dotted)
[B] Gain if sold on each day · ◆ best day in hindsight · ▼ your actual sales          → doc 03
[C] Shares held, and cost basis vs market value
──────────────────────────────────────────────────────────────────────────────────────────────
Lots (FIFO) │ Trades + fee breakdown │ Dividends & corporate actions │ After each sale (+1m/+3m/+12m)
```

## The numbers

| Metric | How | Data | Level |
|---|---|---|---|
| Shares, avg cost, cost basis, value, weight | Already computed by `analysis.compute` | — | Foundation |
| Liquidation value | value × (1 − f) | fee rate ([01 §2](01-understanding-your-numbers.md#2-fees-the-23-hurdle)) | Foundation |
| Break-even price | avg cost ÷ (1 − f) | | Foundation |
| **Lifetime P&L** | realized + unrealized + dividends, for this symbol | `pf.realized`, `pf.actions` | Foundation |
| Fees on this stock | Each fee column summed; also fees ÷ lifetime P&L | `trades` | Foundation |
| 52-week range | (price − low) ÷ (high − low); drawdown from high = price ÷ high − 1 | `p12HiPrice`, `p12LowPrice` | Foundation |
| **XIRR for this stock** | `xirr` on this symbol's buys, sales and dividends, plus today's value | | Intermediate |
| Holding period | First buy → today; average age of the shares you hold (FIFO lots, weighted by quantity) | | Intermediate |
| Yield on cost | Dividends in the last 12 months ÷ cost basis | actions | Intermediate |
| Trailing yield | Dividends per share in the last 12 months ÷ price | actions | Intermediate |
| CSE's published betas | `triASIBetaValue`, `betaValueSPSL` | `companyInfoSummery` | Intermediate |
| **vs the ASPI** | KS-PME or direct alpha on this stock's cash flows ([04 §5](04-performance-and-risk.md#5-better-benchmarks)) | `index_history` | Advanced |
| Volatility, max drawdown since first buy, own beta | [04 §3](04-performance-and-risk.md#3-risk) | `price_history` | Advanced |
| Days to exit | Position value ÷ (participation × average daily turnover) | turnover | Advanced |
| Return contribution | [04 §7](04-performance-and-risk.md#7-attribution-where-the-return-came-from) | | Advanced |
| Risk contribution | wᵢ × Cov(rᵢ, r_p) ÷ σ_p | daily returns | Expert |

### Lifetime P&L and XIRR for one stock

`analysis._Ledger` tracks each symbol separately. The only thing it shares across symbols is the
portfolio-wide `invested` total. So filtering the trades and corporate actions to one symbol and running the
same engine gives exact per-stock numbers:

```python
def ticker_summary(tx, actions, prices, symbol, today):
    """Lifetime P&L and money-weighted return for one symbol."""
    t, a = tx[tx["symbol"] == symbol], actions[actions["symbol"] == symbol]
    pf = compute(t, prices, a)
    h = pf.holdings
    value = float(h["market_value"].fillna(h["cost_basis"]).sum())  # unpriced: at cost, like the dashboard
    unrealized = float(h["unrealized_pnl"].fillna(0).sum())
    return {"realized": pf.realized_pnl, "unrealized": unrealized, "dividends": pf.dividend_income,
            "lifetime": pf.realized_pnl + unrealized + pf.dividend_income, "fees": pf.fees,
            "xirr": xirr(cash_flows(t, pf.dividends) + [(pd.Timestamp(today), value)])}
```

On the worked example this gives lifetime 23,409.04, fees 1,240.96 and an XIRR of 17.0%. That XIRR values
the remaining shares at market price. Valued at what you'd actually get after selling fees, it's 16.6%. Show
both, labelled.

**Test idea:** adding up every symbol's lifetime P&L must give the dashboard's total gain. If it doesn't,
something is being counted twice or dropped.

**Yield on cost vs trailing yield.** Yield on cost tells you what your *money* earns in dividends. Trailing
yield tells you what a *new buyer* would earn today. On a stock that has risen a lot, yield on cost looks great
even when the stock is now expensive. That's a classic way to fool yourself.

### Lots

The app's official method is average cost ([01 §1](01-understanding-your-numbers.md#1-one-identity-ties-everything-together)).
FIFO lots are for *display only*: they show how old your shares are.

- Buy: add a lot (date, quantity, cost including fees).
- Sell n shares: take them from the oldest lots first. A partly used lot keeps what's left.
- Bonus or split: scale every open lot's quantity by the ratio and keep its cost. Round down the way
  `_apply_action` does, and put the rounding difference on the newest lot so the lots still add up to the
  position.
- Delisting: close every lot. Dividends: no change.

In the worked example, FIFO takes the 600 shares sold from the first lot, leaving:

| Bought | Shares | Cost/share | Age on 2026-11-05 |
|---|---|---|---|
| 2024-02-01 | 400 | 40.45 | 1,008 days |
| 2024-08-01 | 500 | 60.67 | 826 days |

Average age is 907 days. The FIFO cost of these lots (46,515.20) **is not** the average-cost basis
(42,470.40). Label the table so nobody adds them up.

### Liquidity

Many CSE shares trade thinly. The last price is what someone paid for a few shares. Selling a large block can
push the price down, which is called *market impact*. A common rule of thumb is to sell no more than 10–20% of
a day's volume.

```
average daily turnover = turnover over the last year ÷ trading days in that year
days to exit           = position value ÷ (0.15 × average daily turnover)
```

Flag anything over a day. A position that takes a week to sell is worth less than its market value says,
especially in a falling market. `companyInfoSummery` sends `ytdTurnover` and `tdyTurnover` (`p12Turnover` was
null in a response checked on 5 Nov 2026), and `tradeSummary` sends each traded stock's `turnover` and
`sharevolume`. Storing that daily ([06 §3](06-data-and-engineering.md#3-keep-what-the-cse-api-already-sends))
gives an exact figure. Also count the days with no trades in the last 3 months. That's the most direct measure
of thin trading, and it tells you how much to trust the stock's beta and volatility.

### Beta

`companyInfoSummery` returns `reqSymbolBetaInfo` with `triASIBetaValue`, `betaValueSPSL`, `triASIBetaPeriod`
and `quarter`. The first is presumably measured against the ASPI's total-return version (its name suggests
"TRI ASI"). The second is against the S&P SL20. A beta of 1.3 means the stock has tended to move 1.3% for every
1% the market moves. Show the CSE's figure next to your own estimate ([04 §3](04-performance-and-risk.md#3-risk)).
If they differ a lot, the stock probably trades too thinly for either figure to mean much.

### Risk contribution

**Expert.** Volatilities don't simply add up. A volatile stock that tends to rise when your others fall
*lowers* total risk. Each stock's contribution to portfolio volatility is
`wᵢ × Cov(rᵢ, r_portfolio) ÷ σ_portfolio`, and the contributions add up exactly to σ_portfolio. A line like
"ABC is 25% of your money but 40% of your risk" is about the clearest position-sizing signal there is.

## Charts

- **[A] Price with your trades.** `price_history` for the symbol, ▲/▼ markers from the trades, an average-cost
  step line (it only changes on buys and corporate actions, so replay the ledger), and a break-even line. Seeing
  your average cost against the price shows at a glance whether you bought well.
- **[B] Gain if sold on each day.** The "what if" curve from [03 §5](03-what-if-i-sold.md#5-best-and-worst-exits-in-hindsight).
- **[C] Shares and money.** Shares held as a step line, with cost basis and market value as two lines. The gap
  between them is your unrealized P&L over time.

Reuse the chart helpers in `src/smpa/web/static/app.js` (`frame`, `linear`, `niceTicks`, `timeTicks`, `xAxis`,
`yGrid`, `showTip`) so the page matches the dashboard.

## How to build it

**Back end**

- `analysis.py`: `ticker_detail(tx, actions, prices, price_hist, index_hist, symbol, today)`. Filter to the
  symbol, then reuse what's already there:
  - `compute` gives position and P&L.
  - `value_history` on the filtered trades gives *this stock's* daily value.
  - `benchmark` on that gives this stock against the same money in the ASPI.
  - `cash_flows` + `xirr` give the per-stock return.
- `web/api.py`: `ticker_payload(cfg, conn, symbol)`, built the same way as `build_payload`.
- `web/server.py`: `GET /api/ticker/<symbol>`. Check the symbol with
  `re.fullmatch(parsers.ndb.common.SYMBOL, s)` before using it, and return 404 for a symbol you never traded.

**Front end**

- Hash routing: `#/t/ABC.N0000` opens the page, `#/` goes back. Listen for `hashchange`.
- Make symbols clickable in `table()`, `network()` and `positionIndex()`.

**Tests**

- Every symbol's lifetime P&L adds up to `readouts.total_gain`.
- One buy, no sales, no dividends: XIRR equals the annualized price change.
- An unknown symbol returns 404, and `../` or `%2F` in the path is rejected.
