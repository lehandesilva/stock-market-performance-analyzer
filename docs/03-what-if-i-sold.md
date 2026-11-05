# 03 · What if I'd sold on a date?

"How much would I have gained if I'd sold on <date>?" is really four different questions:

| Question | Section |
|---|---|
| If I'd sold everything, or one stock, on day D, what would my total gain have been? | [§2](#2-exit-value-on-a-date) |
| Was keeping the shares after D better than selling them on D? | [§3](#3-was-holding-better-than-selling) |
| Did my actual sales come too early? | [§4](#4-did-i-sell-too-early) |
| Which day would have been the best, or worst, to sell? | [§5](#5-best-and-worst-exits-in-hindsight) |

All four need the price on the day in question, so the first step is fixing "As of".

## 1. Fix first: make "As of" mean as of

**Foundation.** I ran the dashboard code on the test fixtures with an as-of date of 2022-01-03. Here's what it does today:

- AAA was valued at the **latest** price (100), even though `price_history` had 14 for that day.
- BBB showed as unpriced. The `prices` table only holds stocks you own *today*, because
  `prices.update_prices` asks `held_symbols` for current holdings.
- The annual return ran 2,057 days, up to when prices were fetched, instead of stopping at 2022-01-03.
- You can't pick a date after your last trade or corporate action. `build_payload` clamps the date there, and
  the date input's `max` is `meta.last_date`.

The cause is in `build_payload` (`src/smpa/web/api.py:58`). It prices everything with `load_prices`, which
returns the latest prices, and it sets `valued` (`api.py:84`) to whichever is later, the as-of date or the
fetch date.

**Fix:** for a past date, take prices from `price_history` and value the portfolio on that date.

```python
def prices_on(conn, day):
    """Each symbol's last close on or before `day`, unless it is more than STALE_DAYS old."""
    return pd.read_sql_query(
        """SELECT p.symbol, p.price, p.date FROM price_history p
           JOIN (SELECT symbol, MAX(date) AS date FROM price_history WHERE date <= :day GROUP BY symbol) last
             ON last.symbol = p.symbol AND last.date = p.date
           WHERE julianday(:day) - julianday(p.date) <= :stale""",
        conn, params={"day": pd.Timestamp(day).strftime("%Y-%m-%d"), "stale": STALE_DAYS})
```

- Allow dates up to the newest `price_history` date, not just up to the last trade.
- For a past date, use `prices_on(conn, when)` and set `valued = when`.
- Leave `data/prices.csv` overrides out of past views, because they're today's prices.
- Show each holding's price date in its tooltip, and "≈" when anything is unpriced, as the readouts already do.

Once this is done, the dashboard itself answers "what was it all worth on that day, and what had I made by then?"

## 2. Exit value on a date

**Intermediate.**

```
proceeds = shares held at the close of D × close on D × (1 − f)
gain     = proceeds − cost basis on D
lifetime = gain + realized P&L up to D + dividends up to D
```

This replays the ledger up to D. It was tested against the example below:

```python
def if_sold_on(tx, actions, price_hist, day, fee_rate):
    """Every holding as if sold at the close on `day`, plus what was realized and received up to then."""
    day = pd.Timestamp(day)
    book = _Ledger()
    for when, kind, r in _events(tx, actions):
        if when > day:
            break
        book.apply(kind, r)  # all trades and actions up to and including `day`
    ph = price_hist[price_hist["date"] <= day].sort_values("date").groupby("symbol").last()
    rows = []
    for sym, (qty, cost) in book.held().items():
        px = ph.loc[sym] if sym in ph.index else None
        if px is None or (day - px["date"]).days > STALE_DAYS:
            rows.append({"symbol": sym, "quantity": qty, "cost": cost, "price": None})  # unknown, not guessed
            continue
        gross = qty * px["price"]
        fees = gross * fee_rate
        rows.append({"symbol": sym, "quantity": qty, "cost": cost, "price": px["price"], "price_date": px["date"],
                     "net": gross - fees, "gain": gross - fees - cost})
    realized = sum(x["pnl"] for x in book.realized)
    income = sum(e["amount"] for e in book.log if e["action"] == "DIVIDEND")
    return pd.DataFrame(rows), realized, income
```

(`price_hist` needs its `date` column as timestamps: `pd.to_datetime(...)`.)

### The worked example

ABC.N0000, a made-up stock. Fees are 1.12% on every trade to keep the numbers round.

| Date | Event | Cash |
|---|---|---|
| 2024-02-01 | Buy 1,000 @ 40.00 | −40,448.00 (448.00 fees) |
| 2024-08-01 | Buy 500 @ 60.00 | −30,336.00 (336.00 fees) |
| 2024-11-01 | Dividend | +3,000.00 |
| **2025-03-03** | **D: closes at 75.00** | |
| 2025-06-02 | Sell 600 @ 68.00 | +40,343.04 (456.96 fees) |
| 2025-11-03 | Dividend on the 900 left | +1,350.00 |
| 2026-11-05 | Today: closes at 55.00, 900 held | |

Average cost is 70,784.00 ÷ 1,500 = 47.19.

| | If sold on D | What actually happened |
|---|---|---|
| Shares sold | 1,500 @ 75.00 → 111,240.00 after 1,260.00 fees | 600 for 40,343.04; 900 still held |
| Gain on the shares | 111,240.00 − 70,784.00 = **40,456.00** (+57.2%) | realized 12,029.44 + unrealized 7,029.60 |
| Dividends | 3,000.00 | 4,350.00 |
| **Lifetime gain** | **43,456.00** | **23,409.04** on the dashboard; **22,854.64** after fees to sell the 900 |
| XIRR | 72.5% a year (over 13 months) | 16.6% a year (after selling fees) |

Selling everything on D would have left you **20,601.36** better off.

**The whole portfolio, every day.** `value_history` already returns each day's `value` and `cost`. It's built
on the same `_Ledger`, which keeps `realized` and `log`, so add realized-to-date and dividends-to-date columns
and you get

```
value × (1 − f) − cost + realized to date + dividends to date
```

for every day. That's a "gain if I'd cashed out that day" line you can add to chart 03.

## 3. Was holding better than selling?

**Advanced.** §2 compares two lifetime totals. If you bought more after D, that comparison mixes in new money.
The clean version follows **the exact shares you held on D** (a *cohort*):

1. The cohort is the shares held at the close of D.
2. Walk through every event after D:
   - **Sale of n shares.** Cohort shares go first, because they're the oldest (FIFO).
     `cohort proceeds += net × (shares taken from the cohort ÷ n)`.
   - **Split or bonus.** Scale the cohort by the same ratio.
   - **Dividend.** `cohort income += per_share × cohort shares`. If only the amount is known, use
     `amount × cohort shares ÷ shares held`.
   - **Delisting.** The cohort is bought out at the offer price.
3. Today: `cohort value = remaining cohort shares × price × (1 − f)`.

```
holding was worth = cohort proceeds + cohort dividends + cohort value today
selling was worth = shares on D × close on D × (1 − f)
```

In the example: 40,343.04 + 1,350.00 + 48,945.60 = **90,638.64**, against **111,240.00** from selling. Holding cost
you 20,601.36. That's the same as §2 here only because you bought nothing after D.

**Making it fair (time value).** The two paths get their cash at different times. Money from selling on D
could have been invested meanwhile. Grow the "sell on D" proceeds at the ASPI (better: ASTRI, or the T-bill
rate) from D to today. Grow the holding path's cash (sale proceeds, dividends) the same way from the day each
arrived. This is the PME idea again ([04 §5](04-performance-and-risk.md#5-better-benchmarks)). The answer then
reads: *"selling ABC on D and putting the money in the index would have left you X better or worse off
today"*. That's the honest version of the question.

## 4. Did I sell too early?

**Advanced.** For every market sale (not rights or buyouts), check the price 1, 3, 6 and 12 months later, and
the ASPI over the same windows:

```
after-sale return = close at t+k ÷ sale price − 1
vs the market     = (1 + after-sale return) ÷ (1 + ASPI return over the same window) − 1
left on the table = shares sold × (close at t+k − sale price)        (negative: a loss you avoided)
```

Example: you sold 600 ABC at 68.00 on 2025-06-02. Say that a year later it closed at 58.00 while the ASPI rose 12%.
Then the after-sale return is −14.7%, it's −23.8% against the market, and the sale avoided 6,000.00 of losses. Good sale.

Judge from the **average over all your sales**, never from one trade. Odean (1998) found that the winners
individual investors sold went on to beat the losers they kept. That's the *disposition effect*, and
[05 §2](05-your-trading-habits.md#2-selling-winners-too-early) measures whether you do it.

You need prices for stocks you no longer hold. `update_history` already downloads a stock's history for a year
after you last held it, so the +12-month window is covered as long as you keep running `smpa prices`.

## 5. Best and worst exits, in hindsight

**Advanced.** For each day you held a stock, work out the gain if sold that day (§2 for one stock). That gives a
curve, which is chart [B] on the per-stock page. On it, mark:

- **MFE** (maximum favourable excursion): the best point, the most you could have made. **MAE** (maximum
  adverse excursion): the worst point, the deepest you were under water. Both terms come from trade analysis.
- **Exit efficiency** for each sale: `(sale price − average cost) ÷ (highest close while held − average cost)`.
  In the example, if the highest close was 78.50, that's (68.00 − 47.19) ÷ (78.50 − 47.19) = **66%** of the
  best possible move.

**Health warning: hindsight.** Nobody can know the top in advance. "I'd have sold at the peak" is not a strategy.
Use these numbers to spot patterns, such as "I give back most of my gains" or "my losers fall much further
before I sell", not to beat yourself up.

What you *can* test is a **rule**. For example, a trailing stop: sell when the price closes X% below its
highest close since you bought. Run it over your own history, fees included, and compare it with what you
actually did. That turns hindsight into a real experiment.

## 6. Fees for a sale that didn't happen

Use the rate in force on D. The clearing fee arrived in 2025 ([01 §2](01-understanding-your-numbers.md#2-fees-the-23-hurdle)).
Take the median `fees ÷ gross` of your last few broker trades on or before D. If D is before your first trade,
use your first few. Leave out manual rows and rights or buyout rows (`sources` is `manual`, `rights issue` or
`delisting`). Statement-only trades are fine: their fee *total* is exact even though the breakdown is unknown.
Per-line rounding to cents makes small trades look slightly more expensive (1,520.00 gross pays 17.20, which
is 1.1316%), which is why the median beats any single trade. If you have no trades at all, fall back to a
setting such as `[fees].rate = 0.01132`.

## 7. Limits of the data

- **Depth.** `price_history` holds a year from the API, plus every day you've run `smpa prices`. Before that,
  the only prices you have are your own trade prices: exact, but only on the days you traded. Don't fill the
  gaps by drawing lines between them. Show "no price", or the last trade price you know with a clear label.
- **Staleness.** A thinly traded stock's "close" can be days old. `STALE_DAYS` (31) decides when it still
  counts. Always show the price's date.
- **Could you really have sold at that price?** The close is the price of the last trade, which may have been
  a few shares. For a big position in a thin stock, check that day's volume
  ([02 Liquidity](02-per-ticker-page.md#liquidity)).
- **Splits.** If the CSE's history isn't adjusted for splits, any what-if across a split date is off by the split
  ratio. [06 §5](06-data-and-engineering.md#5-adjusted-prices) shows how to check this and how to adjust.

## 8. Where it goes in the UI

- **Per-stock page ([02](02-per-ticker-page.md)):** an "If sold on [date]" picker showing the §2 and §3 numbers,
  and chart [B] with the §5 curve. Endpoint: `GET /api/ticker/<symbol>?sold_on=YYYY-MM-DD`, with the date checked
  the same way `as_of` is in `web/server.py`.
- **Dashboard:** the fixed "As of" (§1), and the cash-out line on chart 03 (§2).
- **Sales table:** one row per sale, with +1m, +3m and +12m columns (§4).
