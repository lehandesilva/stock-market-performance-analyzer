# 04 · Performance and risk

The dashboard tells you *how much* you made. This doc covers *how well*: compared with what, and for how much
risk. Most of it needs the daily value series that `analysis.value_history` already builds.

## 1. Time-weighted return

**Advanced.** TWR measures your picks with the effect of *when* money went in removed
([01 §3](01-understanding-your-numbers.md#3-three-kinds-of-return)). It's the number to set beside an index
or a fund. The CFA Institute's performance standards (GIPS) require it when comparing managers, for exactly
that reason.

For each day with a valuation:

```
r_t = (V_t + money out_t) / (V_t-1 + money in_t) − 1        growth_T = Π (1 + r_t)
```

`money in` is the cost of the day's buys. They're counted at the start of the day: you bought during the day
at the trade price, so the move from that price to the close is that day's return. `money out` is sale
proceeds and dividends, counted at the end of the day. Tested:

```python
def twr_index(hist, flows):
    """Growth of 1 rupee kept in your share picks, chain-linked daily. `flows` as from cash_flows()."""
    v = hist.dropna(subset=["value"]).set_index("date")["value"]
    put_in = pd.Series(0.0, index=v.index)
    taken_out = pd.Series(0.0, index=v.index)
    for when, amt in flows:  # buys < 0, sales and dividends > 0
        later = v.index[v.index >= when]
        if len(later):  # a flow on a day with no valuation joins the next valued day
            (put_in if amt < 0 else taken_out)[later[0]] += abs(amt)
    base = v.shift(1).fillna(0) + put_in
    r = ((v + taken_out) / base - 1)[base > 0]
    return (1 + r).cumprod()
```

- **Timing effect** = XIRR − annualized TWR, measured over the same window. If it's positive, your money tended
  to be in when prices rose. Show it as its own readout. It's one of the most revealing numbers here.
- **Gaps.** `value_history` leaves a day blank unless every holding has a recent price. The sketch above folds
  flows into the next valued day, so that day's return covers the whole gap. For monthly figures, or long gaps,
  use **Modified Dietz**:

  ```
  R = (V_end − V_start − F) / (V_start + Σ wᵢ Fᵢ)       wᵢ = days left in the period after flow i ÷ days in the period
  ```

  Example: start at 100,000, buy 50,000 on day 20 of a 30-day month, end at 160,000.
  R = (160,000 − 100,000 − 50,000) / (100,000 + 50,000 × 10/30) = **8.57%**.

## 2. Annualizing on the CSE calendar

**Intermediate.** The CSE's own one-year ASPI chart, up to 4 Nov 2026, has **240 trading days**. Every Poya day
and the other public holidays close the market. Formulas written for the US
assume 252 days, which overstates annual volatility by √(252/240) − 1 ≈ **2.5%**. Don't hard-code either
number. Count the `index_history` days in the last 365 and use that as N:

```
annual volatility = std(daily returns) × √N
```

## 3. Risk

**Advanced → Expert.** Calculate everything on the **TWR growth series**, never on market value. Buying more
shares makes market value jump, and that's not a gain.

| Metric | Formula | What it tells you |
|---|---|---|
| Volatility | std(r) × √N | Typical size of the swings |
| Downside deviation | √(mean(min(r, 0)²)) × √N | Swings that hurt only |
| **Max drawdown** | min over t of growth_t ÷ max(growth up to t) − 1 | Worst fall from a high: the number you actually feel |
| Drawdown duration | days from the peak until you're back above it | How long the pain lasts |
| Ulcer index | √(mean(drawdown²)) | Depth *and* length of drawdowns, as one number |
| Sharpe | (R − R_f) ÷ volatility | Return per unit of risk, above the risk-free rate |
| Sortino | (R − R_f) ÷ downside deviation | Same, counting only bad swings |
| Calmar | annual return ÷ \|max drawdown\| | Return per unit of worst pain |
| Beta, alpha, R² | regression of your daily r on the ASPI's | Market exposure; return the market doesn't explain |
| Tracking error | std(r − r_ASPI) × √N | How differently from the index you move |
| Information ratio | (R − R_ASPI) ÷ tracking error | Excess return per unit of being different |
| VaR / Expected shortfall | 5th percentile of daily r × value / average of the worst 5% | "On a bad day (1 in 20) I lose at least / on average…" |

```python
def max_drawdown(growth):
    dd = growth / growth.cummax() - 1
    trough = dd.idxmin()
    return dd.min(), growth[:trough].idxmax(), trough   # depth, peak date, trough date
```

For example, a series of 100 → 120 → 90 → 110 → 80 → 130 gives **−33.3%**: the peak is 120 and the trough is 80.
It recovers when it passes 120 again.

**The risk-free rate.** Use CBSL's weekly Treasury bill auction yields, taking the rate for each period. Sri Lankan
rates moved a great deal between 2021 and 2024, so one constant would distort Sharpe ratios badly. Store the
yields in `data/tbill.csv` (date, 364-day yield).

**Beta on a thinly traded market** (Expert). When a stock doesn't trade on a given day, its "close" is stale. Its
reaction to the market then shows up a day or more late, and an ordinary regression sees less co-movement than
there really is. Beta comes out **biased towards zero** (Scholes & Williams 1977). The standard fix, from
Dimson (1979), is to regress on yesterday's, today's and tomorrow's market returns and add up the slopes:

```python
def dimson_beta(stock: pd.Series, market: pd.Series, lags: int = 1) -> float:
    """Beta for a thinly traded stock: slopes on lagged, same-day and leading market returns, summed."""
    df = pd.DataFrame({"s": stock, **{f"m{k}": market.shift(k) for k in range(-lags, lags + 1)}}).dropna()
    X = np.column_stack([np.ones(len(df)), df.drop(columns="s").to_numpy()])
    coef, *_ = np.linalg.lstsq(X, df["s"].to_numpy(), rcond=None)
    return float(coef[1:].sum())
```

Test: a simulated stock with a true beta of 1.2, half of whose reaction lands a day late, gave an ordinary beta
of **0.60**. The Dimson beta was **1.11**. Weekly returns are a simpler partial fix. Compare both with the CSE's
published `triASIBetaValue` ([02](02-per-ticker-page.md#beta)).

**VaR with care.** Daily returns have fat tails (2022 is in your data). VaR that assumes a normal distribution
understates the bad days, so use historical VaR, and only once you have a few years of history.

## 4. Concentration and diversification

**Intermediate → Advanced.**

```
HHI = Σ wᵢ²          effective number of holdings = 1 / HHI
```

Weights of 40/30/20/10 give HHI = 0.30, so **3.3 effective holdings**, not 4. Also show the top-1 and top-3 weights.

- **Correlation.** Build a correlation matrix of daily returns from `price_history` and report the average
  correlation between pairs. Ten banks are one bet, not ten.
- **Diversification ratio** = Σ wᵢσᵢ ÷ σ_portfolio. Above 1 means diversification is reducing your risk, and
  the higher, the more it helps.
- **Sectors.** `allSectors` returns the CSE's 22 S&P/CSE industry-group indices (for example, "S&P/CSE Energy
  Industry Group Index") with their levels. None of the API responses I checked say which sector a stock is in,
  so keep a small `data/sectors.csv` (symbol, sector). That one file unlocks sector weights, sector benchmarks
  and §7.

## 5. Better benchmarks

**Advanced.**

**Count dividends on both sides.** The ASPI is a price index. The CSE also calculates the **All Share Total
Return Index (ASTRI)**, which adds dividends back in. It isn't in the unofficial API's endpoint list, and
`chartData` only returned data for chartId 1 (the ASPI) when I tried ids 1–6. So either find the request the
CSE website makes for it (browser developer tools, Network tab), or type month-end levels into `index_history`
under their own name. For the S&P SL20, `snpData` returns today's level, so store it every day.

**Fix the negative line.** `analysis.benchmark` is a Long–Nickels PME. It buys index units with your buys and
sells units with your sales. If you sell a big winner, it has to sell more units than it owns. In a test (buy
1,000, sell for 3,000, index flat) the equivalent went to **−2,000**. Two standard fixes, both of which work with
any cash flows:

- **KS-PME** (Kaplan & Schoar 2005). Grow every flow to today at the index:

  ```
  KS-PME = (Σ money out_t × I_T/I_t + value today) ÷ (Σ money in_t × I_T/I_t)
  ```

  Above 1.0 means you beat the index. In the test above the index is flat, so every growth factor is 1 and
  KS-PME = 3,000 ÷ 1,000 = **3.0**: you tripled your money while the index did nothing. It can't go negative.
- **Direct alpha** (Gredil, Griffiths & Stucke 2014). Grow every flow to today at the index, then take the
  XIRR. The result is your **yearly return over the index**, which is easier to read than a ratio:

  ```python
  def direct_alpha(flows, final_value, end, level):
      """Yearly return over the index: every flow grown at the index to `end`, then XIRR."""
      at = lambda d: level[:d].iloc[-1]  # index level on or before d
      return xirr([(d, amt * at(end) / at(d)) for d, amt in flows] + [(end, final_value)])
  ```

  Tested: a portfolio that tracks the index exactly gives 0.0%, and one that beats it by 5% a year gives 4.99%.

**A risk-free benchmark too.** Run the same PME with a T-bill account instead of the index. The difference is
the extra return you actually earned for taking stock-market risk. If it's negative, the risk didn't pay.

## 6. Real and dollar returns

**Advanced.** Two facts from the Central Bank make this matter: CCPI inflation reached **69.8%** in September 2022,
and the rupee lost **44.8%** against the dollar that year.

- **Real XIRR.** Convert every cash flow and the final value into today's rupees, `flow_t × CPI_today / CPI_t`, then
  take the XIRR. That handles flows of different sizes correctly. Applying `(1 + r)/(1 + inflation) − 1` to the
  final answer is only an approximation.
- **USD XIRR.** `flow_t ÷ LKR-per-USD on day t`, and the same for the final value. This answers "did I beat keeping
  the money in dollars?"
- **Data.** CCPI is published monthly (Department of Census and Statistics, also on CBSL), and CBSL publishes daily
  exchange rates. Keep them as `data/ccpi.csv` (month, index) and `data/fx.csv` (date, LKR per USD), checked as
  strictly as `corporate_actions.csv` is.
- Show nominal, real and USD returns side by side. They can tell three different stories about the same years.

## 7. Attribution: where the return came from

**Expert.**

- **By stock.** Each stock's contribution is its gain over the period divided by the *portfolio's* Modified Dietz
  denominator. The contributions add up exactly to the portfolio's return. "Two stocks made all of it" is common,
  and worth knowing.
- **By decision (Brinson–Fachler).** Using the sector indices from §4 as the benchmark for each sector *s*:

  ```
  allocation_s = (w_you,s − w_index,s) × (R_index,s − R_index)      did you overweight sectors that did well?
  selection_s  = w_you,s × (R_you,s − R_index,s)                    did you pick well within each sector?
  ```

  You need the index's sector weights. Add up `marketCap` per stock from `tradeSummary` by sector, using
  `data/sectors.csv`. This is the most demanding item here, and also the one that tells you most clearly whether
  you have skill or just sector exposure.

## 8. Looking forward, carefully

**Expert.** A **block bootstrap** resamples runs of real daily returns from your history (blocks keep calm and
stormy stretches together) to show a *range* of one-year outcomes. Always present it as a range with its
assumptions, never as a forecast. CSE history contains both the 2022 crisis and the rally that took the ASPI up
42% in 2025. The future won't be a neat average of the two.

---

**Papers** (all short and readable, if you want to go further): Dimson (1979), *Risk measurement when shares are
subject to infrequent trading*; Scholes & Williams (1977), *Estimating betas from nonsynchronous data*; Kaplan &
Schoar (2005), *Private equity performance: returns, persistence, and capital flows*; Gredil, Griffiths & Stucke
(2014), *Benchmarking private equity: the direct alpha method*; Brinson & Fachler (1985), *Measuring non-US equity
portfolio performance*.
