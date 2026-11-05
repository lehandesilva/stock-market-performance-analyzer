"""FIFO lots: how old your shares are, and which buys each sale came from.

A CDS account holds only a count of each share, so the official numbers use average cost (`analysis._Ledger`).
Lots are a second view built alongside it, from the same events, for what average cost can't show: the age
of the shares you hold, round trips (each sale matched to the buys it came from, oldest first), and what
became of the exact shares you held on a given day. Average-cost totals never depend on them.

Invariant, checked after every event: a symbol's lots add up to the ledger's share count.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .analysis import NON_MARKET, _events, _Ledger


@dataclass
class Lot:
    id: int
    symbol: str
    date: pd.Timestamp  # bought (or credited, for a bonus on shares not otherwise held)
    quantity: float  # still held; scaled by splits and bonus issues
    cost: float  # what those shares cost, buy fees included
    fees: float  # the buy fees in `cost`

    @property
    def cost_per_share(self) -> float:
        return self.cost / self.quantity if self.quantity else 0.0


@dataclass
class Trip:
    """Part of a sale matched to one lot (a round trip). `lot` is None for shares sold that no buy in the data
    explains (missing statements); like the ledger, those count at zero gain."""

    symbol: str
    lot: int | None
    bought: pd.Timestamp | None
    sold: pd.Timestamp
    quantity: float
    cost: float  # incl. buy fees
    proceeds: float  # after sell fees
    buy_fees: float
    sell_fees: float
    market: bool  # a sale on the exchange, not a buyout

    @property
    def pnl(self) -> float:
        return self.proceeds - self.cost

    @property
    def days(self) -> int | None:
        return (self.sold - self.bought).days if self.bought is not None else None


@dataclass
class Lots:
    open: dict[str, list[Lot]] = field(default_factory=dict)
    trips: list[Trip] = field(default_factory=list)
    _next: int = 0

    def held(self, symbol: str) -> float:
        return sum(lot.quantity for lot in self.open.get(symbol, []))

    def buy(self, symbol: str, date, quantity: float, cost: float, fees: float = 0.0) -> Lot:
        self._next += 1
        lot = Lot(self._next, symbol, pd.Timestamp(date), float(quantity), float(cost), float(fees))
        self.open.setdefault(symbol, []).append(lot)
        return lot

    def sell(self, symbol: str, date, quantity: float, proceeds: float, fees: float = 0.0,
             market: bool = True) -> list[Trip]:
        """Take `quantity` from the oldest lots first; proceeds and fees are split by shares."""
        left, out, lots = float(quantity), [], self.open.get(symbol, [])
        share = lambda k: k / quantity if quantity else 0.0  # noqa: E731
        while left > 1e-9 and lots:
            lot = lots[0]
            k = min(lot.quantity, left)
            part = k / lot.quantity
            cost, bfees = lot.cost * part, lot.fees * part
            out.append(Trip(symbol, lot.id, lot.date, pd.Timestamp(date), k, cost, proceeds * share(k), bfees,
                            fees * share(k), market))
            lot.quantity, lot.cost, lot.fees, left = lot.quantity - k, lot.cost - cost, lot.fees - bfees, left - k
            if lot.quantity <= 1e-9:
                lots.pop(0)
        if left > 1e-9:  # sold more than the data shows was bought
            out.append(Trip(symbol, None, None, pd.Timestamp(date), left, proceeds * share(left),
                            proceeds * share(left), 0.0, fees * share(left), market))
        self.trips += out
        return out

    def resize(self, symbol: str, date, total: float) -> None:
        """A split or bonus issue left `total` shares: scale every lot, keeping its cost.

        Each lot rounds down, as the ledger does, and the newest takes the difference so the lots still add
        up. Shares credited when none were held (rights entered as a BONUS) become a lot that cost nothing.
        """
        lots = self.open.get(symbol, [])
        have = sum(lot.quantity for lot in lots)
        if have <= 1e-9:
            if total > 1e-9:
                self.buy(symbol, date, total, 0.0)
            return
        k = total / have
        for lot in lots:
            lot.quantity = math.floor(lot.quantity * k + 1e-9)
        lots[-1].quantity += total - sum(lot.quantity for lot in lots)
        self.open[symbol] = [lot for lot in lots if lot.quantity > 1e-9]

    def apply(self, kind: int, r, total: float) -> None:
        """One event from `analysis._events`; `total` is the ledger's share count after it."""
        if kind == 1:
            if r.side == "BUY":
                self.buy(r.symbol, r.trade_date, r.quantity, r.net_amount, r.fees)
            else:
                self.sell(r.symbol, r.trade_date, r.quantity, r.net_amount, r.fees,
                          market=getattr(r, "sources", None) not in NON_MARKET)
        elif r.action in ("SPLIT", "BONUS"):
            self.resize(r.symbol, r.date, total)


class LotError(AssertionError):
    pass


def replay(tx: pd.DataFrame, actions: pd.DataFrame | None, until=None, on_event=None) -> tuple[_Ledger, Lots]:
    """The ledger and the lots after every event up to and including `until` (all of them if None).

    `on_event(date, kind, row, book, lots)` is called before each event is applied.
    """
    book, lots = _Ledger(), Lots()
    until = pd.Timestamp(until) if until is not None else None
    for d, kind, r in _events(tx, actions):
        if until is not None and d > until:
            break
        if on_event:
            on_event(d, kind, r, book, lots)
        book.apply(kind, r)
        total = book.pos.get(r.symbol, (0.0, 0.0))[0]
        lots.apply(kind, r, total)
        if abs(lots.held(r.symbol) - total) > 1e-6:
            raise LotError(f"{r.symbol} on {d:%Y-%m-%d}: lots hold {lots.held(r.symbol):g}, the ledger {total:g}")
    return book, lots


def open_lots(lots: Lots, symbol: str | None, today) -> pd.DataFrame:
    """symbol, bought, quantity, cost, cost_per_share, age_days of every open lot."""
    today = pd.Timestamp(today)
    rows = [{"symbol": lot.symbol, "bought": lot.date, "quantity": lot.quantity, "cost": lot.cost,
             "cost_per_share": lot.cost_per_share, "age_days": (today - lot.date).days}
            for sym, ls in sorted(lots.open.items()) if symbol in (None, sym) for lot in ls]
    return pd.DataFrame(rows, columns=["symbol", "bought", "quantity", "cost", "cost_per_share", "age_days"])


def average_age(lots: pd.DataFrame) -> float | None:
    """Days the shares held have been held, weighted by quantity."""
    q = lots["quantity"].sum() if len(lots) else 0
    return float((lots["age_days"] * lots["quantity"]).sum() / q) if q else None


def trips_frame(trips: list[Trip]) -> pd.DataFrame:
    cols = ["symbol", "bought", "sold", "quantity", "cost", "proceeds", "buy_fees", "sell_fees", "pnl", "days",
            "market", "matched"]
    rows = [{"symbol": t.symbol, "bought": t.bought, "sold": t.sold, "quantity": t.quantity, "cost": t.cost,
             "proceeds": t.proceeds, "buy_fees": t.buy_fees, "sell_fees": t.sell_fees, "pnl": t.pnl, "days": t.days,
             "market": t.market, "matched": t.lot is not None} for t in trips]
    return pd.DataFrame(rows, columns=cols)
