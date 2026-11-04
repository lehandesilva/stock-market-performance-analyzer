# Stock market performance analyzer

Pulls NDB Securities statement PDFs from Gmail, extracts their text, parses and cross-checks every
trade and deposit, and shows portfolio performance on a local Streamlit dashboard.

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
smpa run          # fetch new emails + extract + parse (incremental)
```

```bash
smpa dashboard    # http://localhost:8501
```

Individual steps: `smpa fetch`, `smpa extract [--force]`, `smpa parse [--force]`.
`smpa import <folder>` adds PDFs you already have on disk. `smpa run --no-fetch` skips Gmail.

### Optional files in `data/`

| File | Columns | Purpose |
|---|---|---|
| `prices.csv` | `symbol,price` | Latest prices → market value and unrealized P&L |
| `manual_transactions.csv` | `trade_date,side,symbol,quantity,price[,fees]` | Trades not in any document |

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
- **Analysis**: average-cost method. Fees are added to buy cost and deducted from sell proceeds.

When a run stops, the log (`data/logs/smpa.log`) names the file, line and reason. Usually it's a layout
version not seen yet: share that document's extracted text (from `data/text/`, personal details removed) to
add it. If the file isn't a statement at all, add its name to `[parser].ignore_attachments`.

## Development

```bash
pytest
```
