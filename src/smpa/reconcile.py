"""Merge what every document says into one trade per contract number.

Contract Notes are the record of truth, then Trade Confirmations, then
statements. Every other copy of a trade must agree with it exactly; any
difference raises ReconcileError. Missing counterparts are only warnings.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from decimal import Decimal

from .db import FEE_COLUMNS
from .parsers.models import FEE_FIELDS, ReconcileError

PRIORITY = ("contract_note", "trade_confirmation", "statement", "receipt")
TRADE_FIELDS = ("trade_date", "side", "symbol", "quantity", "price", "gross", "net")
MONEY_FIELDS = {"price", "gross", "net", *FEE_FIELDS}


def _value(row: sqlite3.Row, field: str):
    v = row[field]
    return Decimal(v) if field in MONEY_FIELDS and v is not None else v


def _diff(a: sqlite3.Row, b: sqlite3.Row, fields: tuple[str, ...]) -> list[str]:
    return [f"{f}: {_value(a, f)} vs {_value(b, f)}" for f in fields if _value(a, f) != _value(b, f)]


def _trade_diffs(a: sqlite3.Row, b: sqlite3.Row) -> list[str]:
    fields = list(TRADE_FIELDS)
    if a["settlement_date"] and b["settlement_date"]:
        fields.append("settlement_date")
    if a["brokerage"] is not None and b["brokerage"] is not None:  # both have a fee breakdown
        fields += FEE_FIELDS
    return _diff(a, b, tuple(fields))


def reconcile(conn: sqlite3.Connection) -> list[str]:
    """Rebuild `trades`, `cash_movements` and `issues`. Returns the warnings."""
    warnings: list[tuple[str, str]] = []

    by_contract: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute(
        "SELECT r.*, d.path FROM trade_records r JOIN documents d ON d.id = r.document_id ORDER BY r.id"
    ):
        by_contract[r["contract_no"]].append(r)

    trades = []
    for cno, recs in sorted(by_contract.items()):
        recs.sort(key=lambda r: PRIORITY.index(r["doc_type"]))
        primary = recs[0]
        for other in recs[1:]:
            if diffs := _trade_diffs(primary, other):
                raise ReconcileError(
                    f"contract {cno}: {primary['doc_type']} ({primary['path']}) and {other['doc_type']} "
                    f"({other['path']}) disagree on " + "; ".join(diffs)
                )
        types = {r["doc_type"] for r in recs}
        label = f"{primary['trade_date']} {primary['side']} {primary['quantity']} {primary['symbol']}"
        if types == {"statement"}:
            warnings.append((cno, f"{label}: only found in a statement (no Contract Note or Trade "
                                  "Confirmation), so the fee breakdown is unknown"))
        elif "contract_note" not in types:
            warnings.append((cno, f"{label}: no Contract Note found"))
        elif "trade_confirmation" not in types:
            warnings.append((cno, f"{label}: no Trade Confirmation found"))

        with_fees = next((r for r in recs if r["brokerage"] is not None), None)
        first = lambda col: next((r[col] for r in recs if r[col]), None)  # noqa: E731
        trades.append((
            cno, primary["trade_date"], first("settlement_date"), primary["side"], primary["symbol"],
            primary["quantity"], float(Decimal(primary["price"])), float(Decimal(primary["gross"])),
            *[float(Decimal(with_fees[f])) if with_fees else None for f in FEE_FIELDS],
            float(Decimal(primary["fees_total"])), float(Decimal(primary["net"])),
            first("isin"), first("company"), ",".join(t for t in PRIORITY if t in types),
        ))

    by_ref: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute(
        "SELECT c.*, d.path FROM cash_records c JOIN documents d ON d.id = c.document_id ORDER BY c.id"
    ):
        by_ref[r["ref"]].append(r)
    cash = []
    for ref, recs in sorted(by_ref.items()):
        recs.sort(key=lambda r: PRIORITY.index(r["doc_type"]))
        primary = recs[0]
        for other in recs[1:]:
            diffs = [f"{f}: {primary[f]} vs {other[f]}" for f in ("date", "kind") if primary[f] != other[f]]
            if Decimal(primary["amount"]) != Decimal(other["amount"]):
                diffs.append(f"amount: {primary['amount']} vs {other['amount']}")
            if diffs:
                raise ReconcileError(f"receipt {ref}: {primary['path']} and {other['path']} disagree on "
                                     + "; ".join(diffs))
        types = {r["doc_type"] for r in recs}
        cash.append((ref, primary["date"], primary["kind"], float(Decimal(primary["amount"])),
                     primary["description"], ",".join(t for t in PRIORITY if t in types)))

    conn.execute("DELETE FROM trades")
    conn.executemany(
        f"""INSERT INTO trades (contract_no, trade_date, settlement_date, side, symbol, quantity, price, gross,
                                {FEE_COLUMNS}, fees_total, net, isin, company, sources)
            VALUES ({','.join('?' * 20)})""",
        trades,
    )
    conn.execute("DELETE FROM cash_movements")
    conn.executemany("INSERT INTO cash_movements VALUES (?, ?, ?, ?, ?, ?)", cash)
    conn.execute("DELETE FROM issues")
    conn.executemany("INSERT INTO issues (level, subject, message) VALUES ('warning', ?, ?)", warnings)
    conn.commit()
    return [m for _, m in warnings]
