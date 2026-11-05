"""Laws the maths must obey for any trades, not just the examples (docs/06 §7), checked with Hypothesis.

Hypothesis makes up random trade sequences, prices and index paths, and shrinks any that break a law to the
smallest one that does. Law 7 (the fee rates in the fixtures) is an exact example: tests/test_fees.py.
"""

import pandas as pd
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from conftest import fixture_db, make_actions, make_tx
from smpa.analysis import benchmark, compute, value_history, xirr
from smpa.lots import open_lots, replay, trips_frame
from smpa.performance import direct_alpha, ks_pme, level_series, twr_index, twr_return, annualize

SYMBOLS = ("AAA.N0000", "BBB.N0000", "CCC.N0000")
START = pd.Timestamp("2024-01-02")
price = st.decimals(min_value="0.50", max_value="500.00", places=2).map(float)


@st.composite
def histories(draw, with_actions=True):
    """Trades that never sell more than is held, with splits, bonus issues and dividends between them."""
    held = dict.fromkeys(SYMBOLS, 0)
    trades, actions, day = [], [], START
    for _ in range(draw(st.integers(1, 14))):
        day += pd.Timedelta(days=draw(st.integers(0, 45)))
        sym = draw(st.sampled_from(SYMBOLS))
        kind = draw(st.sampled_from(["trade", "trade", "trade", "action"])) if with_actions else "trade"
        if kind == "action" and held[sym] > 0:
            what = draw(st.sampled_from(["SPLIT", "BONUS", "DIVIDEND"]))
            if what == "DIVIDEND":
                actions.append({"date": day, "symbol": sym, "action": "DIVIDEND", "per_share": draw(price) / 10})
            else:  # only ones that add shares, so a later sale is never more than is held
                new, old = draw(st.integers(1, 5)), draw(st.integers(1, 5))
                if what == "SPLIT":
                    new = max(new, old)
                actions.append({"date": day, "symbol": sym, "action": what, "ratio_new": new, "ratio_old": old})
                held[sym] = held[sym] * new // old if what == "SPLIT" else held[sym] + held[sym] * new // old
            continue
        if held[sym] > 0 and draw(st.booleans()):
            q = draw(st.integers(1, held[sym]))
            trades.append((day, "SELL", sym, q, draw(price)))
            held[sym] -= q
        else:
            q = draw(st.integers(1, 2_000))
            trades.append((day, "BUY", sym, q, draw(price)))
            held[sym] += q
    assume(trades)
    tx = make_tx(trades, fee=draw(st.sampled_from([0.0, 0.0112, 0.01132])))
    # a same-day sale and split can't be ordered by these rules alone: keep days with both apart
    assume(not {(d, s) for d, _, s, _, _ in trades} & {(a["date"], a["symbol"]) for a in actions})
    return tx, make_actions(actions)


def priced(tx, data):
    return {s: data.draw(price) for s in sorted(set(tx["symbol"]))}


@given(histories(), st.data())
def test_cash_identity(h, data):
    """1. realized + unrealized + dividends = value + sales + dividends − buys (no sale bigger than the holding)."""
    tx, actions = h
    pf = compute(tx, priced(tx, data), actions)
    assert not pf.warnings or all("price went from" in w for w in pf.warnings)  # never "sold more than held"
    buys, sales = tx.loc[tx.side == "BUY", "net_amount"].sum(), tx.loc[tx.side == "SELL", "net_amount"].sum()
    lhs = pf.realized_pnl + pf.holdings["unrealized_pnl"].sum() + pf.dividend_income
    rhs = pf.holdings["market_value"].sum() + sales + pf.dividend_income - buys
    assert lhs == pytest.approx(rhs, abs=1e-6)


@given(histories(), st.data())
def test_every_stock_adds_up_to_the_portfolio(h, data):
    """2. Each symbol on its own gives the same realized, unrealized and dividends as the portfolio has for it."""
    tx, actions = h
    prices = priced(tx, data)
    whole = compute(tx, prices, actions)
    total = 0.0
    for sym in sorted(set(tx["symbol"])):
        one = compute(tx[tx.symbol == sym], prices, actions[actions.symbol == sym])
        total += one.realized_pnl + one.dividend_income + one.holdings["unrealized_pnl"].sum()
    assert total == pytest.approx(whole.realized_pnl + whole.dividend_income + whole.holdings["unrealized_pnl"].sum(),
                                  abs=1e-6)


@given(histories(), st.data())
def test_lots_always_match_the_ledger_and_fifo_changes_no_total(h, data):
    """6. Lot quantities add up to the ledger's after every event (replay checks it), and FIFO and average cost
    split the gain between realized and unrealized differently but never change their sum."""
    tx, actions = h
    prices = priced(tx, data)
    book, lots = replay(tx, actions)  # raises LotError the moment they differ
    held = open_lots(lots, None, "2030-01-01")
    for sym, (q, _) in book.held().items():
        assert held.loc[held.symbol == sym, "quantity"].sum() == pytest.approx(q)
    pf = compute(tx, prices, actions)
    fifo = trips_frame(lots.trips)["pnl"].sum() + sum(
        r.quantity * prices[r.symbol] - r.cost for r in held.itertuples())
    assert fifo == pytest.approx(pf.realized_pnl + pf.holdings["unrealized_pnl"].sum(), abs=1e-6)


@given(price, st.lists(st.floats(min_value=0.3, max_value=3.0), min_size=60, max_size=300), st.integers(1, 5_000))
def test_one_buy_held_twr_is_the_price_change_and_xirr_its_yearly_rate(first, moves, q):
    """3. Bought at the first close (no fees) and held: the time-weighted return is the price change, and the
    money-weighted return is that change as a yearly rate. (Three months or more, and moves within 0.3x to 3x,
    keep the yearly rate inside what xirr searches: from -99.99% up to a million.)"""
    closes = [first] + [round(first * m, 2) or 0.01 for m in moves]
    days = pd.bdate_range(START, periods=len(closes))
    tx = make_tx([(days[0], "BUY", "AAA.N0000", q, closes[0])], fee=0)
    ph = pd.DataFrame({"symbol": "AAA.N0000", "date": days, "price": closes})
    hist = value_history(tx, None, ph, pd.DataFrame({"date": pd.DatetimeIndex([]), "value": []}), days[-1])
    tw = twr_return(twr_index(hist, [(days[0], -q * closes[0])]))
    assert tw["growth"] == pytest.approx(closes[-1] / closes[0])
    rate = xirr([(days[0], -q * closes[0]), (days[-1], q * closes[-1])])
    assert rate == pytest.approx(annualize(tw["growth"], tw["days"]), rel=1e-6, abs=1e-6)


@st.composite
def index_portfolio(draw):
    """Index levels on business days, and buys and sales of index units at those levels."""
    n = draw(st.integers(5, 60))
    levels = draw(st.lists(st.floats(min_value=500, max_value=20_000, allow_nan=False), min_size=n, max_size=n))
    days = pd.bdate_range(START, periods=n)
    units, flows, value = 0.0, [], []
    for i, (d, lv) in enumerate(zip(days, levels)):
        if i == 0 or draw(st.integers(0, 4)) == 0:
            if units > 0 and draw(st.booleans()):
                k = units * draw(st.floats(min_value=0.05, max_value=0.9))
                flows.append((d, k * lv))
                units -= k
            else:
                amount = draw(st.floats(min_value=100, max_value=100_000))
                flows.append((d, -amount))
                units += amount / lv
        value.append(units * lv)
    return days, levels, flows, value


@given(index_portfolio())
def test_a_portfolio_that_is_the_index_has_no_edge(p):
    """4. Buying and selling the index itself: direct alpha 0, KS-PME 1, and the ASPI line is the value."""
    days, levels, flows, value = p
    level = level_series(pd.DataFrame({"date": days, "value": levels}))
    assert ks_pme(flows, value[-1], days[-1], level) == pytest.approx(1.0, rel=1e-9)
    alpha = direct_alpha(flows, value[-1], days[-1], level)
    assert alpha is None or alpha == pytest.approx(0.0, abs=1e-6)  # None: nothing left and no sale, say
    hist = pd.DataFrame({"date": days, "value": value, "index": levels, "fresh": [True] + [False] * (len(days) - 1)})
    equiv, b = benchmark(hist, flows, pd.DataFrame({"date": days, "value": levels}))
    assert equiv.tolist() == pytest.approx(value, rel=1e-9, abs=1e-6) and b["emptied"] is None


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    return fixture_db(tmp_path_factory.mktemp("laws"))


@settings(max_examples=8)
@given(st.lists(st.tuples(st.sampled_from(["AAA.N0000", "BBB.N0000", "CCC.N0000", "DDD.N0000"]),
                          st.integers(0, 400), price), min_size=1, max_size=40),
       st.lists(st.tuples(st.integers(0, 400), st.floats(min_value=5_000, max_value=30_000)), max_size=20))
def test_as_of_the_newest_day_is_the_latest_view(parsed, closes, aspi):
    """5. Asking for the newest day there is a value for gives exactly the payload asked for no day."""
    from smpa.db import connect
    from smpa.web.api import build_payload

    day = lambda k: (pd.Timestamp("2025-12-01") + pd.Timedelta(days=k)).strftime("%Y-%m-%d")  # noqa: E731
    with connect(parsed.db_path) as conn:
        conn.execute("DELETE FROM price_history")
        conn.execute("DELETE FROM index_history")
        conn.executemany("INSERT OR REPLACE INTO price_history (symbol, date, price) VALUES (?, ?, ?)",
                         [(s, day(k), p) for s, k, p in closes])
        conn.executemany("INSERT OR REPLACE INTO index_history (name, date, value) VALUES ('ASPI', ?, ?)",
                         [(day(k), v) for k, v in aspi])
        conn.commit()
        now = build_payload(parsed, conn)
        assert build_payload(parsed, conn, now["meta"]["max_date"]) == now
