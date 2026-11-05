"""Statement parsers: extracted text -> trades and cash movements.

A parser is ``parse_document(text) -> ParsedDocument`` and must raise
``ParseError`` for anything it doesn't recognise exactly. Register new brokers
in ``_registry()`` and select one with ``[parser].name`` in config.toml.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from ..config import Config
from ..errors import PipelineStop
from .models import FEE_FIELDS, ParsedDocument, ParseError, ReconcileError

log = logging.getLogger(__name__)
Parser = Callable[[str], ParsedDocument]


def _registry() -> dict[str, Parser]:
    from . import ndb

    return {"ndb": ndb.parse_document}


def get_parser(name: str) -> Parser:
    parsers = _registry()
    if name not in parsers:
        raise SystemExit(f"Unknown parser {name!r}. Available: {', '.join(parsers)}")
    return parsers[name]


def _store(conn: sqlite3.Connection, doc_id: int, doc: ParsedDocument) -> None:
    conn.execute("DELETE FROM trade_records WHERE document_id = ?", (doc_id,))
    conn.execute("DELETE FROM cash_records WHERE document_id = ?", (doc_id,))
    conn.execute("DELETE FROM balance_records WHERE document_id = ?", (doc_id,))
    s = lambda v: None if v is None else str(v)  # noqa: E731  exact decimal text
    conn.executemany(
        f"""INSERT INTO trade_records (document_id, doc_type, contract_no, trade_date, settlement_date, side,
                symbol, quantity, price, gross, {', '.join(FEE_FIELDS)}, fees_total, net, isin, company)
            VALUES ({','.join('?' * 21)})""",
        [
            (doc_id, doc.doc_type, t.contract_no, t.trade_date.isoformat(), s(t.settlement_date), t.side,
             t.symbol, t.quantity, s(t.price), s(t.gross),
             *[s(t.fees[f]) if t.fees else None for f in FEE_FIELDS],
             s(t.fees_total), s(t.net), t.isin, t.company)
            for t in doc.trades
        ],
    )
    conn.executemany(
        "INSERT INTO cash_records (document_id, doc_type, ref, date, kind, amount, description) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(doc_id, doc.doc_type, c.ref, c.date.isoformat(), c.kind, s(c.amount), c.description) for c in doc.cash],
    )
    conn.executemany(
        "INSERT INTO balance_records (document_id, period_start, period_end, opening, closing) VALUES (?, ?, ?, ?, ?)",
        [(doc_id, b.period_start.isoformat(), b.period_end.isoformat(), s(b.opening), s(b.closing)) for b in doc.balances],
    )


def parse_all(cfg: Config, conn: sqlite3.Connection, force: bool = False) -> tuple[int, list[str]]:
    """Parse new documents, then reconcile everything.

    Returns (documents parsed, warnings). Raises PipelineStop on the first
    document that can't be parsed or on conflicting documents.
    """
    parser = get_parser(cfg.parser)
    ignore = [re.compile(p) for p in cfg.ignore_attachments]
    # Statements parsed before closing balances were kept are read again once, to store them.
    where = "text_path IS NOT NULL" + ("" if force else " AND (parsed_at IS NULL OR parse_error IS NOT NULL OR "
                                       "(doc_type = 'statement' AND id NOT IN (SELECT document_id FROM balance_records)))")
    rows = conn.execute(
        f"SELECT id, path, text_path, attachment_name FROM documents WHERE {where} ORDER BY email_date, id"
    ).fetchall()
    accounts = {r[0] for r in conn.execute(
        "SELECT DISTINCT account FROM documents WHERE account IS NOT NULL AND parse_error IS NULL")}
    if cfg.account:
        accounts.add(cfg.account)
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731

    for row in rows:
        if any(p.search(row["attachment_name"]) for p in ignore):
            log.info("ignored %s (matches [parser].ignore_attachments)", row["path"])
            conn.execute("DELETE FROM trade_records WHERE document_id = ?", (row["id"],))
            conn.execute("DELETE FROM cash_records WHERE document_id = ?", (row["id"],))
            conn.execute("DELETE FROM balance_records WHERE document_id = ?", (row["id"],))
            conn.execute("UPDATE documents SET doc_type = 'ignored', parsed_at = ?, parse_error = NULL, "
                         "parse_count = 0 WHERE id = ?", (now(), row["id"]))
            conn.commit()
            continue
        text = (cfg.data_dir / row["text_path"]).read_text()
        try:
            doc = parser(text)
            if accounts and doc.account not in accounts:
                raise ParseError(f"account {doc.account} differs from {', '.join(sorted(accounts))} "
                                 "(set [parser].account if you have more than one)")
        except ParseError as exc:
            conn.execute("UPDATE documents SET parse_error = ?, parsed_at = ? WHERE id = ?",
                         (str(exc), now(), row["id"]))
            conn.commit()
            raise PipelineStop(
                f"Cannot parse {row['path']}\n  text: {row['text_path']}\n  reason: {exc}\n"
                "Fix the parser for this layout, or add the file name to [parser].ignore_attachments "
                "if it isn't a statement."
            ) from exc
        accounts.add(doc.account)
        _store(conn, row["id"], doc)
        conn.execute(
            """UPDATE documents SET doc_type = ?, layout = ?, account = ?, parser = ?, parsed_at = ?,
                                    parse_count = ?, parse_error = NULL WHERE id = ?""",
            (doc.doc_type, doc.layout, doc.account, cfg.parser, now(), len(doc.trades) + len(doc.cash), row["id"]),
        )
        conn.commit()
        log.info("parsed %s as %s: %d trades, %d cash", row["path"], doc.layout, len(doc.trades), len(doc.cash))

    from ..reconcile import reconcile  # late import: reconcile imports .models

    try:
        warnings = reconcile(conn)
    except ReconcileError as exc:
        raise PipelineStop(f"Documents disagree: {exc}") from exc
    for w in warnings:
        log.warning(w)
    return len(rows), warnings
