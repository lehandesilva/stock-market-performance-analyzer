# Handover: the docs/ roadmap

For the next agent picking this up. The user asked to implement the ideas in [docs/README.md](README.md)
(roadmap items 1–14, plus the "Found while reviewing the code" list). **All of it is built.** This file says
where each piece lives, the conventions to keep, and ideas for after.

## Working agreement

- Work on the branch your session names, starting from the latest `main`. Push with `git push -u origin <branch>`.
  **Don't open a pull request** unless the user asks. (The second round of this work was on
  `claude/ecstatic-carson-ghk78d`; the first, `claude/youthful-curie-a2kv7z`, was merged as PR #6.)
- Commit messages: imperative subject; the body explains *why*, in the README's plain style. End every message
  with the attribution lines your session's system reminder gives. No model names in code, docs or commits.
- The user is on Ubuntu 24.04 (noble) and wants concise answers.
- One commit per roadmap item (or a coherent group), each with tests and the README updated. All **154 tests
  pass** (`pytest`, about 25 s).

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
an ASPI, hand-typed trades, dividends, `security_info`, sector indices and every optional `data/` file
(including `journal.csv`). It's for looking at pages, not checking numbers. Restart the server after Python
changes (static files are read on each request). Wait about 3 s after starting it before the first screenshot.
Playwright and Chromium are installed: screenshot `/`, `/#/performance`, `/#/habits` and
`/#/t/AAA.N0000?sold=2024-01-02` at 1440 px, in dark mode (`colorScheme: "dark"`) and at 390 px. Check
`document.documentElement.scrollWidth - clientWidth == 0`: a wide table not inside `.scroll-x` or
`.table-wrap` widens its whole grid column on a phone.

The CSE API (`www.cse.lk/api/…`) is reachable from the container. Real response shapes are in the tests
(`tests/test_prices.py`: `COMPANY_AAA`, `SNP`, `SECTORS`, `SUMMARY`, `ANNOUNCEMENTS`). Keep live requests to a
handful.

## Where everything lives

| # | Idea | Where |
|---|---|---|
| 1 | Daily `smpa prices` timer | `smpa timer [--remove]` → `src/smpa/timer.py` (systemd user units, 16:00 Asia/Colombo Mon–Fri) |
| 2 | Point-in-time "As of" | `web/api.py` `_dates`, `_valuation_prices`; `analysis.prices_on` |
| 3 | Liquidation value, break-even | `src/smpa/fees.py`; holdings columns, "If sold" and "Gain if sold" readouts |
| 4 | Exit value if sold on any date | `whatif.if_sold_on`; chart 03 "Gain if sold" mode; per-stock "If sold on" |
| 5 | A page per stock | `src/smpa/ticker.py`, `GET /api/ticker/<symbol>[?sold_on=]`, `static/ticker.js`, `#/t/SYMBOL` |
| 6 | Keep what the CSE API sends | `prices.py` (`Extras`, `parse_market`, `parse_info`, `parse_sectors`, `parse_announcements`); tables `daily_quotes`, `security_info`, `history_changes`, `announcements` |
| 7 | TWR, volatility, drawdown | `src/smpa/performance.py`, `web/report.py`, `GET /api/performance`, `static/views.js`, `#/performance`; TWR also on the overview (`api._twr`) |
| 8 | Holding vs selling, sold too early | Per stock: `whatif.holding_vs_selling`, `whatif.sales_review`, `whatif.exit_curve`. Portfolio: the overview's **Sales** tab (`whatif.sales_summary`). Trailing stop (doc 03 §5): `habits.trailing_stop` on the Habits page |
| 9 | ASTRI, KS-PME, direct alpha | Performance page (ASPI, S&P SL20, T-bills, `indices.csv`); chart 03's note and the overview's "vs the ASPI" cell (`analysis.benchmark` returns them) |
| 10 | Trading habits (doc 05) | `src/smpa/habits.py`, `GET /api/habits` (`web/report.habits_payload`), `static/habits.js`, `#/habits` |
| 11 | CCPI and USD returns | `web/report._money`, `performance.converted_xirr`; `data/ccpi.csv`, `data/fx.csv` |
| 12 | Thin-trading beta, VaR, risk contributions | `performance.dimson_beta`, `value_at_risk`, `risk_contributions`, `concentration` |
| 13 | Attribution | `performance.attribution`, `performance.brinson`; `data/sectors.csv` |
| 14 | Adjusted prices, lots, invariant tests | `adjust.py` (`to_basis`, `total_return_closes`, `dividends_per_share`, `unrecorded_splits` flagged on the overview), `lots.py`, `tests/test_laws.py` (Hypothesis), `tests/fixtures/golden_payload.json` |

"Found while reviewing the code": all seven are fixed. #2 (the negative ASPI line): `analysis.benchmark` stops
the line on the first day it reaches zero (`stopped`), and chart 03's note shows direct alpha instead of the
line's XIRR.

Doc 05 §7 (cash left idle): statements' closing balances go into `balance_records` (per document, filled in
`parsers._store`) and `cash_balances` (one per period, `reconcile._balances`: the same period must agree;
statements starting the same day, like the mid-month 1st–15th one and the full month, must agree on the
opening balance and the longest is kept; any other overlap stops). Statements parsed before that are re-read once by `parse_all`. `habits.cash_idle`
gives the account return over the latest unbroken run of statements, and cash drag.

Doc 06 §8: `web/server._Cache` caches each page's JSON, keyed on `_fingerprint` (SQLite's file change counter
in the header, a hash of each `data/` CSV, today's date). `src/smpa/export.py` and `/export/yearly.csv`,
`/export/transactions.csv`, `/export/stock/<SYMBOL>.csv`. `src/smpa/alerts.py` (shown above the flags on the
overview, latest view only). `recorded_on` in `corporate_actions.csv` (`actions.py`; flagged as `CHG-xx` for 30
days).

## Conventions in the code

**Data (`db.py`)**: new columns go in `_NEW_COLUMNS` (added by `connect()`); new tables in `SCHEMA` with
`CREATE TABLE IF NOT EXISTS`. Index names in `index_history`: `ASPI`, `SPSL20`, `sector:<name>`.

**Analysis**: `analysis._events` / `_Ledger` is the one engine; `whatif._walk` yields the ledger and lots after
each event, and most new code builds on it. For prices across a split, use `adjust.actual_closes` (as traded)
for valuing ledger share counts, `adjust.adjusted_closes` or `total_return_closes` (today's basis) for
returns, and `adjust.to_basis` to put a window of closes on one day's basis. **Don't call `adjust.multiplier`
once per price day**: it filters the actions frame each time (that made the overview take 6.6 s once);
`habits._Closes.mult` caches it.

**Web**:
- `web/api.load_data()` reads everything once into a `Data`; `_plain()` makes nested payloads JSON-safe.
- Page payloads are registered in `server.VIEWS` (path → builder) and cached; **new static files must be added
  to `server.FILES`**; new pages go in `PAGES` in `app.js`, the nav in `index.html`, and a `<div id=…>`.
- Front end: ES modules, no dependencies. `lib.js` has formatting, tooltip, scales, `dataTable`, `box`,
  `amount`, `symLink`, `F` and `NUMERIC`. Labels are uppercased with letter-spacing, so avoid Greek letters.
- The golden payload changes whenever the overview payload does: regenerate it with
  `SMPA_UPDATE_GOLDEN=1 pytest tests/test_web.py -k golden` and check the diff is only what you meant.

**Tests**: `tests/conftest.py` has the docs' worked example (`abc` fixture) and `make_tx` / `make_actions`.
`tests/test_web.py` has the fixture database (`cfg`), a live server (`live`) and `_priced(cfg)` (40 days of
made-up closes). Tests reproduce the docs' numbers exactly, for example: lifetime 23,409.04; selling on D
better by 20,601.36; XIRR 72.5% / 16.6%; exit efficiency 66%; Modified Dietz 8.57%; HHI 0.30; KS-PME 3.0;
PGR 0.50 / PLR 0.00; Wilson 7 of 12 → 32%–81%.

## Gotchas

- Strictness is the project's core rule: no doubtful numbers. A file mistake stops with its line number; an
  unknown price is left unpriced, never guessed; extras from the API are logged and skipped.
- Every performance figure covers only the days with a value for *every* holding (`value_history`), so with
  real data it usually starts when price history does (a year from the API, plus daily runs).
- `growth` from `twr_index` starts at 1 on the first valued day unless nothing was held going into it.
- The S&P SL20 and sector indices have no history endpoint: they only accumulate from daily runs.
- `fees.fee_rate` excludes `manual`, `rights issue` and `delisting` rows. Statement-only trades count.
- `price_history.fetched_at` is *last* fetched, not first known: the weekly re-download replaces it. So it
  can't reproduce what was known on a past day; only corporate actions have that (`recorded_on`).
- `approvedAnnouncement` has company names but no symbol, XD date or amount (as of Nov 2026). Alerts match
  names (`alerts.company_key`) and only suggest a `corporate_actions.csv` row.
- Exit efficiency is only defined for sales at a gain, and summarized by its median.
- `xirr` returns None outside −99.99% … +1,000,000% a year (a few days' move annualized can exceed that).
- Pandas 3 is installed. Build empty date-indexed series with `pd.DatetimeIndex([])`.

## Ideas for after

- **Reproduce a past day's dashboard** (`?known_on=`): filter corporate actions by `recorded_on`. Prices would
  need a first-seen date that the weekly re-download doesn't overwrite.
- **Dividends of stocks you didn't hold on the ex-date**: `total_return_closes` only reinvests the ones you
  recorded. `getFinancialAnnouncement` (has symbols, links to PDFs) wasn't explored for this.
- **Trailing stop variants**: a stop on top of your own sales (the earlier of the two) rather than instead of
  them; reinvesting early exits at the T-bill rate so both sides compare over the same days.
- **A stacked bar of fees by type per year** on the Habits page (the table has the numbers).
- **Precision**: `reconcile` stores floats. Keep cents exact end to end if you ever produce tax figures.
