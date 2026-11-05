"""Golden payloads (docs/06 §7): each page's JSON for the fixtures, with a year of made-up but fixed prices, against
the copy in tests/golden/. A change you meant to make is one look at the diff; an accidental one fails here.

To accept the new output after a change you meant:

    SMPA_GOLDEN=update pytest tests/test_golden.py
"""

import json
import math
import os
from pathlib import Path

import pandas as pd
import pytest

from conftest import fixture_db
from smpa.db import connect

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("SMPA_GOLDEN") == "update"


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    cfg = fixture_db(tmp_path_factory.mktemp("golden"))
    days = pd.bdate_range("2025-11-03", "2026-11-05")
    base = {"AAA.N0000": 14.0, "BBB.N0000": 90.0, "CCC.N0000": 9.8, "DDD.N0000": 24.0}
    with connect(cfg.db_path) as conn:
        for i, d in enumerate(days):
            wave = math.sin(i / 9)
            conn.executemany("INSERT INTO price_history (symbol, date, price, volume) VALUES (?, ?, ?, ?)",
                             [(s, d.strftime("%Y-%m-%d"), round(p * (1 + 0.0004 * i + 0.03 * wave * (k + 1) / 4), 2),
                               1000.0 * (k + 1) * (1 + i % 5)) for k, (s, p) in enumerate(base.items())])
            conn.execute("INSERT INTO index_history (name, date, value) VALUES ('ASPI', ?, ?)",
                         (d.strftime("%Y-%m-%d"), round(20_000 * (1 + 0.0003 * i + 0.01 * math.sin(i / 7)), 2)))
        conn.executemany("""INSERT INTO prices (symbol, price, change, change_pct, traded_at, fetched_at)
                            VALUES (?, ?, 0, 0.5, '2026-11-05T08:50:00+00:00', '2026-11-05T10:31:00+00:00')""",
                         [("AAA.N0000", 16.1), ("BBB.N0000", 95.0), ("CCC.N0000", 10.2), ("DDD.N0000", 25.3)])
        conn.commit()
    cfg.actions_csv.write_text("date,symbol,action,amount,per_share,ratio,note\n"
                               "2022-05-10,AAA.N0000,DIVIDEND,,0.75,,\n2026-03-02,BBB.N0000,DIVIDEND,240.00,,,final\n")
    cfg.journal_csv.write_text("contract_no,reason,target,stop,note\n2021900001,results play,16,10,\n"
                               "2026900011,a tip,12,8.5,from a friend\n")
    cfg.tbill_csv.write_text("date,yield\n2025-10-29,8.10\n2026-04-29,7.85\n")
    return cfg


@pytest.fixture(scope="module")
def pages(data) -> dict:
    """Every page, built once: the overview now and as of a past day, Performance, Habits and a stock's page."""
    from smpa.web.api import build_payload, ticker_payload
    from smpa.web.report import habits_payload, performance_payload

    cfg = data
    with connect(cfg.db_path) as conn:
        return {"overview": build_payload(cfg, conn), "as-of-2026-06-30": build_payload(cfg, conn, "2026-06-30"),
                "performance": performance_payload(cfg, conn), "habits": habits_payload(cfg, conn),
                "ticker-AAA": ticker_payload(cfg, conn, "AAA.N0000", "2026-03-02")}


def differences(want, got, where="", out=None) -> list[str]:
    """Where two payloads differ, floats within a part in a billion counting as the same."""
    out = [] if out is None else out
    if isinstance(want, dict) and isinstance(got, dict):
        for k in sorted(set(want) | set(got)):
            if k not in want or k not in got:
                out.append(f"{where}.{k}: {'missing' if k not in got else 'new'}")
            else:
                differences(want[k], got[k], f"{where}.{k}", out)
    elif isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            out.append(f"{where}: {len(want)} items, now {len(got)}")
        for i, (a, b) in enumerate(zip(want, got)):
            differences(a, b, f"{where}[{i}]", out)
    elif isinstance(want, float) or isinstance(got, float):
        if not (isinstance(want, (int, float)) and isinstance(got, (int, float)) and math.isclose(want, got, rel_tol=1e-9,
                                                                                                    abs_tol=1e-9)):
            out.append(f"{where}: {want!r}, now {got!r}")
    elif want != got:
        out.append(f"{where}: {want!r}, now {got!r}")
    return out


@pytest.mark.parametrize("page", ["overview", "as-of-2026-06-30", "performance", "habits", "ticker-AAA"])
def test_page_matches_its_golden_copy(pages, page):
    got = json.loads(json.dumps(pages[page], allow_nan=False))
    path = GOLDEN / f"{page}.json"
    if UPDATE or not path.exists():
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(json.dumps(got, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        if not UPDATE:
            pytest.fail(f"wrote {path.name}: check it, then run the tests again")
        return
    diff = differences(json.loads(path.read_text()), got)
    assert not diff, (f"{page} differs from {path.name} in {len(diff)} places (SMPA_GOLDEN=update to accept):\n  "
                      + "\n  ".join(diff[:25]))
