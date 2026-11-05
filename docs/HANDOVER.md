# Handover: building the docs/ roadmap

For the next agent picking this up. The user asked to implement the ideas in [docs/README.md](README.md) (roadmap
items 1–14, plus the "Found while reviewing the code" list), then to "continue from where it was left off". This
file says what is done, where it lives, what is left, and the conventions to keep. It was rewritten at the end of
the second session; the first session's work is summarised under "What was added, by layer".

## Working agreement

- Branch: **`claude/practical-thompson-rhsk4c`** (pushed; 10 commits on top of `origin/main` 5f20d1f, which merged
  the first session's branch as PR #6). Keep committing there and push with
  `git push -u origin claude/practical-thompson-rhsk4c`. **Don't open a pull request** unless the user asks.
  Force-pushing is blocked by the environment, so don't amend pushed commits.
- Commit messages: imperative subject; the body explains *why*, in the README's plain style. End every message
  with the attribution lines your session's system reminder gives (`Co-Authored-By: …` and `Claude-Session: …`).
  No model names in code, docs or commits.
- The user is on Ubuntu 24.04 (noble) and wants concise answers.
- One commit per roadmap item (or a coherent group), each with tests and the README updated. All **159 tests
  pass** (`pytest`, about 27 s; the Hypothesis laws take about 12 s of that).

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
an ASPI, hand-typed trades, dividends, `security_info`, sector indices, every optional `data/` file and a
`journal.csv`. It's for looking at pages, not checking numbers. Rebuild it after schema changes (an old demo
database has statements marked for re-parsing, which only `smpa run`/`parse` does). Restart the server after
Python changes (static files are read on each request). Playwright and Chromium are installed: screenshot `/`,
`/#/performance`, `/#/habits` and `/#/t/AAA.N0000?sold=2024-01-02` at 1440 px, in dark mode
(`colorScheme: "dark"`) and at 390 px, and check `document.documentElement.scrollWidth - clientWidth == 0`.

The CSE API (`www.cse.lk/api/…`) is reachable from the container. Real response shapes are in the tests
(`tests/test_prices.py`: `COMPANY_AAA`, `SNP`, `SECTORS`, `SUMMARY`, `ANNOUNCEMENTS`). Keep live requests to a
handful.

## Status of the roadmap

| # | Idea | Status | Where |
|---|---|---|---|
| 1 | Daily `smpa prices` timer | **Done** | `smpa timer [--remove]` → `src/smpa/timer.py` |
| 2 | Point-in-time "As of" | **Done** | `web/api.py` `_dates`, `_valuation_prices`, `Data.as_traded` |
| 3 | Liquidation value, break-even | **Done** | `src/smpa/fees.py`; holdings columns, "If sold", "Gain if sold" |
| 4 | Exit value if sold on any date | **Done** | `whatif.if_sold_on`; chart 03 "Gain if sold"; per-stock "If sold on" |
| 5 | A page per stock | **Done** | `src/smpa/ticker.py`, `static/ticker.js`, `#/t/SYMBOL` |
| 6 | Keep what the CSE API sends | **Done** | `prices.py`; tables `daily_quotes`, `security_info`, `history_changes`, `announcements` |
| 7 | TWR, volatility, drawdown | **Done** | `performance.py`, `web/report.py`, `#/performance`; TWR also on the overview |
| 8 | Holding vs selling, sold too early | **Done** (trailing stop optional, not done) | per stock: `whatif.holding_vs_selling`, `sales_review`; overview Sales tab: `whatif.sales_summary` |
| 9 | ASTRI, KS-PME, direct alpha | **Done** | Performance page; overview chart 03 header and readout (`analysis.benchmark`) |
| 10 | Disposition effect, round trips, fee drag | **Done** | `src/smpa/habits.py`, `GET /api/habits`, `static/habits.js`, `#/habits` |
| 11 | CCPI and USD returns | **Done** | `web/report._money`, `performance.converted_xirr` |
| 12 | Thin-trading beta, VaR, risk contributions | **Done** | `performance.dimson_beta`, `value_at_risk`, `risk_contributions` |
| 13 | Attribution | **Done** | `performance.attribution`, `performance.brinson` |
| 14 | Adjusted prices, lot engine, invariant tests | **Done** | `adjust.py` (incl. `total_return_closes`), `lots.py`, `tests/test_laws.py`, `tests/golden/` |

"Found while reviewing the code" (docs/README.md): all seven are now fixed. #2 (the negative ASPI line) was the
last: the line stops where it would go below zero and the header shows direct alpha. **docs/README.md itself
hasn't been updated to say so** (see "Left to do").

doc 06 §8 "Smaller things": caching, exports and alerts are done; `recorded_on` is not.

## What this session added (newest last)

1. **The overview's ASPI line** (`3b02db2`). `analysis.benchmark` stops the dashed line on the day the index side
   would go below zero (`emptied`), leaves the index XIRR out from then, and returns `ks_pme`, `direct_alpha` and
   `period_alpha` over the same window and flows. It imports `performance` inside the function (performance
   imports analysis). Chart 03's header shows direct alpha; a note under the chart (`#val-foot`) explains a stop.
2. **Third readout row** (`2f653fe`): "Time-weighted" (`performance.twr_return`, also used by `risk_table` so both
   pages agree) and "vs the ASPI" (direct alpha from the benchmark window). `readouts.twr` in the payload.
3. **Split bug fix** (`3533b4f`): for a past "As of", chart 03 used only actions up to that date to undo the
   CSE's split adjustment. `Data.as_traded` (a cached property: `actual_history` with *all* actions) is now the one
   as-traded price history for every page.
4. **Sales tab** (`b598cac`): `payload.sales` (`whatif.sales_review` for every symbol, up to the valuation day) and
   `payload.sales_summary` (`whatif.sales_summary`: averages with `n`, rupees left on the table). Exit efficiency is
   now only for sales at a gain. After-sale returns are no longer alert-coloured (also on the stock page).
5. **Habits page** (`845dbe0`). `src/smpa/habits.py`, each function tested by hand in `tests/test_habits.py`:
   - `wilson`, `share_of` (every share has its 95% range), `decisions` (same-day fills of a stock = one decision)
   - `disposition` (doc 05 §2's sketch, ties skipped), `round_trips`, `trip_stats`
   - `fees_by_year` (by kind, drag, turnover, the year's TWR), `fees_by_stock`
   - `trading_alpha` (frozen 1 January portfolio vs TWR), `switches`, `switch_summary`, `never_sold`
   - `buys_review` (tag from the start-of-day position; 20-trading-day excess over the ASPI; +3/+12 months),
     `buys_summary`, `journal_review` (FIFO per journaled buy, dividends per lot), `by_reason`

   Payload: `web/report.habits_payload` (report.py now holds both extra pages). Route in `server.VIEWS`; front end
   `static/habits.js` (added to `FILES`), nav link and `PAGES` entry in `app.js`. `performance.window_xirr` gives
   the money-weighted return over exactly the TWR's days (Performance page and Habits timing). The stock page's
   "Trades and fees" table shows journal reasons and each journaled buy's outcome (`ticker._fills`).
6. **Cash left idle** (`7db580c`). Parser: `models.BalanceRecord`, `ParsedDocument.balance` (statements). Storage:
   `balance_records` (per document, `parsers._store`/`_forget`) → `cash_balances` (one per period, rebuilt by
   `reconcile`; two copies must agree). `db.connect` marks statements for re-parsing once when the table is new.
   `src/smpa/cash.py`: `flows_at_broker`, `cash_history` (daily balance, `breaks` where documents don't add up,
   `known` mask, `anchor`), `account_return` (account vs shares XIRR, drag, average cash, T-bill interest
   forgone). Habits section H8 (`report._cash`).
7. **Rest of item 14** (`a42bdb2`, `57c1eb4`): `adjust.unrecorded_splits` flags in `build_payload`;
   `adjust.total_return_closes` (dividends put back, 1 − D/P) for the stock page's risk and the Performance page's
   per-holding risk, plus `ticker.year` (past year with and without dividends); `analysis.dividends_per_share`
   (moved from habits). `tests/test_laws.py`: laws 1–6 with Hypothesis (law 7 is `test_fees.py`); profiles
   `default` (100 cases) and `thorough` in `conftest.py`; `conftest.fixture_db` builds the parsed fixtures.
   `tests/test_golden.py` + `tests/golden/*.json`: five pages; accept intended changes with
   `SMPA_GOLDEN=update pytest tests/test_golden.py` after reading what it reports.
8. **Caching and exports** (`8629809`): `server.Cache` keeps encoded payloads keyed on `Config.inputs` (DB, its
   `-wal`, every data CSV: mtime_ns and size) plus today's date. `web/exports.py`: `/api/export/<table>.csv`
   (overview tabs, honours `as_of`), `/api/export/yearly.csv` (`exports.yearly`), `/api/export/stock/<SYM>.csv`.
   The action log has a new `withheld` column (tax on per-share dividends).
9. **Alerts** (`a952f03`): `src/smpa/alerts.py` (`price_alerts`, `announcement_alerts`, `KINDS`), `payload.alerts`
   (latest view only, `api._alerts`), panel `#alerts` above the flags. `prices.parse_announcements`, table
   `announcements`, fetched in `_store_extras`.

## What the first session added, by layer (still true)

- **Data (`db.py`)**: `daily_quotes`, `security_info`, `history_changes`; `price_history.volume`/`fetched_at` and
  `index_history.fetched_at` via `_NEW_COLUMNS`. Index names: `ASPI`, `SPSL20`, `sector:<name>`.
- **Prices**: daily rows dated by the last trade (holidays add nothing); extras never block prices;
  `update_history` stores volume and records re-downloaded closes that changed (`history_changes`).
- **Modules**: `fees.py` (fee rate from your last 5 broker trades), `lots.py` (FIFO alongside `_Ledger`, checked
  by `replay`), `whatif.py`, `adjust.py` (`actual_*` for valuing ledger counts, `adjusted_closes` for returns),
  `performance.py`, `extdata.py` (strict CSV loaders), `ticker.py`.
- **Web**: `load_data()` returns a `Data`; `_plain()` makes payloads JSON-safe; GET routes go through `_get`;
  **new static files must be added to `FILES`**; page payloads registered in `VIEWS`. Front end: ES modules, no
  dependencies; shared helpers in `lib.js` (`F`/`NUMERIC` formatters incl. the new `share2`, `dataTable`, `box`).
  Labels are uppercased with letter-spacing, so avoid Greek letters.

## Left to do

1. **`recorded_on` for corporate actions** (doc 06 §4, bitemporal). An optional `recorded_on` column in
   `corporate_actions.csv` (validated like the others in `actions.load_actions`), shown in the Corp. actions tables,
   and an "as known then" option for a past "As of" that drops actions recorded after it. `price_history` and
   `index_history` already have `fetched_at` for the same idea on prices.
2. **docs/README.md**: mark the roadmap rows done and the "Found while reviewing the code" items fixed (the table
   points here for status), and give the main README a final read for consistency.
3. **Optional: trailing-stop experiment** (doc 03 §5): per lot, sell at the first close X% below the highest close
   since the buy, fees included, against what you actually did. Fits the Habits page.
4. **Worth a look**: `performance.compare_with_index` (Performance page benchmarks) returns None when an index's
   history starts before the first day with a portfolio value and the first flow is earlier still
   (`value_on(start)` is None). `analysis.benchmark` (overview) starts at the first day with both instead. Starting
   `compare_with_index` at the first day that has both would make the pages agree.
5. **Alert categories**: only `CASH DIVIDEND`, `RIGHTS ISSUE`, `RIGHTS ISSUE (DATES)`, `TRADING HALTED` were seen in
   a live response; the patterns for splits, bonus issues, scrip dividends, delistings and offers in
   `alerts.KINDS` are guesses. Check them against real announcements as they appear in the `announcements` table.

## Gotchas

- Strictness is the project's core rule: no doubtful numbers. A file mistake stops with its line number; an
  unknown price is left unpriced, never guessed; extras from the API are logged and skipped.
- Every performance figure covers only the days with a value for *every* holding (`value_history`), so with real
  data it usually starts when price history does (a year from the API, plus daily runs).
- `growth` from `twr_index` starts at 1 on the first valued day unless nothing was held going into it;
  `twr_return` measures from its first point (as the Performance page always has).
- The S&P SL20, sector indices and announcements only accumulate from daily runs.
- `fees.fee_rate` excludes `manual`, `rights issue` and `delisting` rows. Statement-only trades count.
- Pandas 3 is installed. Build empty date-indexed series with `pd.DatetimeIndex([])`. In a DataFrame a `None`
  becomes NaN: test with `pd.isna`, not `is None`.
- The golden test fails on *any* payload change, including a new key. That's intended: read the report, then
  update the copies in the same commit as the change.
- On the test fixtures the cash history has a break: no deposit is documented between the March 2021 and October
  2026 statements, so the account return starts on 30 Sep 2026 and needs closes for that day.
- Rights subscriptions, buyouts and dividends are taken to go through your bank, not the broker account
  (`cash.flows_at_broker`); manual trades are taken to go through the broker.
- The frozen-portfolio comparison (trading alpha) only knows dividends you recorded, usually only those received,
  which flatters trading a little; the Habits page says so.
