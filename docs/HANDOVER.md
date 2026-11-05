# Handover: building the docs/ roadmap

For the next agent picking this up. The user asked to "check README.md in the doc folder and start working on
all": implement the ideas in [docs/README.md](README.md) (roadmap items 1–14, plus the "Found while reviewing the
code" list). This file says what is done, where it lives, what is left, and the conventions to keep.

## Working agreement

- Branch: **`claude/youthful-curie-a2kv7z`** (pushed; 7 commits on top of `origin/main` 4eba052). Keep committing
  there and push with `git push -u origin claude/youthful-curie-a2kv7z`. **Don't open a pull request** unless the
  user asks.
- Commit messages: imperative subject; the body explains *why*, in the README's plain style. End every message
  with the attribution lines your session's system reminder gives (this session used
  `Co-Authored-By: …` and `Claude-Session: …`). No model names in code, docs or commits.
- The user is on Ubuntu 24.04 (noble) and wants concise answers.
- One commit per roadmap item (or a coherent group), each with tests and the README updated. All **125 tests
  pass** (`pytest`, about 11 s).

## Setup and how to look at the UI

```bash
python3 -m venv .venv --system-site-packages && . .venv/bin/activate && pip install -e '.[dev]'
```

```bash
pytest -q
```

```bash
python scripts/make_demo.py /tmp/smpa-demo && smpa -c /tmp/smpa-demo/config.toml dashboard --no-browser --port 8765
```

`scripts/make_demo.py` builds a demo folder: the test fixtures' documents, made-up prices (with thin trading),
an ASPI, hand-typed trades, dividends, `security_info`, sector indices and every optional `data/` file. It's for
looking at pages, not checking numbers. Restart the server after Python changes (static files are read on each
request). Playwright and Chromium are installed: screenshot `http://localhost:8765/`, `/#/performance` and
`/#/t/AAA.N0000?sold=2024-01-02` at 1440 px, in dark mode (`colorScheme: "dark"`) and at 390 px. Also check
`document.documentElement.scrollWidth - clientWidth == 0` (no sideways scroll on a phone).

The CSE API (`www.cse.lk/api/…`) is reachable from the container. Real response shapes are in the tests
(`tests/test_prices.py`: `COMPANY_AAA`, `SNP`, `SECTORS`, `SUMMARY`). Keep live requests to a handful.

## Status of the roadmap

| # | Idea | Status | Where |
|---|---|---|---|
| 1 | Daily `smpa prices` timer | **Done** | `smpa timer [--remove]` → `src/smpa/timer.py` (systemd user units, 16:00 Asia/Colombo Mon–Fri) |
| 2 | Point-in-time "As of" | **Done** | `web/api.py` `_dates`, `_valuation_prices`; `analysis.prices_on` |
| 3 | Liquidation value, break-even | **Done** | `src/smpa/fees.py`; holdings columns, "If sold" and "Gain if sold" readouts |
| 4 | Exit value if sold on any date | **Done** | `whatif.if_sold_on`; chart 03 "Gain if sold" mode; per-stock "If sold on" |
| 5 | A page per stock | **Done** | `src/smpa/ticker.py`, `GET /api/ticker/<symbol>[?sold_on=]`, `static/ticker.js`, `#/t/SYMBOL` |
| 6 | Keep what the CSE API sends | **Done** | `prices.py` (`Extras`, `parse_market`, `parse_info`, `parse_sectors`); tables `daily_quotes`, `security_info`, `history_changes` |
| 7 | TWR, volatility, drawdown | **Done** | `src/smpa/performance.py`, `web/report.py`, `GET /api/performance`, `static/views.js`, `#/performance` |
| 8 | Holding vs selling, sold too early | **Partly** | Per stock: `whatif.holding_vs_selling`, `whatif.sales_review` (+1/3/6/12 months, exit efficiency), MFE/MAE in `whatif.exit_curve`. **Left:** the portfolio-wide view (below) |
| 9 | ASTRI, KS-PME, direct alpha | **Partly** | Performance page: ASPI, S&P SL20, T-bills, `data/indices.csv` (e.g. ASTRI), KS-PME, direct alpha. **Left:** the overview's negative ASPI line (below) |
| 10 | Disposition effect, round trips, fee drag | **Not started** | Building blocks exist: `lots.trips_frame`, `extdata.load_journal`, fee columns in `trades` |
| 11 | CCPI and USD returns | **Done** | `web/report._money`, `performance.converted_xirr`; `data/ccpi.csv`, `data/fx.csv` |
| 12 | Thin-trading beta, VaR, risk contributions | **Done** | `performance.dimson_beta`, `value_at_risk`, `risk_contributions`, `concentration`; Performance page P5, stock page |
| 13 | Attribution | **Done** | `performance.attribution`, `performance.brinson`; Performance page P6; `data/sectors.csv` |
| 14 | Adjusted prices, lot engine, invariant tests | **Partly** | `src/smpa/adjust.py`, `src/smpa/lots.py` done. **Left:** property tests, golden payload, total-return series, wiring `unrecorded_splits` |

"Found while reviewing the code": #1, #3, #5, #6 (`[tax].dividend_withholding`) and #7 are fixed. #4 is fixed on
the Performance page via `indices.csv`. **#2 is still open on the overview** (see "Left to do").

## What was added, by layer

**Data (`db.py`)**: new tables `daily_quotes` (every traded stock per day), `security_info` (per holding per day:
52-week range, betas, market cap, `cse_id`), `history_changes` (stored closes the CSE later reported >0.5%
differently). New columns `price_history.volume`, `price_history.fetched_at` and `index_history.fetched_at`, added by
a generic `_NEW_COLUMNS` migration in `connect()`. Index names in `index_history`: `ASPI`, `SPSL20`,
`sector:<name>` (e.g. `sector:Banks`).

**Prices (`prices.py`)**:
- Daily runs date each `price_history` row by its last trade (Colombo day). A stock that hasn't traded today
  gets no row, so holidays add nothing.
- `update_prices` also keeps the extras, and extras never block your own prices.
- `update_history` stores volume (chart field `q`) and compares every re-downloaded close with the stored one.
- Live API facts:
  - `allSectors` also returns the ASPI and the S&P SL20 as rows; they have `indexCode: null` and are skipped.
  - `approvedAnnouncement` has company names but no symbol, XD date or amount.

**Analysis modules (new)**:
- `fees.py`: `fee_rate(tx, day, fallback)` is the median fees ÷ gross of the last 5 broker trades on or before
  `day`, within 180 days of the latest of them (1.12% before mid-2025, 1.132% after). Also `fee_rates`,
  `liquidation`, `break_even`. Config `[fees].rate` is the fallback.
- `lots.py`: FIFO `Lots` fed by `analysis._events` alongside `_Ledger`; `replay()` checks that lots equal the
  ledger's share count after every event. Also `open_lots`, `average_age`, `trips_frame`.
- `whatif.py`:
  - `if_sold_on` (doc 03 §2)
  - `holding_vs_selling` (cohort, §3, with an index-grown fair version)
  - `sales_review` (§4/§5)
  - `exit_curve` (gain if sold each day)
  - `_walk` (ledger + lots after each event)
- `adjust.py`: whether the CSE's history is adjusted for a split isn't known, so `basis()` looks for the ~1/k
  jump at each ex-date. `actual_closes` / `actual_history` give closes as traded: **use these for valuing
  ledger share counts**. `adjusted_closes` gives today's basis: **use this for returns**. Also `multiplier`,
  `unrecorded_splits`.
- `performance.py`:
  - returns: `twr_index` (doc 04's sketch, fixed so flows before the first valued day don't collapse into one
    day), `modified_dietz`, `monthly_returns`
  - risk: `trading_days_per_year` (ASPI days in the last 365), `risk_table`, `value_at_risk`, `bootstrap`,
    `dimson_beta`, `regression`
  - benchmarks: `ks_pme`, `direct_alpha`, `compare_with_index` (starts where the index starts), `tbill_index`,
    `mean_rate`
  - `converted_xirr`, `concentration`, `risk_contributions`, `attribution`, `brinson`
  - helpers: `on_calendar`, `level_series`
- `extdata.py`: strict loaders with line numbers for `tbill.csv` (yield in percent), `ccpi.csv`, `fx.csv`,
  `indices.csv`, `sectors.csv` and `journal.csv`. Paths are on `Config`.
- `ticker.py`: `ticker_detail(...)` builds the whole per-stock page.

**Changes to existing code**:
- `analysis.value_history` now returns `realized` and `dividends` to date.
- `ACTION_LOG_COLUMNS` gained `held` (shares held going into the ex-date).
- `actions.load_actions(path, withholding)` adds a `tax` column, and `_apply_action` applies it to per-share
  dividends.

**Web**:
- `web/api.py`: `load_data()` returns a `Data` with everything read once, plus `index_named()`.
  `build_payload` uses it, and `_plain()` makes nested payloads JSON-safe.
- `web/report.py` has `performance_payload`.
- `web/server.py`:
  - GET routes go through `_get` (unexpected errors now answer 500 instead of dropping the connection)
  - page payloads are registered in `VIEWS` (path → builder)
  - **new static files must be added to `FILES`**
  - `/api/ticker/<symbol>` checks `SYMBOL` after `unquote`
- Front end (ES modules, no dependencies):
  - `lib.js`: shared formatting, tooltip, scales and axes, `dataTable`, `box`, `amount`, `symLink`, and the
    `F` and `NUMERIC` formatters
  - `app.js`: overview and hash routing (`#/`, `#/performance`, `#/t/SYM?sold=YYYY-MM-DD`); `PAGES` lists the
    extra pages
  - `ticker.js` and `views.js` are the two new pages
  - Labels are uppercased with letter-spacing, so avoid Greek letters ("β" renders as "B")

**Tests**: `tests/conftest.py` has the docs' worked example (`abc` fixture: ABC.N0000) and `make_tx` /
`make_actions` helpers. New test files are `test_fees.py`, `test_lots.py`, `test_whatif.py`, `test_ticker.py`,
`test_performance.py` and `test_timer.py`; `test_web.py` and `test_prices.py` were extended. They reproduce the
docs' numbers exactly, for example:
- lifetime 23,409.04; selling on D better by 20,601.36; XIRR 72.5% / 16.6%; exit efficiency 66%
- Modified Dietz 8.57%; HHI 0.30; KS-PME 3.0; direct alpha 0 / 5%

## Left to do, in a sensible order

1. **The overview's ASPI line (found #2, doc 04 §5).** `analysis.benchmark` (Long–Nickels PME) still goes
   negative after a big sale, and chart 03's header note shows its XIRR (the demo prints "ASPI −94.6% a year").
   Fix:
   - stop drawing the dashed line where it reaches ≤ 0, with a short note
   - make `valueNote()` in `app.js` show direct alpha over the same window (`performance.compare_with_index`
     with `hist` values; see how `web/report.py` calls it)
   - keep `benchmark()` for the line itself, and update `test_value_history_and_aspi_benchmark`
2. **Fill the overview's third readout row.** It has two cells ("If sold", "Gain if sold"). The plan was a
   time-weighted return cell and a vs-ASPI (direct alpha) cell, reusing `performance.twr_index` and
   `compare_with_index`.
3. **Item 8, portfolio-wide:**
   - a "Sales" tab on the overview records with `whatif.sales_review(tx, actions, actual_history(...),
     index_hist, today)` for all symbols (it already handles `symbol=None`), with averages over all sales
     ("judge from the average, never one trade", doc 03 §4)
   - optional: a trailing-stop experiment (doc 03 §5)
4. **Item 10, trading habits (doc 05).** A `habits.py`, `GET /api/habits` (add to `VIEWS`), a Habits page in
   `views.js` (add to `PAGES` in `app.js` and the nav in `index.html`; the link was removed until it exists).
   Contents:
   - disposition effect, using doc 05 §2's tested sketch
   - round-trip stats from `lots.trips_frame` (market sales only), with Wilson intervals and `n` beside every figure
   - fee drag and turnover per year, fees by type per year (fee columns in `trades`), fees per stock
   - trading alpha (frozen 1 January portfolio vs that year's TWR), switches, "never sold", buy timing
     (20 days vs the ASPI), averaging down vs adding to winners
   - reasons from `journal.csv` (`extdata.load_journal`), also shown on the stock page next to its trades
5. **Cash left idle (doc 05 §7).** `parsers/ndb/statement.py` checks each statement's closing balance but doesn't
   store it. Store it per period (new table, filled in `parsers._store`, rebuilt by `reconcile`), then an
   account-level return (deposits and withdrawals from `cash_movements` as flows), and cash drag.
6. **Item 14, the rest (doc 06 §5, §7):**
   - flag `adjust.unrecorded_splits` for each held symbol in `build_payload`'s flags
   - a total-return series (dividend factor 1 − D/P)
   - Hypothesis property tests: add `hypothesis` to the `dev` extras; laws 1–7 in doc 06 §7, several of which
     already have example tests
   - a golden payload JSON for the fixtures
7. **Smaller things (doc 06 §8):**
   - cache `build_payload` and the page payloads, keyed on file and DB modification times (the per-stock page
     replays the ledger several times)
   - CSV exports, including a yearly realized / dividends / fees summary
   - alerts: below break-even, a new 52-week low, a big daily move, and dividend announcements (match
     `approvedAnnouncement` company names to holdings; only *suggest* `corporate_actions.csv` rows)
   - `recorded_on` for corporate actions (bitemporal, doc 06 §4)
8. **Finally**, update the roadmap table in `docs/README.md` (it now points here) and the main README.

## Gotchas

- Strictness is the project's core rule: no doubtful numbers. A file mistake stops with its line number; an
  unknown price is left unpriced, never guessed; extras from the API are logged and skipped.
- Every performance figure covers only the days with a value for *every* holding (`value_history`), so with
  real data it usually starts when price history does (a year from the API, plus daily runs).
- `growth` from `twr_index` starts at 1 on the first valued day unless nothing was held going into it.
- The S&P SL20 and sector indices have no history endpoint. They only accumulate from daily runs, so their
  comparisons start later.
- `fees.fee_rate` excludes `manual`, `rights issue` and `delisting` rows. Statement-only trades count.
- Pandas 3 is installed. Build empty date-indexed series with `pd.DatetimeIndex([])`: an empty default index
  broke slicing twice.
