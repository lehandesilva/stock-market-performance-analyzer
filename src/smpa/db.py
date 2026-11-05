"""SQLite storage: documents, per-document parsed records, and reconciled tables."""

from __future__ import annotations

import sqlite3
from pathlib import Path

FEE_COLUMNS = "brokerage, cds, cse, sec, stl, clearing, foreign_brokerage"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS gmail_messages (
    message_id   TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,          -- 'gmail' | 'local'
    message_id      TEXT NOT NULL,
    attachment_name TEXT NOT NULL,
    email_date      TEXT,                   -- ISO date
    subject         TEXT,
    sender          TEXT,
    path            TEXT NOT NULL,          -- relative to data_dir
    sha256          TEXT NOT NULL,
    downloaded_at   TEXT NOT NULL,
    text_path       TEXT,
    extract_method  TEXT,                   -- text | ocr | mixed | empty
    extracted_at    TEXT,
    extract_error   TEXT,
    doc_type        TEXT,                   -- contract_note | trade_confirmation | statement | receipt | ignored
    layout          TEXT,                   -- e.g. contract_note/v2
    account         TEXT,
    parser          TEXT,
    parsed_at       TEXT,
    parse_count     INTEGER,
    parse_error     TEXT,
    UNIQUE (message_id, attachment_name)
);

-- What each document says, before reconciliation (one row per trade per document).
CREATE TABLE IF NOT EXISTS trade_records (
    id              INTEGER PRIMARY KEY,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    doc_type        TEXT NOT NULL,
    contract_no     TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    settlement_date TEXT,
    side            TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    symbol          TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    price           TEXT NOT NULL,          -- decimals stored as exact text
    gross           TEXT NOT NULL,
    brokerage TEXT, cds TEXT, cse TEXT, sec TEXT, stl TEXT, clearing TEXT, foreign_brokerage TEXT,
    fees_total      TEXT NOT NULL,
    net             TEXT NOT NULL,
    isin            TEXT,
    company         TEXT
);
CREATE INDEX IF NOT EXISTS idx_trade_records_contract ON trade_records(contract_no);

CREATE TABLE IF NOT EXISTS cash_records (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    doc_type    TEXT NOT NULL,
    ref         TEXT NOT NULL,
    date        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    amount      TEXT NOT NULL,
    description TEXT
);

-- Reconciled: one row per contract number / receipt number. Rebuilt on every parse.
CREATE TABLE IF NOT EXISTS trades (
    contract_no     TEXT PRIMARY KEY,
    trade_date      TEXT NOT NULL,
    settlement_date TEXT,
    side            TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    price           REAL NOT NULL,
    gross           REAL NOT NULL,
    brokerage REAL, cds REAL, cse REAL, sec REAL, stl REAL, clearing REAL, foreign_brokerage REAL,
    fees_total      REAL NOT NULL,
    net             REAL NOT NULL,
    isin            TEXT,
    company         TEXT,
    sources         TEXT NOT NULL           -- e.g. "contract_note,trade_confirmation,statement"
);

CREATE TABLE IF NOT EXISTS cash_movements (
    ref         TEXT PRIMARY KEY,
    date        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    amount      REAL NOT NULL,
    description TEXT,
    sources     TEXT NOT NULL
);

-- Each statement's period and its cash balance at both ends, as parsed (credit positive: held for you).
CREATE TABLE IF NOT EXISTS balance_records (
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    account      TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    opening      TEXT NOT NULL,         -- exact decimal text
    closing      TEXT NOT NULL
);

-- One row per statement period, rebuilt by reconcile from balance_records.
CREATE TABLE IF NOT EXISTS cash_balances (
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    opening      REAL NOT NULL,
    closing      REAL NOT NULL,
    PRIMARY KEY (period_start, period_end)
);

-- Latest market price per symbol (CSE API, see prices.py) and one price per symbol per day.
CREATE TABLE IF NOT EXISTS prices (
    symbol     TEXT PRIMARY KEY,
    name       TEXT,
    price      REAL NOT NULL,
    change     REAL,
    change_pct REAL,
    traded_at  TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    symbol     TEXT NOT NULL,
    date       TEXT NOT NULL,           -- the trading day (Colombo)
    price      REAL NOT NULL,           -- close
    volume     REAL,                    -- shares traded that day, when the CSE says
    fetched_at TEXT,                    -- when it was stored
    PRIMARY KEY (symbol, date)
);

-- Daily index levels and when each history series was last downloaded in full (see prices.py).
-- Names: ASPI, SPSL20 (S&P Sri Lanka 20), sector:<name> (S&P/CSE industry group indices, e.g. sector:Banks).
CREATE TABLE IF NOT EXISTS index_history (
    name       TEXT NOT NULL,
    date       TEXT NOT NULL,
    value      REAL NOT NULL,
    fetched_at TEXT,
    PRIMARY KEY (name, date)
);

-- Every stock that traded on a day, from the tradeSummary request prices already make (~280 a day).
CREATE TABLE IF NOT EXISTS daily_quotes (
    symbol         TEXT NOT NULL,
    date           TEXT NOT NULL,
    open           REAL,
    high           REAL,
    low            REAL,
    close          REAL NOT NULL,
    previous_close REAL,
    share_volume   REAL,
    trades         INTEGER,
    turnover       REAL,
    market_cap     REAL,
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);

-- What companyInfoSummery says about a stock you hold, one row per stock per day it was asked.
CREATE TABLE IF NOT EXISTS security_info (
    symbol         TEXT NOT NULL,
    fetched_on     TEXT NOT NULL,
    name           TEXT,
    isin           TEXT,
    cse_id         INTEGER,
    market_cap     REAL,
    shares_issued  REAL,
    hi_52w         REAL,
    lo_52w         REAL,
    volume_52w     REAL,
    turnover_ytd   REAL,
    previous_close REAL,
    beta_tri_aspi  REAL,                    -- against the ASPI (total return, by its name)
    beta_spsl      REAL,                    -- against the S&P SL20
    beta_period    TEXT,
    beta_quarter   INTEGER,
    PRIMARY KEY (symbol, fetched_on)
);

-- Stored closes the CSE later reported differently (see prices.update_history).
CREATE TABLE IF NOT EXISTS history_changes (
    series     TEXT NOT NULL,
    date       TEXT NOT NULL,
    old        REAL NOT NULL,
    new        REAL NOT NULL,
    noticed_on TEXT NOT NULL,
    PRIMARY KEY (series, date, noticed_on)
);

CREATE TABLE IF NOT EXISTS history_fetches (
    series     TEXT PRIMARY KEY,            -- a symbol, or 'index:ASPI'
    fetched_on TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
    id      INTEGER PRIMARY KEY,
    level   TEXT NOT NULL,                  -- 'warning'
    subject TEXT NOT NULL,                  -- contract or receipt number
    message TEXT NOT NULL
);
"""

# Columns added to `documents` after the first release.
_NEW_DOCUMENT_COLUMNS = {"doc_type": "TEXT", "layout": "TEXT", "account": "TEXT", "parse_error": "TEXT"}
# Columns added to other tables later; nothing to redo, old rows just have them empty.
_NEW_COLUMNS = {"price_history": {"volume": "REAL", "fetched_at": "TEXT"}, "index_history": {"fetched_at": "TEXT"}}


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
    missing = {c: t for c, t in _NEW_DOCUMENT_COLUMNS.items() if c not in cols}
    if missing:
        for col, typ in missing.items():
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {typ}")
        # Old heuristic-parser output: derived data, re-created by parsing again.
        conn.execute("DROP TABLE IF EXISTS transactions")
        conn.execute("UPDATE documents SET parsed_at = NULL, parse_count = NULL, parser = NULL")
        conn.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    existing = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'documents'").fetchone()
    if existing:
        _migrate(conn)
    new_balances = not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'balance_records'").fetchone()
    conn.executescript(SCHEMA)
    if existing and new_balances:  # statements parsed before their balances were kept: read them again next run
        conn.execute("UPDATE documents SET parsed_at = NULL WHERE doc_type = 'statement'")
    for table, cols in _NEW_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    conn.commit()
    return conn
