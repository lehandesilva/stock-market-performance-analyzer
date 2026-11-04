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

CREATE TABLE IF NOT EXISTS issues (
    id      INTEGER PRIMARY KEY,
    level   TEXT NOT NULL,                  -- 'warning'
    subject TEXT NOT NULL,                  -- contract or receipt number
    message TEXT NOT NULL
);
"""

# Columns added to `documents` after the first release.
_NEW_DOCUMENT_COLUMNS = {"doc_type": "TEXT", "layout": "TEXT", "account": "TEXT", "parse_error": "TEXT"}


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
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'documents'").fetchone():
        _migrate(conn)
    conn.executescript(SCHEMA)
    return conn
