"""Laws the maths must obey for any trades, not just the examples (docs/06 §7), checked with Hypothesis.

Each test generates random trade sequences and looks for one that breaks the rule. Laws 5 (as_of on the last
date changes nothing) and 7 (the fixtures' fee rates) need the fixture database: see test_web.py and
test_fees.py.
"""

import pandas as pd
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from conftest import make_actions, make_tx
from smpa.analysis import benchmark, compute, value_history, xirr
from smpa.lots import replay
from smpa.performance import direct_alpha, ks_pme, level_series, twr_index

SYMBOLS = ["A.N0000", "B.N0000", "C.N0000"]
SLOW = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
D0 = pd.Timestamp("2024-01-02")


@st.composite
def trades(draw, splits=False):
    """Buys and sells (never more than held) across a few symbols, days apart; optionally splits and bonuses."""
    held = dict.fromkeys(SYMBOLS, 0)
    rows, acts, day = [], [], D0
    for _ in range(draw(st.integers(1, 20))):
        day += pd.Timedelta(days=draw(st.integers(1, 40)))
        sym = draw(st.sampled_from(SYMBOLS))
        kind = draw(st.sampled_from(["buy", "sell", "split"] if splits else ["buy", "sell"]))
        if kind == "split" and held[sym]:
            new, old = draw(st.sampled_from([(2, 1), (3, 2), (1, 2), (5, 1)]))
            action = draw(st.sampled_from(["SPLIT", "BONUS"]))
            acts.append({"date": day.strftime("%Y-%m-%d"), "symbol": sym, "action": action,
                         "ratio_new": float(new), "ratio_old": float(old)})
            held[sym] = int(held[sym] * new / old + 1e-9) if action == "SPLIT" else held[sym] + int(held[sym] * new / old + 1e-9)
            continue
        price = draw(st.integers(100, 50_000)) / 100
        if kind == "sell" and held[sym]:
            q = draw(st.integers(1, held[sym]))
            held[sym] -= q
            rows.append((day, "SELL", sym, q, price))
        else:
            q = draw(st.integers(1, 1000))
            held[sym] += q
            rows.append((day, "BUY", sym, q, price))
    fee = draw(st.sampled_from([0.0, 0.0112, 0.01132]))
    return make_tx(rows, fee=fee), make_actions(acts), held


prices = st.dictionaries(st.sampled_from(SYMBOLS), st.integers(100, 50_000).map(lambda c: c / 100), min_size=3)


@given(trades(), prices)
@settings(deadline=None)
def test_1_cash_identity(t, px):
    """realized + unrealized + dividends = value + sales + dividends − buys (no sale bigger than the shares held)."""
    tx, _, _ = t
    pf = compute(tx, px)
    unreal = float(pf.holdings["unrealized_pnl"].sum())
    value = float(pf.holdings["market_value"].sum())
    assert pf.realized_pnl + unreal == pytest.approx(value - pf.net_invested, abs=1e-6)
    assert not pf.warnings or all("price went" in w for w in pf.warnings)  # only split hints, never a short sale


@given(trades(), prices)
@settings(max_examples=50, deadline=None)
def test_2_every_stocks_pnl_adds_up_to_the_portfolios(t, px):
    tx, _, _ = t
    total = compute(tx, px)
    whole = total.realized_pnl + float(total.holdings["unrealized_pnl"].sum())
    parts = 0.0
    for sym in SYMBOLS:
        pf = compute(tx[tx["symbol"] == sym], px)
        parts += pf.realized_pnl + float(pf.holdings["unrealized_pnl"].sum())
    assert parts == pytest.approx(whole, abs=1e-6)


@given(st.integers(1, 2000), st.integers(100, 10_000), st.lists(st.floats(-0.04, 0.04), min_size=20, max_size=60))
@SLOW
def test_3_a_single_buy_held_twr_is_the_price_change(q, cents, moves):
    """One buy held to the end: TWR = the price change from the price paid; XIRR = its annualized version."""
    p = cents / 100
    days = pd.bdate_range(D0, periods=len(moves))
    closes = [p]
    for m in moves[1:]:
        closes.append(round(closes[-1] * (1 + m), 2) or 0.01)
    ph = pd.DataFrame({"symbol": "A.N0000", "date": days, "price": closes})
    tx = make_tx([(D0, "BUY", "A.N0000", q, p)], fee=0)
    hist = value_history(tx, None, ph, pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"), "value": []}), days[-1])
    g = twr_index(hist, [(D0, -q * p)])
    assert g.iloc[-1] == pytest.approx(closes[-1] / p, rel=1e-9)
    span = (days[-1] - D0).days
    expected = (closes[-1] / p) ** (365 / span) - 1 if span else None
    assume(expected is not None and -0.999 < expected < 1e5)  # xirr gives up (None) on absurd rates from a few days
    r = xirr([(D0, -q * p), (days[-1], q * closes[-1])])
    assert r == pytest.approx(expected, rel=1e-6, abs=1e-6)


@given(st.lists(st.floats(-0.03, 0.03), min_size=5, max_size=80),
       st.lists(st.tuples(st.integers(0, 79), st.integers(-50, 100)), min_size=1, max_size=8))
@SLOW
def test_4_a_portfolio_that_is_the_index(moves, trades_):
    """Buying and selling index units at the index level: direct alpha 0, KS-PME 1, and the same money in the
    index is the portfolio itself."""
    days = pd.bdate_range(D0, periods=len(moves))
    level = pd.Series(1000 * (1 + pd.Series(moves)).cumprod().to_numpy(), index=days)
    units, flows, held = 10.0, [(days[0], -10.0 * level.iloc[0])], {}
    for k, u in sorted(trades_):
        k = min(k, len(days) - 1)
        u = max(u, -units + 1)  # never sell everything: keep a final value
        if k == 0 or not u:
            continue
        units += u
        flows.append((days[k], -u * level.iloc[k]))
        held[days[k]] = units
    end = days[-1]
    final = units * level.iloc[-1]
    assert ks_pme(flows, final, end, level) == pytest.approx(1.0, rel=1e-9)
    assert direct_alpha(flows, final, end, level) == pytest.approx(0.0, abs=1e-6)
    path = pd.Series(held).reindex(days).ffill().fillna(10.0)
    hist = pd.DataFrame({"date": days, "value": (path * level).to_numpy(), "index": level.to_numpy(),
                         "fresh": [True] + [False] * (len(days) - 1)})
    ih = pd.DataFrame({"date": days, "value": level.to_numpy()})
    equiv, b = benchmark(hist, flows, ih)
    assert equiv.to_numpy() == pytest.approx(hist["value"].to_numpy(), rel=1e-9)
    assert b["direct_alpha"] == pytest.approx(0.0, abs=1e-6) and level_series(ih).index[0] == days[0]


@given(trades(splits=True))
@settings(deadline=None)
def test_6_lots_always_match_the_ledger(t):
    """replay() checks lots against the ledger's share count after every event, splits and bonuses included."""
    tx, actions, held = t
    book, lots = replay(tx, actions)
    for sym in SYMBOLS:
        assert lots.held(sym) == pytest.approx(book.pos.get(sym, (0.0, 0.0))[0])
        assert book.pos.get(sym, (0.0, 0.0))[0] == pytest.approx(held[sym])
