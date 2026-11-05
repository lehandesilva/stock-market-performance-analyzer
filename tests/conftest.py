"""Shared test data: the worked example from docs/03-what-if-i-sold.md, and the parsed NDB fixtures."""

from pathlib import Path

import pandas as pd
import pytest
from hypothesis import HealthCheck, settings

from smpa.actions import empty_actions

# The laws in test_laws.py: `pytest --hypothesis-profile=thorough tests/test_laws.py` tries many more cases.
settings.register_profile("default", max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("thorough", max_examples=2_000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("default")

FIX = Path(__file__).parent / "fixtures" / "ndb"
ALL = ["contract_note_2021-05_bought.txt", "trade_confirmation_2021-05.txt", "statement_2021-05.txt",
       "contract_note_2026-09_sold.txt", "trade_confirmation_2026-09.txt", "statement_2026-10.txt", "receipt.txt"]


def fixture_db(data_dir: Path):
    """A data folder with the NDB fixtures extracted and parsed: six trades, three receipts, two statements."""
    from smpa.config import Config
    from smpa.db import connect
    from smpa.parsers import parse_all

    cfg = Config(data_dir=data_dir)
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    for i, name in enumerate(ALL):
        (cfg.text_dir / name).write_text((FIX / name).read_text())
        conn.execute(
            """INSERT INTO documents (source, message_id, attachment_name, email_date, path, sha256, downloaded_at,
                                      text_path, extracted_at) VALUES ('local', ?, ?, '2026-01-01', ?, ?, 'x', ?, 'x')""",
            (f"m{i}", name, f"raw/{name}", f"s{i}", f"text/{name}"),
        )
    conn.commit()
    parse_all(cfg, conn)
    conn.close()
    return cfg

TX_COLS = ["trade_date", "side", "symbol", "quantity", "price", "gross_amount", "fees", "net_amount", "contract_no",
           "sources", "company"]


def make_tx(rows, fee=0.0112):
    """(date, side, symbol, quantity, price) rows -> transactions, each paying `fee` of its gross."""
    out = []
    for i, (d, side, sym, q, p) in enumerate(rows, 1):
        gross = q * p
        f = round(gross * fee, 2)
        out.append((pd.Timestamp(d), side, sym, q, p, gross, f, gross + f if side == "BUY" else gross - f, str(i),
                    "contract_note", None))
    return pd.DataFrame(out, columns=TX_COLS).sort_values(["trade_date", "side", "contract_no"]).reset_index(drop=True)


def make_actions(rows):
    """(date, symbol, action, field=value...) dicts -> a corporate actions frame like load_actions gives."""
    df = empty_actions()
    if rows:
        df = pd.DataFrame([{**{c: None for c in df.columns}, "note": "", "tax": 0.0, **r} for r in rows],
                          columns=df.columns)
        df["date"] = pd.to_datetime(df["date"])
        for c in ("ratio_new", "ratio_old", "quantity", "price", "amount", "per_share", "tax"):
            df[c] = df[c].astype(float)
    return df


@pytest.fixture
def abc():
    """ABC.N0000: bought twice, partly sold, two dividends; fees 1.12% on every trade (docs 02 and 03)."""
    tx = make_tx([("2024-02-01", "BUY", "ABC.N0000", 1000, 40.0), ("2024-08-01", "BUY", "ABC.N0000", 500, 60.0),
                  ("2025-06-02", "SELL", "ABC.N0000", 600, 68.0)])
    actions = make_actions([{"date": "2024-11-01", "symbol": "ABC.N0000", "action": "DIVIDEND", "amount": 3000.0},
                            {"date": "2025-11-03", "symbol": "ABC.N0000", "action": "DIVIDEND", "amount": 1350.0}])
    closes = pd.DataFrame({"symbol": "ABC.N0000", "date": pd.to_datetime(["2025-03-03", "2026-11-05"]),
                           "price": [75.0, 55.0]})
    return tx, actions, closes
