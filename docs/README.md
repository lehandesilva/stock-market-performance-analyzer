# Improving the analyzer

Ideas for what to build next. Each one explains the finance behind it as it goes, so you learn while you
build. For every idea: what it is, why it matters, how to compute it, and where it fits in the code.

| Doc | What's in it |
|---|---|
| [01 · Understanding your numbers](01-understanding-your-numbers.md) | What the dashboard already computes, and the handful of ideas everything else builds on |
| [02 · A page per stock](02-per-ticker-page.md) | Lifetime P&L, return vs the ASPI, lots, fees, dividends, risk and liquidity for one symbol |
| [03 · What if I'd sold on a date?](03-what-if-i-sold.md) | Exit value on any day, holding vs selling, "did I sell too early?", best and worst exits |
| [04 · Performance and risk](04-performance-and-risk.md) | Time-weighted return, drawdown, Sharpe, beta on a thinly traded market, better benchmarks, inflation and USD |
| [05 · Your trading habits](05-your-trading-habits.md) | Selling winners too early, round-trip stats, what fees cost you, whether trading beat holding |
| [06 · Data and engineering](06-data-and-engineering.md) | Data the CSE API already sends that the app throws away, deeper price history, adjusted prices, tests |

Each idea has a level: **Foundation**, **Intermediate**, **Advanced** or **Expert**. Foundation items are cheap
and fix numbers you could misread. Expert items need more data and care, and they give the deepest insight.

## Found while reviewing the code

I checked each of these by running the code, not just by reading it.

1. **The "As of" date mixes today's prices into the past.** If you pick 2022-01-03, you see the shares you
   held that day, but valued at *today's* prices, and the return runs up to today. Shares you've since sold
   show as unpriced. So the dashboard can't yet answer "what if I'd sold then". Fixing this
   ([03 §1](03-what-if-i-sold.md#1-fix-first-make-as-of-mean-as-of)) is the first step.
2. **The ASPI comparison line can go below zero.** If you sell a big winner, "the same money in the ASPI"
   can turn negative. In a test, buying for 1,000 and selling for 3,000 with the index flat put the line at
   −2,000. This is a known flaw of the method. [04 §5](04-performance-and-risk.md#5-better-benchmarks) shows
   two standard fixes.
3. **Unrealized P&L ignores the cost of selling.** Selling costs about 1.13% of the sale value in fees, so
   "what I'd get if I sold today" is that much lower than the dashboard says.
   [01 §2](01-understanding-your-numbers.md#2-fees-the-23-hurdle).
4. **The benchmark leaves out dividends.** The ASPI only tracks prices. The CSE also calculates a version
   that adds dividends back in (ASTRI). The README already mentions this bias.
5. **Price history only goes back about a year**, because that's all the CSE API gives, plus every day
   you've run `smpa prices` since. A "what if I'd sold" date older than that has no price. A missed day can't
   be filled in once it's more than a year old, so start a daily timer
   ([06 §2](06-data-and-engineering.md#2-grow-price-history-every-day)).
6. **Dividends entered `per_share` are counted before withholding tax**, so your income is overstated
   unless you enter the `amount` that actually reached your bank.
7. **Useful data gets thrown away.** The CSE responses the app already asks for include per-stock betas,
   52-week high/low, daily volume and turnover. Nothing stores them ([06 §3](06-data-and-engineering.md#3-keep-what-the-cse-api-already-sends)).

## Roadmap

| # | Idea | Level | Effort | Builds on | Doc |
|---|---|---|---|---|---|
| 1 | Daily `smpa prices` timer, so history grows | Foundation | S | — | [06](06-data-and-engineering.md#2-grow-price-history-every-day) |
| 2 | Point-in-time "As of": value a past date at that day's prices | Foundation | S | — | [03](03-what-if-i-sold.md#1-fix-first-make-as-of-mean-as-of) |
| 3 | Liquidation value and break-even price | Foundation | S | — | [01](01-understanding-your-numbers.md#2-fees-the-23-hurdle), [02](02-per-ticker-page.md) |
| 4 | Exit value if sold on any date, for one stock or everything | Intermediate | M | 2 | [03](03-what-if-i-sold.md#2-exit-value-on-a-date) |
| 5 | A page per stock | Intermediate | M–L | 3 | [02](02-per-ticker-page.md) |
| 6 | Store beta, 52-week range, volume and turnover from the CSE | Intermediate | S | — | [06](06-data-and-engineering.md#3-keep-what-the-cse-api-already-sends) |
| 7 | Time-weighted return, volatility, drawdown | Advanced | M | 1 | [04](04-performance-and-risk.md#1-time-weighted-return) |
| 8 | Holding vs selling, and "did I sell too early?" | Advanced | M | 4 | [03](03-what-if-i-sold.md#3-was-holding-better-than-selling) |
| 9 | Total-return benchmark (ASTRI), KS-PME, direct alpha | Advanced | M | index data | [04](04-performance-and-risk.md#5-better-benchmarks) |
| 10 | Disposition effect, round-trip stats, fee drag | Advanced | M | 4 | [05](05-your-trading-habits.md) |
| 11 | Inflation-adjusted (CCPI) and USD returns | Advanced | M | CPI and FX data | [04](04-performance-and-risk.md#6-real-and-dollar-returns) |
| 12 | Beta corrected for thin trading, VaR, risk contributions | Expert | M | 7 | [04](04-performance-and-risk.md#3-risk) |
| 13 | Return attribution against sector indices | Expert | L | 6, a symbol → sector map | [04](04-performance-and-risk.md#7-attribution-where-the-return-came-from) |
| 14 | Adjusted price series, a lot engine, invariant tests | Expert | M | — | [06](06-data-and-engineering.md) |

Effort: S is an evening, M a weekend, L more than that.

**Suggested order:** do 1 first, today, because missed days can't be recovered. Then 2 → 3 → 4 → 5, which
answers your two questions. After that 7 and 8, then whatever interests you most.
