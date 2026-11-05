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

#### Corporate actions

Broker statements don't show these: the company's registrar pays dividends to your bank and credits bonus
shares to your CDS account directly. Add one row per event. `date` is the **ex-date (XD)**: an action applies
to the shares you held at the start of that day, before any trades on it. Columns you don't need can be left
out or empty. A mistake stops with the line number, like a document that doesn't add up.

| `action` | Fill in | Effect |
|---|---|---|
| `DIVIDEND` | `amount` (cash received, after withholding tax) **or** `per_share` (counted before tax) | Income: counts in your return, not in cost |
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
  keeps one price per stock per day in `price_history`, and the day's ASPI level. If the API's responses
  change shape, the step stops with an error instead of storing wrong prices.
- **Price history**: `smpa prices` also downloads a year of daily closes (the most the API gives) for the ASPI
  and every stock you held during that year, and again once a week to fill in days it wasn't run. From then
  on the daily runs extend it, so the history grows past a year. A problem here is logged and skipped: it
  only feeds the value chart.
- **Analysis**: average-cost method. Fees are added to buy cost and deducted from sell proceeds. Market value
  and unrealized P&L use the latest prices. Corporate actions change share counts, not cost.
- **Return**: the dashboard's *Annual return* is the money-weighted return (XIRR, as in Excel) of your share
  investing: buys and rights subscriptions are money in, sales and dividends money out, and today's holdings
  count as if sold on the date prices were fetched (unpriced ones at cost, marked ≈). Cash left idle at the
  broker isn't counted. For under a year of history it shows the plain return instead, since an annual rate
  would exaggerate it.
- **Value vs invested** (chart 03): market value on each day with prices for every holding, against net invested.
  The dashed line puts the same money in the **ASPI**: from the first day with both a value and an index level
  (or from your first purchase, if that's later), each buy adds index units at that day's level and each sale
  or dividend takes some out. The header compares both returns over that stretch. The ASPI is a price index,
  so dividends you record give your side a small edge. If a holding had a split or bonus issue during the
  year, check the line around that date: it's not known whether the CSE's history is adjusted for them.

When a run stops, the log (`data/logs/smpa.log`) names the file, line and reason. Usually it's a layout
version not seen yet: share that document's extracted text (from `data/text/`, personal details removed) to
add it. If the file isn't a statement at all, add its name to `[parser].ignore_attachments`.

## Development

```bash
pytest
```
