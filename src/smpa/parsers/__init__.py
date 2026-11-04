"""Statement parsers: text -> transactions.

Every broker formats statements differently. A parser is a function
``parse(text, ctx) -> list[Transaction]``. Add a broker-specific module next to
``generic.py`` and register it in ``PARSERS``; select it with ``[parser].name``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone

from ..config import Config


@dataclass
class Transaction:
    trade_date: date
    side: str  # "BUY" | "SELL"
    symbol: str
    quantity: float
    price: float
    gross_amount: float
    fees: float = 0.0
    raw_line: str = ""

    @property
    def net_amount(self) -> float:
        """Cash paid for a buy / received for a sell, after fees."""
        return self.gross_amount + self.fees if self.side == "BUY" else self.gross_amount - self.fees


@dataclass
class ParseContext:
    dayfirst: bool = True
    fallback_date: date | None = None  # e.g. the email date


Parser = Callable[[str, ParseContext], list[Transaction]]


def _registry() -> dict[str, Parser]:
    from . import generic

    return {"generic": generic.parse}


def get_parser(name: str) -> Parser:
    parsers = _registry()
    if name not in parsers:
        raise SystemExit(f"Unknown parser {name!r}. Available: {', '.join(parsers)}")
    return parsers[name]


def parse_all(cfg: Config, conn: sqlite3.Connection, force: bool = False) -> tuple[int, int]:
    """Parse extracted text into transactions. Returns (documents, transactions)."""
    parser = get_parser(cfg.parser)
    where = "text_path IS NOT NULL" + ("" if force else " AND parsed_at IS NULL")
    rows = conn.execute(
        f"SELECT id, text_path, email_date FROM documents WHERE {where} ORDER BY id"
    ).fetchall()
    total = 0
    for row in rows:
        text = (cfg.data_dir / row["text_path"]).read_text()
        ctx = ParseContext(
            dayfirst=cfg.dayfirst,
            fallback_date=date.fromisoformat(row["email_date"]) if row["email_date"] else None,
        )
        txs = parser(text, ctx)
        conn.execute("DELETE FROM transactions WHERE document_id = ?", (row["id"],))
        conn.executemany(
            """INSERT INTO transactions (document_id, trade_date, side, symbol, quantity, price,
                                         gross_amount, fees, net_amount, raw_line)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (row["id"], t.trade_date.isoformat(), t.side, t.symbol, t.quantity, t.price,
                 t.gross_amount, t.fees, t.net_amount, t.raw_line)
                for t in txs
            ],
        )
        conn.execute(
            "UPDATE documents SET parser = ?, parsed_at = ?, parse_count = ? WHERE id = ?",
            (cfg.parser, datetime.now(timezone.utc).isoformat(timespec="seconds"), len(txs), row["id"]),
        )
        conn.commit()
        total += len(txs)
    return len(rows), total
