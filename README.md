# Stock market performance analyzer

Pulls broker statement PDFs from Gmail, extracts their text (OCR for scanned pages),
parses trades, and shows portfolio performance on a local Streamlit dashboard.

```
Gmail (read-only) ──► data/raw/*.pdf ──► data/text/*.txt ──► data/smpa.db ──► dashboard
     smpa fetch          smpa extract        smpa parse         smpa dashboard
```

All private data — PDFs, extracted text, the SQLite DB and OAuth tokens — lives in
`data/`, which is gitignored. So is `config.toml`.

## Setup (Ubuntu 24.04)

```bash
sudo apt install python3-venv tesseract-ocr poppler-utils
```

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
`from:(statements@broker.com) has:attachment filename:pdf`. Test it in the Gmail search box first.
If statements are password-protected, add the password to `[pdf].passwords`.

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
| `manual_transactions.csv` | `trade_date,side,symbol,quantity,price[,fees]` | Trades the parser missed |

## How it works

- **Fetch**: Gmail search with your query; every PDF attachment is saved once (tracked by message ID).
- **Extract**: uses the PDF's embedded text where present and runs Tesseract OCR on pages that have none.
  Set `[ocr].mode = "always"` to OCR every page.
- **Parse**: `parsers/generic.py` treats a line as a trade when it has BUY/SELL, a ticker, and three numbers
  with quantity × price ≈ amount. Lines without a date take the last date seen (e.g. a "Trade Date:" header).
  The same trade in a contract note *and* a monthly statement is counted once.
- **Analysis**: average-cost method. Fees are added to buy cost and deducted from sell proceeds.

### Adding a parser for your broker

The generic parser is a heuristic. If the dashboard's **Documents** tab shows statements with no trades
found, open the extracted text there (or in `data/text/`) and write a parser:

1. Create `src/smpa/parsers/mybroker.py` with `def parse(text: str, ctx: ParseContext) -> list[Transaction]`.
2. Register it in `_registry()` in `src/smpa/parsers/__init__.py`.
3. Set `[parser].name = "mybroker"` and run `smpa parse --force`.

## Development

```bash
pytest
```
