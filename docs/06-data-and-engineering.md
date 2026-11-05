# 06 · Data and engineering

The plumbing the other docs depend on. The theme is the README's own rule, "no doubtful numbers", applied to
prices and history as strictly as it already is to documents.

## 1. What you have, what's missing

| Data | Now | Where it would come from | Needed for |
|---|---|---|---|
| Trades with every fee | ✓ | Broker documents | Everything |
| Dividends, splits, bonuses, rights | ✓ by hand | `data/corporate_actions.csv` | Everything |
| Latest prices | ✓ current holdings only | `tradeSummary`, `companyInfoSummery` | Valuation |
| Daily closes | ✓ about a year, plus days you ran it | `companyChartDataByStock`, daily runs | [03](03-what-if-i-sold.md), [04](04-performance-and-risk.md) |
| ASPI daily | ✓ | `chartData`, `aspiData` | Benchmark |
| Open, high, low, volume, turnover | ✗ sent, then dropped | `tradeSummary` | Liquidity, "could I have sold?" |
| Beta, 52-week range, market cap | ✗ sent, then dropped | `companyInfoSummery` | [02](02-per-ticker-page.md) |
| S&P SL20, 22 sector indices | ✗ | `snpData`, `allSectors` (today's levels) | Benchmarks, attribution |
| ASTRI (total return) | ✗ | Not in the documented API ([04 §5](04-performance-and-risk.md#5-better-benchmarks)) | Fair benchmark |
| Symbol → sector | ✗ | Not in any response checked: a small CSV | Sectors, attribution |
| Cash at the broker | ✗ checked, not stored | Statement closing balances | Account return ([05 §7](05-your-trading-habits.md#7-cash-left-idle)) |
| CCPI, LKR/USD, T-bill yields | ✗ | CBSL / Department of Census and Statistics: small CSVs | Real and USD returns, Sharpe |

## 2. Grow price history every day

**Foundation, and do it first.** The API gives at most a year of daily closes. Any day you didn't capture is gone
once it's more than a year old, and "what if I'd sold on <date>" can't work without it. Every `smpa prices` run
stores one close per holding plus the ASPI. The weekly full-year download fills any missed days inside the last
year.

Run `smpa prices`, not `smpa run`: while your Google app is in Testing mode the Gmail token expires every 7 days
and needs a browser. A systemd user timer on Ubuntu:

`~/.config/systemd/user/smpa-prices.service`

```ini
[Unit]
Description=smpa: store today's CSE prices

[Service]
Type=oneshot
WorkingDirectory=%h/stock-market-performance-analyzer
ExecStart=%h/stock-market-performance-analyzer/.venv/bin/smpa prices
```

`~/.config/systemd/user/smpa-prices.timer`. The regular session closes at 2:30 pm Colombo time, and
`Persistent=true` catches up after the machine was off:

```ini
[Unit]
Description=Store CSE prices after the market closes

[Timer]
OnCalendar=Mon..Fri 16:00 Asia/Colombo
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
```

```bash
systemctl --user enable --now smpa-prices.timer
```

To keep it running while you're logged out:

```bash
sudo loginctl enable-linger "$USER"
```

Check it with `systemctl --user list-timers` and `journalctl --user -u smpa-prices`.

## 3. Keep what the CSE API already sends

**Intermediate.** Responses checked on 5 Nov 2026 contain much more than the app keeps:

| Endpoint | Useful fields |
|---|---|
| `companyInfoSummery` → `reqSymbolInfo` | `p12HiPrice`, `p12LowPrice` (52-week range); `ytd`/`mtd`/`wtd` highs and lows; `tdyShareVolume`, `p12ShareVolume`; `tdyTurnover`, `ytdTurnover`, `p12Turnover`; `marketCap`, `quantityIssued`, `isin`, `previousClose` |
| `companyInfoSummery` → `reqSymbolBetaInfo` | `triASIBetaValue`, `betaValueSPSL`, `triASIBetaPeriod`, `quarter` |
| `tradeSummary` (every stock traded today) | `open`, `high`, `low`, `price`, `previousClose`, `sharevolume`, `tradevolume`, `turnover`, `marketCap`, `lastTradedTime` |
| `snpData` | S&P SL20 `value`, `highValue`, `lowValue`, `change`, `percentage`, `timestamp` |
| `allSectors` | 22 rows: `indexName` (e.g. "S&P/CSE Energy Industry Group Index"), `indexValue`, `change`, `percentage`, `sectorTurnoverToday` |

Suggested schema additions in `db.py`:

- `daily_quotes (symbol, date, open, high, low, close, share_volume, trades, turnover, market_cap)`, filled from
  the one `tradeSummary` request the app already makes. Store *every* stock in it, not just yours: about 265 rows
  a day is tiny, and market caps for all stocks give you index sector weights
  ([04 §7](04-performance-and-risk.md#7-attribution-where-the-return-came-from)).
- `security_info (symbol, fetched_on, name, isin, cse_id, market_cap, shares_issued, hi_52w, lo_52w,
  beta_tri_aspi, beta_spsl, beta_period, beta_quarter, turnover_ytd)`, one row per symbol per fetch.
- `index_history` gets the S&P SL20 (`SPSL20`) and the sector indices under their own names, stored daily.

Check these responses the way `prices.parse_trade_summary` checks today's: if a field you rely on disappears, say
so loudly. But treat the extras like the ASPI update: if one of them fails, log it and move on. It must never
block your own prices.

**Check adjustment before using highs and lows.** In one response, JKH's all-time high (`allHiPrice`) was 430.25
against a last price of 18.90. A gap like that could be a real fall or a split that isn't adjusted for. Check
which before showing any long-range high or low ([§5](#5-adjusted-prices)).

## 4. Point-in-time correctness

**Advanced.**

- **Rule:** a view of a past date uses only data *as of* that date: prices, fee rates, dividends. [03 §1](03-what-if-i-sold.md#1-fix-first-make-as-of-mean-as-of)
  fixes the main place where it doesn't.
- **Don't overwrite history silently.** `update_history` writes a year of closes with `INSERT OR REPLACE` every
  week. If the CSE ever starts adjusting its history for splits, or corrects a close, your stored prices change
  without anyone noticing. Before replacing a close, compare the two. If they differ by more than rounding
  (say 0.5%), log a warning naming the symbol and the dates. That's how you'd find out the CSE adjusts its data.
- **Know when you learned a fact** (Expert, also called *bitemporal* data). Store two dates per fact: when it was
  true, and when you recorded it. Add `fetched_at` to `price_history` and `index_history` (`prices` already has
  it), and `recorded_on` to corporate actions. Then you can reproduce what the dashboard showed on any past day,
  and explain why a number changed: "the 2024 return moved because you added a dividend on 3 March".

## 5. Adjusted prices

**Advanced.** The ledger already gets share counts right through splits and bonuses. But price *series* used for
charts, single-stock volatility and beta, and after-sale returns need to be continuous across those events. Build
adjustment factors from `corporate_actions.csv`, the way research databases such as CRSP do. For each event on
its ex-date, multiply every close **before** that date by:

| Event | Factor | Example |
|---|---|---|
| `SPLIT` new:old | old ÷ new | 2:1 → ½ |
| `BONUS` new:held | held ÷ (held + new) | 1:10 → 10/11 |
| `DIVIDEND` D a share, previous close P | 1 − D ÷ P | gives a *total-return* series |

**Is the CSE's history already adjusted?** Look at a known split inside your stored history. The README's example
is the Hemas 5:1 split on 2025-05-07; if you held HHL then and were running `smpa prices`, you have it. An
unadjusted series drops about 80% that day. `analysis._split_hint` already runs this ratio test on your trade
prices. Run it over `price_history` too, and you'd catch it automatically.

## 6. A lot engine

**Expert.** Doc 02 (FIFO lots), 03 (cohorts) and 05 (round trips) all need lot-level tracking, but `_Ledger`
keeps only `(quantity, cost)` per symbol. Add one `Lots` class, fed by the same `_events` replay:

- `buy(date, qty, cost)`, `sell(date, qty) → [(lot, qty taken)]`, `scale(new, old)` for splits and bonuses,
  `close(price)` for delistings.
- `_Ledger` stays the source of the official average-cost numbers. Lots are extra views built alongside it.
- Invariant, tested after every event: the lot quantities add up to the ledger's quantity.

## 7. Tests that check the maths

**Advanced.** The current tests check examples. Add tests that check *laws*, using property-based testing
([Hypothesis](https://hypothesis.readthedocs.io/), added to the `dev` extras), which generates random trade
sequences and looks for one that breaks the rule:

1. **Cash identity.** realized + unrealized + dividends = value + sales + dividends − buys, whenever no sale is
   bigger than the shares held ([01 §1](01-understanding-your-numbers.md#1-one-identity-ties-everything-together)).
2. Every symbol's lifetime P&L adds up to the portfolio's.
3. A single buy held to the end: TWR equals the price change, and XIRR equals its annualized version.
4. A portfolio that *is* the index: direct alpha is 0, KS-PME is 1, and PME shows no gap.
5. `as_of` = the last date gives the same payload as no `as_of`.
6. Lot quantities always match the ledger.
7. The fee rate worked out from the fixtures is 1.12% for 2025-03 and 1.132% for 2026-09.

Also add a **golden payload**: save `build_payload`'s output for the fixtures as JSON and fail on any difference.
A change you meant to make is then a one-line review, and an accidental one gets caught.

## 8. Smaller things

- **Precision.** The parsers use `Decimal` and check to the cent. `reconcile` then stores floats (`REAL`). That's
  fine for analysis, but if you ever produce tax figures, keep cents exact end to end.
- **Caching.** `build_payload` recomputes everything on every request, and per-stock pages will add more requests.
  Cache the result, keyed on the modification times of the database and the CSV files.
- **Exports.** A CSV per stock, and a yearly summary of realized P&L, dividends and fees, for tax time.
- **Alerts.** Price below break-even, a new 52-week low, a large daily move, an ex-date coming for a stock you
  hold. The unofficial API docs list `approvedAnnouncement` and `getFinancialAnnouncement`, which I didn't
  inspect. Whatever you find there, keep the app's rule: *suggest* rows for `corporate_actions.csv` for you to
  confirm. Never write them automatically.
