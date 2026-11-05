"""Fee rate for a sale that didn't happen, from your own trades."""

from pathlib import Path

import pandas as pd
import pytest

from smpa.fees import fee_rate, fee_rates
from smpa.parsers.ndb import parse_document

FIX = Path(__file__).parent / "fixtures" / "ndb"


def trades(*names, sources="contract_note"):
    rows = [{"trade_date": pd.Timestamp(t.trade_date), "side": t.side, "symbol": t.symbol, "gross_amount": float(t.gross),
             "fees": float(t.fees_total), "sources": sources}
            for n in names for t in parse_document((FIX / n).read_text()).trades]
    return pd.DataFrame(rows).sort_values("trade_date", kind="stable")


def test_rate_in_force_on_a_day():
    tx = trades("contract_note_2021-05_bought.txt", "contract_note_2025-03_bought.txt", "trade_confirmation_2026-09.txt")
    assert fee_rate(tx, "2025-03-31")["rate"] == pytest.approx(0.0112, abs=1e-6)
    assert fee_rate(tx, "2026-09-30")["rate"] == pytest.approx(0.01132, abs=2e-5)  # the clearing fee came in 2025
    assert fee_rate(tx, "2020-01-01")["rate"] == pytest.approx(0.0112, abs=1e-5)  # before any trade: the first ones
    assert fee_rates(tx, ["2025-03-31", "2026-09-30"]).round(5).tolist() == [0.0112, 0.01132]


def test_manual_rights_and_buyouts_dont_count_and_the_setting_is_the_fallback():
    tx = trades("contract_note_2025-03_bought.txt")
    manual = tx.assign(sources="manual", fees=0.0)
    assert fee_rate(manual, "2025-04-01", fallback=0.0115) == {"rate": 0.0115, "trades": 0, "from": None, "to": None}
    assert fee_rate(pd.concat([tx, manual]), "2025-04-01")["trades"] == 2
