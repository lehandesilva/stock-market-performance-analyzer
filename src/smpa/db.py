"""SQLite storage for documents and parsed transactions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
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
    parser          TEXT,
    parsed_at       TEXT,
    parse_count     INTEGER,
    UNIQUE (message_id, attachment_name)
);

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    trade_date   TEXT NOT NULL,             -- ISO date
    side         TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    symbol       TEXT NOT NULL,
    quantity     REAL NOT NULL,
    price        REAL NOT NULL,
    gross_amount REAL NOT NULL,
    fees         REAL NOT NULL DEFAULT 0,
    net_amount   REAL NOT NULL,             -- BUY: cash paid, SELL: cash received
    raw_line     TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_doc ON transactions(document_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
