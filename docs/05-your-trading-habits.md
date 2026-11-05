# 05 · Your trading habits

Docs 02–04 judge the *portfolio*. This one judges the *decisions*. Costs and behaviour are the part of your
return you actually control. In a study of 66,465 US households (1991–96), the fifth that traded most earned
11.4% a year while the market made 17.9%, mostly because of costs (Barber & Odean 2000, *Trading is hazardous to
your wealth*). Everything here comes from data the app already has: trades, fees and price history.

## 1. Read these with care

You'll have tens of trades, not thousands, so every statistic needs its sample size shown next to it. Example:
7 winners out of 12 round trips is a 58% win rate, but the 95% range (Wilson interval) runs from **32% to 81%**.
That's consistent with skill and with luck. Show `n` beside every figure, and show the interval once `n` is small.

## 2. Selling winners too early

**Advanced.** The **disposition effect** is the tendency to sell winners too soon and hold losers too long
(Shefrin & Statman 1985). Odean (1998) measured it like this. On every day you sell something, each stock you
hold is either up or down (price against your average cost), and either sold that day (*realized*) or kept
(*paper*).

```
PGR = realized gains ÷ (realized gains + paper gains)       proportion of gains realized
PLR = realized losses ÷ (realized losses + paper losses)    proportion of losses realized
```

If PGR is greater than PLR, you're quicker to cash in a winner than to cut a loser. In Odean's sample, PGR was
about 0.15 and PLR about 0.10.

Example over two days of selling:

| Sell day | Up | Down |
|---|---|---|
| 1 | A (sold), B (kept) | C (kept) |
| 2 | D (sold), B (kept) | C (kept), E (kept) |
| **Total** | **2 realized, 2 paper** | **0 realized, 3 paper** |

PGR = 2 ÷ 4 = **0.50**, PLR = 0 ÷ 3 = **0.00**. A strong disposition effect. This sketch was tested on exactly
that example:

```python
def disposition(tx, actions, price_hist, max_age=7):
    """Odean's PGR and PLR: on the days you sold, what share of your winners, and of your losers, did you sell?"""
    px = price_hist.assign(date=pd.to_datetime(price_hist["date"])).pivot_table(
        index="date", columns="symbol", values="price").sort_index()
    market_sells = tx[(tx["side"] == "SELL") & ~tx["sources"].isin(NON_MARKET)]
    book, events, k = _Ledger(), _events(tx, actions), 0
    n = {"rg": 0, "pg": 0, "rl": 0, "pl": 0}  # realized/paper × gain/loss
    for day, sold in market_sells.groupby("trade_date"):
        while k < len(events) and (events[k][0] < day or (events[k][0] == day and events[k][1] == 0)):
            book.apply(events[k][1], events[k][2])  # positions at the start of the day
            k += 1
        got = dict(zip(sold["symbol"], sold["price"]))
        for sym, (q, cost) in book.held().items():
            if sym in got:
                price = got[sym]
            else:
                closes = px[sym][:day].dropna() if sym in px else pd.Series(dtype=float)
                if closes.empty or (day - closes.index[-1]).days > max_age:
                    continue  # no recent price: can't tell, leave it out
                price = closes.iloc[-1]
            n[("r" if sym in got else "p") + ("g" if price > cost / q else "l")] += 1
    ratio = lambda a, b: n[a] / (n[a] + n[b]) if n[a] + n[b] else None
    return ratio("rg", "pg"), ratio("rl", "pl"), n
```

Two follow-ups make the result more useful:

- **Holding time of winners vs losers.** From the round trips in §3, compare the median days held for trades that
  ended in a gain against those that ended in a loss. Holding losers much longer is the same bias seen from
  another angle.
- **What happened next.** [03 §4](03-what-if-i-sold.md#4-did-i-sell-too-early) tells you whether the winners you
  sold kept rising. If they did, the bias cost you money. Odean found it did for his investors. He also found
  the effect flips every December, when US investors sell losers to save tax. You may not have that motive, if
  gains on CSE shares stay untaxed ([01 §6](01-understanding-your-numbers.md#6-dividends-and-tax)).

## 3. Round trips

**Intermediate.** Match each sale to the buys it came from, oldest first (FIFO). Each matched piece is a *round
trip*: entry date and price, exit date and price, the fees on both legs, P&L, and days held. FIFO is the standard
way to compute trade-level statistics. The app's average-cost totals don't change
([01 §1](01-understanding-your-numbers.md#1-one-identity-ties-everything-together)).

| Statistic | Formula |
|---|---|
| Win rate | winning round trips ÷ all of them |
| Payoff ratio | average win ÷ \|average loss\| |
| Profit factor | total wins ÷ \|total losses\| (above 1 makes money) |
| Expectancy | win rate × average win − loss rate × \|average loss\|, the average result per trade |
| Median days held | for winners and for losers separately |

A low win rate with a high payoff ratio can be a good strategy: small losses and occasional big wins. A high win
rate with a payoff ratio well below 1 is the disposition effect's usual fingerprint.

## 4. What fees cost you

**Foundation → Intermediate.**

- **Fee drag** = fees paid in a year ÷ average portfolio value that year. Put it next to that year's return. For
  example, a 1.5% drag in a +12% year means fees took an eighth of the gain.
- **Turnover** = the smaller of the year's buys and sells ÷ average portfolio value, the same definition funds
  use. At 100% turnover you replaced the whole portfolio once, which cost about **2.26%** of its value (a full
  round trip at 1.132% each way).
- **Fees by type.** Brokerage is 0.64% of the 1.132% each way, so it's **57%** of every fee you pay. The
  `trades` table has each fee in its own column, so a stacked bar of fees by type per year takes a few lines.
- **Fees per stock.** Fees ÷ lifetime P&L ([02](02-per-ticker-page.md#the-numbers)). If that's high, the stock
  was traded more than its gains could pay for.

## 5. Did trading beat holding?

**Advanced.** Compare what you did against simply leaving the portfolio alone.

- **Frozen portfolio.** Take your holdings on 1 January each year and pretend you made no trades that year.
  Compare that portfolio's return (prices plus dividends) with your actual TWR for the year
  ([04 §1](04-performance-and-risk.md#1-time-weighted-return)). The difference is your **trading alpha**, the value
  your trades added or cost. Using TWR keeps new money from distorting it. This is the "own benchmark" approach
  used in research on individual investors.
- **Switches.** A sale followed within about 5 trading days by a buy is a switch from X to Y. For each one,
  compare Y's return with X's over the next 3, 6 and 12 months, minus the ~2.26% the switch cost in fees. The
  result reads "on average, your switches gained or lost X%". It's one of the most direct tests of whether your
  ideas beat your old ideas.
- **Never sold.** What would every share you ever sold be worth today? That's the sum of the "after the sale"
  values from [03 §4](03-what-if-i-sold.md#4-did-i-sell-too-early), taken to today. It's a blunt number, but a
  sobering one.

## 6. Timing

**Advanced.**

- **Market timing** is the XIRR − TWR gap ([04 §1](04-performance-and-risk.md#1-time-weighted-return)). Did you
  add money before rises or before falls?
- **Buy timing.** For each buy, look at the stock's return over the next 20 trading days minus the ASPI's. Average
  it across buys. Positive means you tend to buy just before a rise.
- **Adding to losers vs adding to winners.** Tag every buy of a stock you already held at a loss (price below
  average cost) as *averaging down*, and every buy of one held at a gain as *adding to a winner*. Track how each
  group turned out. Averaging down isn't wrong in itself, but you should know whether it works *for you*.

## 7. Cash left idle

**Intermediate.** The return on the dashboard ignores cash sitting at the broker (README, "Return"). That cash
still had a cost: it could have been in a T-bill. An **account-level return** fixes this:

- Treat deposits and withdrawals (`cash_movements`) as the cash flows, and value the account as shares plus the
  broker cash balance.
- The statement parser already checks every statement's closing balance (`parsers/ndb/statement.py`) but doesn't
  store it. Saving it per statement period gives you the cash series.
- **Cash drag** = return on shares only − account return. It shows how much idle cash cost you.

## 8. A trading journal

**Foundation, and the one that teaches you most.** Add an optional `data/journal.csv` (contract number, reason,
target, stop) and write one line when you buy. The per-stock page shows it next to the outcome. After a year you
can group trades by reason ("results play", "dividend", "tip from a friend"…) and see which reasons actually
make you money. No statistic can tell you that without the reasons written down at the time.
