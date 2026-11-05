# Stock market performance analyzer

Pulls NDB Securities statement PDFs from Gmail, extracts their text, parses and cross-checks every
trade and deposit, and shows portfolio performance on a local web dashboard.

```
Gmail (read-only) ──► data/raw/*.pdf ──► data/text/*.txt ──► data/smpa.db ──► dashboard
     smpa fetch          smpa extract        smpa parse         smpa dashboard
```

All private data — PDFs, extracted text, the SQLite DB and OAuth tokens — lives in
`data/`, which is gitignored. So is `config.toml`.

## Setup (Ubuntu 24.04)

```bash
sudo apt install python3-venv
```

Only if you ever enable OCR (`[ocr].mode`), also install `tesseract-ocr poppler-utils`.

```bash
python3 -m venv .venv && source .venv/bin/activate
```

```bash
pip install -e '.[dev]'
```

```bash
cp config.example.toml config.toml
```

Set `[gmail].query` in `config.toml` to match your broker's emails, e.g.
`from:(advisor@ndbs.lk) has:attachment filename:pdf`. Test it in the Gmail search box first.
Set `[parser].account` to your account number so documents for any other account are rejected.

### Gmail API credentials (one-time)

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project.
2. **APIs & Services → Library**: enable the **Gmail API**.
3. **OAuth consent screen**: choose *External*, fill in the app name, and add your Gmail address as a **test user**.
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON to
   `data/secrets/credentials.json`.
5. Run `smpa auth`. A browser opens; approve read-only access. The token is saved to `data/secrets/token.json`.

While the app is in *Testing* mode, Google expires the token after 7 days; `smpa` re-prompts when that happens.

## Usage

```bash
smpa run          # fetch new emails + extract + parse (incremental) + latest prices and price history
```

```bash
smpa dashboard    # opens http://localhost:8501 (--port, --no-browser)
```

Individual steps: `smpa fetch`, `smpa extract [--force]`, `smpa parse [--force]`, `smpa prices`.
`smpa import <folder>` adds PDFs you already have on disk. `smpa run --no-fetch` skips Gmail,
`--no-prices` skips the price update.

### Keep price history growing

The CSE API gives at most a year of daily closes, so a day that isn't stored is gone once it's a year old.
Run this once, in this folder:

```bash
smpa timer
```

It installs a systemd user timer that runs `smpa prices` at 4 pm Colombo time every weekday (the market
closes at 2:30), and catches up after the computer was off. To keep it running while you're logged out:

```bash
sudo loginctl enable-linger "$USER"
```

Check it with `systemctl --user list-timers` and `journalctl --user -u smpa-prices`; `smpa timer --remove`
takes it out again. It runs `smpa prices`, not `smpa run`, because the Gmail token needs a browser every 7 days.

The dashboard is a small local web app: Python's built-in HTTP server (`src/smpa/web/server.py`) serves
one JSON endpoint and a hand-written HTML/CSS/JS page (`src/smpa/web/static/`) with SVG charts. No
framework, nothing loaded from the internet. It listens on 127.0.0.1 only, refuses requests addressed to any
other host name, and its one action (refreshing prices) requires a header other websites can't send.

The **Sales** tab under the charts lists every sale with the stock's price 1, 3, 6 and 12 months later, against
the price you sold at and against the ASPI, and above it the averages over all your sales, each with how many
sales it covers: one sale says little, the average says whether you tend to sell too early.

Click any symbol (a holdings row, a node in chart 01, a row in the position index) for its own page: lifetime
P&L (realized + unrealized + dividends, which add up across stocks to the total gain), its money-weighted return
before and after selling fees and against the ASPI (direct alpha, KS-PME), fees, yields, 52-week range, the CSE's
betas next to ones from your price history (plain, and corrected for thin trading), volatility, drawdown and how
many days it would take to sell. Its charts show the price with your trades and average cost, the gain had you
sold on each day (best and worst days marked, in hindsight), and value against cost. **If sold on** a date
compares selling everything then with what actually happened, and follows the exact shares held that day (FIFO)
to today, with and without what the money could have earned in the ASPI meanwhile. Tables: FIFO lots, trades
with each fee, dividends and corporate actions, and the price 1, 3 and 12 months after each sale.

**Performance** (the link under the header) measures how *well*, over the days with a value for every holding:
the time-weighted return of your picks next to the money-weighted one (the gap is your timing), growth of 100
against the ASPI and any other benchmark, KS-PME and direct alpha (fair comparisons that can't go negative, unlike
replaying your cash flows into the index), volatility, drawdown, Sharpe and Sortino, beta and tracking error on the ASPI,
historical VaR, a block-bootstrap range for the next year (a range, not a forecast), monthly returns (Modified
Dietz), returns after inflation and in US dollars, concentration and each holding's share of the risk, and how
much of the last year's return each stock and sector contributed (Brinson–Fachler). Volatility is annualized with
the CSE's own trading days, counted from the ASPI history (about 240 a year, not the US 252).

The look is a monochrome, Swiss-style console: Helvetica (falls back to Nimbus Sans, which Ubuntu ships in
`fonts-urw-base35`), hairlines, corner brackets, and one accent colour reserved for losses and flags. Colours
and type are CSS variables at the top of `src/smpa/web/static/app.css`. There is a dark theme: it follows your
OS setting, and the **Theme** button in the header switches between Auto, Light and Dark (remembered per browser).

### Optional files in `data/`

| File | Columns | Purpose |
|---|---|---|
| `prices.csv` | `symbol,price` | Overrides individual prices from the CSE (e.g. a suspended stock) |
| `manual_transactions.csv` | `trade_date,side,symbol,quantity,price[,fees]` | Trades not in any document |
| `corporate_actions.csv` | `date,symbol,action` + see below | Dividends, bonus issues, splits and rights issues |
| `tbill.csv` | `date,yield` (364-day yield in percent, e.g. `8.75`; CBSL weekly auctions) | Risk-free rate: Sharpe, Sortino, a T-bill benchmark |
| `ccpi.csv` | `month,index` (`YYYY-MM`, Colombo CPI) | Return after inflation |
| `fx.csv` | `date,lkr_per_usd` (CBSL) | Return in US dollars |
| `indices.csv` | `name,date,value` | More benchmarks, e.g. `ASTRI` month-end levels (the ASPI leaves dividends out) |
| `sectors.csv` | `symbol,sector` (an S&P/CSE industry group as the CSE names it, e.g. `Banks`) | Sector attribution |

These are checked as strictly as the corporate actions file: a mistake stops with the file and line number.

#### Corporate actions

Broker statements don't show these: the company's registrar pays dividends to your bank and credits bonus
shares to your CDS account directly. Add one row per event. `date` is the **ex-date (XD)**: an action applies
to the shares you held at the start of that day, before any trades on it. Columns you don't need can be left
out or empty. A mistake stops with the line number, like a document that doesn't add up.

| `action` | Fill in | Effect |
|---|---|---|
| `DIVIDEND` | `amount` (cash received, after withholding tax) **or** `per_share` (less `[tax].dividend_withholding`) | Income: counts in your return, not in cost |
| `BONUS` | `ratio` new:held, e.g. `1:10`, **or** `quantity` received | More shares at the same total cost (fractions round down; `quantity` wins) |
| `SPLIT` | `ratio` new:old, e.g. `2:1` (subdivision) or `1:5` (consolidation) | Same as above |
| `RIGHTS` | `quantity` subscribed, `price` per share | A purchase at the subscription price (no brokerage) |
| `DELIST` | `price` each share was bought out at (`0` if you got nothing) | Sells every share you hold at that price: the position closes and the gain or loss is realized |

```csv
date,symbol,action,amount,per_share,ratio,quantity,price,note
2024-03-12,JKH.N0000,DIVIDEND,,0.50,,,,interim
2024-06-20,HAYC.N0000,DIVIDEND,1275.00,,,,,final after tax
2024-07-01,COMB.N0000,BONUS,,,1:20,,,
2024-08-15,SAMP.N0000,SPLIT,,,3:1,,,
2024-09-02,HNB.N0000,RIGHTS,,,,50,180.00,
```

A missing split is easy to miss and badly wrong: a 1-into-5 split you haven't entered looks like an 80% loss.
So if a stock's price drops to about 1/2 … 1/10 between two of your trades, or between your last trade and
today, with no split or bonus entered, the dashboard flags it. A real crash can trip this too, so check first.
For example, Hemas (HHL) shares were sub-divided 1-into-5 and traded on the new basis from 7 May 2025:
`2025-05-07,HHL.N0000,SPLIT,5:1` (with `date,symbol,action,ratio` as the header).

When a company leaves the exchange, its majority owner usually buys out the other shareholders, paying
outside your broker. Use `DELIST` with the date you were paid. For example, Expolanka's Rs. 185 exit offer was
paid from 10 June 2024: `2024-06-10,EXPO.N0000,DELIST,185` (header `date,symbol,action,price`). If you kept
your shares, they are now in an unlisted company: enter `0` to write them off, or a price you think is fair.

Rights you sell instead of taking up trade under their own symbol (e.g. `HNB.R0000`). Enter the rights you
were given as a `BONUS` on that symbol with their `quantity`, so the sale counts as a gain on zero cost.

## How it works

- **Fetch**: Gmail search with your query; every PDF attachment is saved once (tracked by message ID).
- **Extract**: reads the PDF's embedded text. NDB's PDFs are machine-generated, so this is exact and no OCR
  is used. A page without text stops the run (set `[ocr].mode = "auto"` to OCR such pages instead).
- **Parse** (`src/smpa/parsers/ndb/`): each document is classified and matched against known layouts:

  | Document | Layouts (month first seen) | Gives |
  |---|---|---|
  | Contract Note (BOUGHT/SOLD Note) | 2021-05, 2025-03 (adds foreign brokerage), 2025-08 (adds clearing) | trades with full fees |
  | Trade Confirmation | 2021-05, 2021-07 (per-row dates), 2025-03 (adds foreign brokerage), 2025-05 (CSE → Exchange), 2025-08 (adds clearing), 2026-09 | trades with fees, company name and ISIN |
  | Statement of Accounts | 2021-05, 2025-03 (month-first period dates), 2026-10 | trades (net amount only), deposits and payments to you |
  | Receipt | 2026-11 | deposits |

  Every line is checked: quantity × price = gross, gross ± fees = net, rows add up to page and document
  totals, statement running balances and summaries match. **An unknown document, layout or line, or any
  failed check, is logged and stops the run** — no doubtful numbers get stored.
- **Reconcile**: one trade per contract number. The Contract Note is the record of truth, then the Trade
  Confirmation, then the statement; every other copy must match it exactly or the run stops. Deposits are
  matched by receipt number. Missing counterparts (e.g. no Contract Note) are warnings, shown in the log and
  on the dashboard's Documents tab.
- **Prices** (`smpa prices`): last traded prices for your current holdings from the CSE website's unofficial
  API (documented at [GH0STH4CKER/Colombo-Stock-Exchange-CSE-API-Documentation](https://github.com/GH0STH4CKER/Colombo-Stock-Exchange-CSE-API-Documentation)).
  One `tradeSummary` request covers everything that traded today; other holdings are looked up one by one
  (`companyInfoSummery`); before a stock's first trade of the day, its previous close is used. Each run also
  keeps one price per stock per trading day in `price_history` (dated by its last trade, so a holiday adds
  nothing), and the day's ASPI level. If the API's responses change shape, the step stops with an error
  instead of storing wrong prices.
- **Market data kept on the side**: the same responses say more than prices, so each run also stores every
  traded stock's day (open, high, low, close, volume, turnover, market cap) in `daily_quotes`, and for each
  holding, once a day, the CSE's betas, 52-week range, size and turnover in `security_info`. It also stores the
  S&P SL20 (`SPSL20`) and the 20 S&P/CSE industry group indices (`sector:Banks` …) in `index_history`. These
  are extras: if one fails it's logged and skipped, and never holds up your own prices.
- **Price history**: `smpa prices` also downloads a year of daily closes (the most the API gives) for the ASPI
  and every stock you held during that year (with each day's volume), and again once a week to fill in days
  it wasn't run. From then on the daily runs extend it, so the history grows past a year. A problem here is
  logged and skipped: it only feeds the charts. If a re-downloaded close differs from the stored one by more
  than 0.5%, the dashboard flags it: the CSE corrected it, or began adjusting its history for a split.
- **Analysis**: average-cost method. Fees are added to buy cost and deducted from sell proceeds. Market value
  and unrealized P&L use the latest prices. Corporate actions change share counts, not cost.
- **If sold**: selling costs about 1.13% in fees, so *If sold* is what selling everything would actually bring
  in, and each holding's *break-even* is the price at which selling gets back what it cost (fees both ways).
  The fee rate on a day is the median fees ÷ gross of your last five broker trades before it (within half a
  year of each other, since the rates change; the clearing fee came in 2025). `[fees].rate` is used only
  until you have a broker trade.
- **As of**: a past date shows the shares held then, valued at each stock's last close on or before that day
  (from `price_history`, at most 31 days old), with fees and returns up to that day. `prices.csv` overrides
  apply to today only.
- **Return**: the dashboard's *Annual return* is the money-weighted return (XIRR, as in Excel) of your share
  investing: buys and rights subscriptions are money in, sales and dividends money out, and today's holdings
  count as if sold on the date prices were fetched (unpriced ones at cost, marked ≈). Cash left idle at the
  broker isn't counted. For under a year of history it shows the plain return instead, since an annual rate
  would exaggerate it.
- **Time-weighted** and **vs the ASPI**: the return of your picks with the timing of your money taken out, and
  direct alpha (below), both over the days chart 03 has a value for. They're the same numbers as on the
  Performance page; the gap between the time-weighted return and the XIRR is your timing.
- **Value vs invested** (chart 03): market value on each day with prices for every holding, against net invested.
  The dashed line puts the same money in the **ASPI**: from the first day with both a value and an index level
  (or from your first purchase, if that's later), each buy adds index units at that day's level and each sale
  or dividend takes some out. Selling a big winner can take out more than the index side holds; it would then
  go below zero, so the line stops there, with a note. The header compares both money-weighted returns over that
  stretch, and gives **direct alpha**: your yearly return over the ASPI with the same cash flows, which has no
  such flaw. The ASPI is a price index, so dividends you record give your side a small edge. If a holding had a split or bonus issue during the
  year, check the line around that date: it's not known whether the CSE's history is adjusted for them.

When a run stops, the log (`data/logs/smpa.log`) names the file, line and reason. Usually it's a layout
version not seen yet: share that document's extracted text (from `data/text/`, personal details removed) to
add it. If the file isn't a statement at all, add its name to `[parser].ignore_attachments`.

## Development

```bash
pytest
```

To look at the dashboard without your own statements, build a demo folder (made-up prices, trades and data
files around the test fixtures) and open it:

```bash
python scripts/make_demo.py /tmp/smpa-demo && smpa -c /tmp/smpa-demo/config.toml dashboard
```
