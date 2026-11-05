"""Market prices and price history from the Colombo Stock Exchange website's API.

The API is unofficial (reverse-engineered from www.cse.lk; see
github.com/GH0STH4CKER/Colombo-Stock-Exchange-CSE-API-Documentation), so the
response is checked: if its shape changes, PriceAPIError says so instead of
storing a wrong price.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import requests

from .config import Config

log = logging.getLogger(__name__)

SUMMARY_URL = "https://www.cse.lk/api/tradeSummary"  # every security traded today, one request
COMPANY_URL = "https://www.cse.lk/api/companyInfoSummery"  # sic; one security, last traded price
ASPI_URL = "https://www.cse.lk/api/aspiData"  # All Share Price Index, now
SNP_URL = "https://www.cse.lk/api/snpData"  # S&P Sri Lanka 20, now
SECTORS_URL = "https://www.cse.lk/api/allSectors"  # the 22 S&P/CSE industry group indices, now
INDEX_CHART_URL = "https://www.cse.lk/api/chartData"  # chartId=1: ASPI; period=5: daily, last year
STOCK_CHART_URL = "https://www.cse.lk/api/companyChartDataByStock"  # stockId (from companyInfoSummery), period=5
YEAR_PERIOD = "5"  # the longest period the chart endpoints answer; longer ones come back empty
HISTORY_MAX_AGE = 7  # days before a series' year of history is downloaded again (fills days not run)
ASPI = "ASPI"
SPSL20 = "SPSL20"
SECTOR = "sector:"  # index_history name prefix, e.g. sector:Banks
CHANGED = 0.005  # a re-downloaded close this far from the stored one is reported, not replaced silently
COLOMBO = timezone(timedelta(hours=5, minutes=30))  # CSE timestamps are instants; trading days are local
HEADERS = {"User-Agent": "smpa/0.1 (personal portfolio tracker)", "Accept": "application/json"}
TRANSIENT = {429, 500, 502, 503, 504}


class PriceAPIError(Exception):
    pass


@dataclass
class Quote:
    symbol: str
    price: float
    name: str | None = None
    change: float | None = None
    change_pct: float | None = None
    traded_at: str | None = None  # ISO time of the last trade, when known
    volume: float | None = None  # shares traded today, when known


@dataclass
class Extras:
    """What the CSE sends besides prices: kept for liquidity, betas, 52-week ranges and benchmarks."""

    market: list[tuple] = field(default_factory=list)  # daily_quotes rows: every stock that traded today
    info: dict[str, dict] = field(default_factory=dict)  # companyInfoSummery payloads by symbol


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def parse_trade_summary(payload) -> dict[str, Quote]:
    """Quotes from a tradeSummary response (only securities that traded today)."""
    rows = payload.get("reqTradeSummery") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) and "symbol" in r and "price" in r for r in rows):
        raise PriceAPIError("unexpected CSE tradeSummary response; the API may have changed")
    quotes = {}
    for r in rows:
        price = _num(r["price"])
        if price and price > 0:
            ts = _num(r.get("lastTradedTime"))
            traded_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(timespec="seconds") if ts else None
            quotes[r["symbol"]] = Quote(r["symbol"], price, r.get("name"), _num(r.get("change")),
                                        _num(r.get("percentageChange")), traded_at, _num(r.get("sharevolume")))
    return quotes


MARKET_FIELDS = ("open", "high", "low", "previousClose", "sharevolume", "tradevolume", "turnover", "marketCap",
                 "lastTradedTime")


def parse_market(payload, fetched_at: str) -> list[tuple]:
    """daily_quotes rows from a tradeSummary response: every stock that traded, on its trading day."""
    rows = parse_trade_summary_rows(payload)
    if rows and not all(k in r for r in rows for k in MARKET_FIELDS):
        gone = sorted({k for r in rows for k in MARKET_FIELDS if k not in r})
        raise PriceAPIError(f"tradeSummary rows have no {', '.join(gone)}; the API may have changed")
    out = []
    for r in rows:
        price, ts = _num(r["price"]), _num(r["lastTradedTime"])
        if price and price > 0 and ts:
            out.append((r["symbol"], _day(ts), _num(r["open"]), _num(r["high"]), _num(r["low"]), price,
                        _num(r["previousClose"]), _num(r["sharevolume"]), _num(r["tradevolume"]), _num(r["turnover"]),
                        _num(r["marketCap"]), fetched_at))
    return out


def parse_trade_summary_rows(payload) -> list[dict]:
    rows = payload.get("reqTradeSummery") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) and "symbol" in r and "price" in r for r in rows):
        raise PriceAPIError("unexpected CSE tradeSummary response; the API may have changed")
    return rows


# security_info column -> companyInfoSummery field (reqSymbolInfo, then reqSymbolBetaInfo)
INFO_FIELDS = {"name": "name", "isin": "isin", "cse_id": "id", "market_cap": "marketCap",
               "shares_issued": "quantityIssued", "hi_52w": "p12HiPrice", "lo_52w": "p12LowPrice",
               "volume_52w": "p12ShareVolume", "turnover_ytd": "ytdTurnover", "previous_close": "previousClose"}
BETA_FIELDS = {"beta_tri_aspi": "triASIBetaValue", "beta_spsl": "betaValueSPSL", "beta_period": "triASIBetaPeriod",
               "beta_quarter": "quarter"}


def parse_info(symbol: str, payload) -> dict | None:
    """A security_info row from a companyInfoSummery response; None if the CSE doesn't know the symbol."""
    info = (payload or {}).get("reqSymbolInfo") if isinstance(payload, dict) else None
    if not info:
        return None
    if gone := [v for v in ("p12HiPrice", "p12LowPrice", "marketCap") if v not in info]:
        raise PriceAPIError(f"companyInfoSummery for {symbol} has no {', '.join(gone)}; the API may have changed")
    beta = payload.get("reqSymbolBetaInfo") or {}
    row = {"symbol": symbol, **{k: info.get(v) for k, v in INFO_FIELDS.items()},
           **{k: beta.get(v) for k, v in BETA_FIELDS.items()}}
    for k in ("market_cap", "shares_issued", "hi_52w", "lo_52w", "volume_52w", "turnover_ytd", "previous_close",
              "beta_tri_aspi", "beta_spsl"):
        row[k] = _num(row[k])
    row["beta_period"] = None if row["beta_period"] is None else str(row["beta_period"])
    return row


def parse_quote(symbol: str, payload) -> Quote | None:
    """Quote from a companyInfoSummery response; None if the CSE has no price for it."""
    if payload is None or payload == {}:  # HTTP 404: unknown or delisted symbol
        return None
    if not isinstance(payload, dict) or "reqSymbolInfo" not in payload:
        keys = list(payload)[:10] if isinstance(payload, dict) else type(payload).__name__
        raise PriceAPIError(f"unexpected CSE API response for {symbol} (got {keys}); the API may have changed")
    info = payload["reqSymbolInfo"]
    if not info:  # unknown or delisted symbol
        return None
    if not isinstance(info, dict) or "lastTradedPrice" not in info:
        raise PriceAPIError(f"CSE API response for {symbol} has no lastTradedPrice; the API may have changed")
    if info.get("symbol") not in (None, symbol):
        raise PriceAPIError(f"asked the CSE API for {symbol} but got {info.get('symbol')}")
    price = _num(info["lastTradedPrice"])
    if price is None and info.get("lastTradedPrice") is None:
        price = _num(info.get("previousClose"))  # before today's first trade the CSE reports null
    if not price or price < 0:  # never traded / suspended
        return None
    return Quote(symbol, price, info.get("name"), _num(info.get("change")), _num(info.get("changePercentage")))


def _day(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=COLOMBO).date().isoformat()


def trading_day(iso: str) -> str:
    """The Colombo date of an ISO timestamp (CSE timestamps are instants)."""
    return datetime.fromisoformat(iso).astimezone(COLOMBO).date().isoformat()


def parse_aspi(payload, what: str = "aspiData") -> tuple[str, float]:
    """(trading day, level) from an aspiData (or snpData) response."""
    if not isinstance(payload, dict) or _num(payload.get("value")) is None or _num(payload.get("timestamp")) is None:
        raise PriceAPIError(f"unexpected CSE {what} response; the API may have changed")
    return _day(payload["timestamp"]), float(payload["value"])


def parse_sectors(payload) -> list[tuple[str, str, float]]:
    """(index_history name, trading day, level) for each S&P/CSE industry group index in allSectors.

    The response also has the ASPI and the S&P SL20 (they have no indexCode); those are stored on their own.
    """
    if not isinstance(payload, list) or not payload or not all(
            isinstance(r, dict) and {"name", "indexCode", "indexValue", "transactionTime"} <= set(r) for r in payload):
        raise PriceAPIError("unexpected CSE allSectors response; the API may have changed")
    out = []
    for r in payload:
        v, ts = _num(r["indexValue"]), _num(r["transactionTime"])
        if v and v > 0 and ts and r["name"] and r["indexCode"]:
            out.append((SECTOR + str(r["name"]).strip(), _day(ts), v))
    return out


def _series(rows, t: str, v: str, what: str) -> dict[str, float]:
    if not isinstance(rows, list) or not all(isinstance(r, dict) and t in r and v in r for r in rows):
        raise PriceAPIError(f"unexpected CSE chart response for {what}; the API may have changed")
    out = {}
    for r in rows:  # several points on one day: the last one is the close
        ts, val = _num(r[t]), _num(r[v])
        if ts and val and val > 0:
            out[_day(ts)] = val
    return out


def parse_index_chart(payload) -> dict[str, float]:
    """{day: level} from chartData: [{"d": ms, "v": level, "pc": %}, ...]."""
    return _series(payload, "d", "v", "the ASPI")


def parse_stock_chart(symbol: str, payload) -> dict[str, float]:
    """{day: closing price} from companyChartDataByStock: {"chartData": [{"t": ms, "p": price, ...}], "id": n}."""
    return {d: p for d, (p, _) in parse_stock_chart_rows(symbol, payload).items()}


def parse_stock_chart_rows(symbol: str, payload) -> dict[str, tuple[float, float | None]]:
    """{day: (closing price, shares traded)}; "q" is the day's share volume."""
    if not isinstance(payload, dict) or "chartData" not in payload:
        raise PriceAPIError(f"unexpected CSE chart response for {symbol}; the API may have changed")
    rows = payload["chartData"]
    closes = _series(rows, "t", "p", symbol)
    volume = {_day(r["t"]): _num(r.get("q")) for r in rows if _num(r.get("t"))}
    return {d: (p, volume.get(d)) for d, p in closes.items()}


def _post(session: requests.Session, url: str, data: dict, what: str, retries: int = 3):
    """POST to the CSE API. Returns parsed JSON, or None for HTTP 404."""
    for attempt in range(retries):
        try:
            r = session.post(url, data=data, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code in TRANSIENT and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == retries - 1:
                raise PriceAPIError(f"could not reach the CSE API ({what}): {exc}") from exc
            time.sleep(2 ** attempt)
        except requests.HTTPError as exc:
            raise PriceAPIError(f"CSE API returned HTTP {exc.response.status_code} ({what})") from exc
        except ValueError as exc:  # body isn't JSON
            raise PriceAPIError(f"CSE API response ({what}) is not JSON; the API may have changed") from exc
    raise PriceAPIError(f"CSE API kept failing ({what})")


def fetch_quotes(symbols: list[str], session: requests.Session | None = None,
                 delay: float = 0.3, extras: Extras | None = None) -> tuple[dict[str, Quote], list[str]]:
    """Returns (quotes, symbols without a price). Raises PriceAPIError on API problems.

    One tradeSummary request covers everything that traded today; the rest are
    looked up one by one (their last traded price may be from an earlier day).
    With `extras`, the rest of those responses is kept there.
    """
    session = session or requests.Session()
    if not symbols:
        return {}, []
    summary = _post(session, SUMMARY_URL, {}, "tradeSummary")
    today = parse_trade_summary(summary)
    if extras is not None:
        try:
            extras.market = parse_market(summary, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        except PriceAPIError as exc:  # extra data: never in the way of your prices
            log.warning("market data not kept: %s", exc)
    quotes = {s: today[s] for s in symbols if s in today}
    missing = []
    for i, sym in enumerate(s for s in symbols if s not in today):
        if i and delay:
            time.sleep(delay)  # be gentle with the CSE website
        payload = _post(session, COMPANY_URL, {"symbol": sym}, sym)
        q = parse_quote(sym, payload)
        if extras is not None and payload:
            extras.info[sym] = payload
        if q:
            quotes[sym] = q
        else:
            missing.append(sym)
    return quotes, missing


def held_symbols(cfg: Config, conn: sqlite3.Connection, since: date | None = None) -> list[str]:
    """Symbols held now, or (with `since`) at any time from that day on."""
    from .actions import load_actions
    from .analysis import compute, held_between, load_transactions

    actions = load_actions(cfg.actions_csv)
    tx = load_transactions(conn, cfg.manual_csv, actions)
    if since is not None:
        return held_between(tx, actions, since)
    return sorted(compute(tx, actions=actions).holdings["symbol"])


def update_prices(cfg: Config, conn: sqlite3.Connection, session: requests.Session | None = None,
                  delay: float = 0.3) -> tuple[int, list[str]]:
    """Fetch and store prices for current holdings. Returns (updated, symbols without a price).

    Also keeps what the same responses say beyond prices (every stock's day, betas, 52-week ranges) and the
    day's S&P SL20 and sector index levels. Those are extras: a problem with them is logged, never fatal.
    """
    session = session or requests.Session()
    held = held_symbols(cfg, conn)
    extras = Extras()
    quotes, missing = fetch_quotes(held, session, delay, extras)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.executemany(
        "INSERT OR REPLACE INTO prices (symbol, name, price, change, change_pct, traded_at, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(q.symbol, q.name, q.price, q.change, q.change_pct, q.traded_at, now) for q in quotes.values()],
    )
    # One close per stock per trading day, dated by its last trade. Stocks looked up one by one haven't
    # traded today, so their price is an older close (the weekly download has it); storing it under
    # today's date would invent a trading day, e.g. on a weekend or a Poya day.
    conn.executemany(
        "INSERT OR REPLACE INTO price_history (symbol, date, price, volume, fetched_at) VALUES (?, ?, ?, ?, ?)",
        [(q.symbol, trading_day(q.traded_at), q.price, q.volume, now) for q in quotes.values() if q.traded_at],
    )
    try:  # the benchmark is extra: a problem with it shouldn't block your own prices
        day, level = parse_aspi(_post(session, ASPI_URL, {}, "aspiData"))
        conn.execute("INSERT OR REPLACE INTO index_history (name, date, value, fetched_at) VALUES (?, ?, ?, ?)",
                     (ASPI, day, level, now))
    except PriceAPIError as exc:
        log.warning("ASPI not updated: %s", exc)
    conn.commit()
    _store_extras(conn, session, extras, [s for s in held if s not in missing], delay, now)
    for sym in missing:
        log.warning("no CSE price for %s (unknown, delisted or not traded yet)", sym)
    log.info("prices updated for %d symbols", len(quotes))
    return len(quotes), missing


def _store_extras(conn: sqlite3.Connection, session: requests.Session, extras: Extras, held: list[str],
                  delay: float, now: str) -> None:
    """daily_quotes, security_info for every holding (asked once a day), S&P SL20 and sector levels."""
    today = trading_day(now)
    conn.executemany(f"INSERT OR REPLACE INTO daily_quotes VALUES ({','.join('?' * 12)})", extras.market)
    asked = {r[0] for r in conn.execute("SELECT symbol FROM security_info WHERE fetched_on = ?", (today,))}
    for sym in held:
        try:
            payload = extras.info.get(sym)
            if payload is None:
                if sym in asked:
                    continue
                time.sleep(delay)
                payload = _post(session, COMPANY_URL, {"symbol": sym}, sym)
            store_info(conn, sym, payload, today)
        except PriceAPIError as exc:
            log.warning("security info not kept: %s", exc)
    for what, url in (("snpData", SNP_URL), ("allSectors", SECTORS_URL)):
        try:
            payload = _post(session, url, {}, what)
            rows = [(SPSL20, *parse_aspi(payload, what))] if what == "snpData" else parse_sectors(payload)
            conn.executemany("INSERT OR REPLACE INTO index_history (name, date, value, fetched_at) VALUES (?, ?, ?, ?)",
                             [(*r, now) for r in rows])
        except PriceAPIError as exc:
            log.warning("%s not kept: %s", what, exc)
    conn.commit()


def store_info(conn: sqlite3.Connection, symbol: str, payload, day: str) -> None:
    row = parse_info(symbol, payload)
    if row:
        cols = ["symbol", "fetched_on", *INFO_FIELDS, *BETA_FIELDS]
        conn.execute(f"INSERT OR REPLACE INTO security_info ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [symbol, day, *[row[c] for c in cols[2:]]])


def _replace_history(conn: sqlite3.Connection, table: str, key: str, series: str, points: dict[str, tuple],
                     today: str) -> int:
    """Store downloaded (value, volume) points, reporting closes that differ from the ones already stored.

    The CSE could correct a close, or start adjusting its history for splits; either way stored prices
    would change without anyone noticing. Returns how many changed by more than CHANGED.
    """
    value = "price" if table == "price_history" else "value"
    old = dict(conn.execute(f"SELECT date, {value} FROM {table} WHERE {key} = ?", (series,)).fetchall())
    changed = [(d, old[d], v) for d, (v, _) in points.items() if d in old and abs(v / old[d] - 1) > CHANGED]
    conn.executemany("INSERT OR IGNORE INTO history_changes (series, date, old, new, noticed_on) VALUES (?, ?, ?, ?, ?)",
                     [(series, d, a, b, today) for d, a, b in changed])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if table == "price_history":
        conn.executemany("INSERT OR REPLACE INTO price_history (symbol, date, price, volume, fetched_at) "
                         "VALUES (?, ?, ?, ?, ?)", [(series, d, v, q, now) for d, (v, q) in points.items()])
    else:
        conn.executemany("INSERT OR REPLACE INTO index_history (name, date, value, fetched_at) VALUES (?, ?, ?, ?)",
                         [(series, d, v, now) for d, (v, _) in points.items()])
    if changed:
        log.warning("the CSE now reports %d stored closes of %s differently (e.g. %s: %s -> %s); a correction, "
                    "or history adjusted for a split?", len(changed), series, *changed[0])
    return len(changed)


def update_history(cfg: Config, conn: sqlite3.Connection, session: requests.Session | None = None,
                   delay: float = 0.3, today: date | None = None) -> tuple[int, list[str]]:
    """Download a year of daily closes for the ASPI and every stock held during that year.

    Each series is downloaded again after HISTORY_MAX_AGE days, which fills in days `smpa prices`
    wasn't run. Returns (series downloaded, problems). Problems are logged, never fatal: history only
    feeds the value chart and the ASPI comparison.
    """
    session = session or requests.Session()
    today = today or date.today()
    fresh = {r[0] for r in conn.execute("SELECT series FROM history_fetches WHERE fetched_on > ?",
                                        ((today - timedelta(days=HISTORY_MAX_AGE)).isoformat(),))}
    wanted = [f"index:{ASPI}"] + held_symbols(cfg, conn, since=today - timedelta(days=366))
    done, problems = 0, []
    for i, series in enumerate(s for s in wanted if s not in fresh):
        if i and delay:
            time.sleep(delay)
        try:
            if series.startswith("index:"):
                points = parse_index_chart(_post(session, INDEX_CHART_URL,
                                                 {"symbol": "", "chartId": "1", "period": YEAR_PERIOD}, "ASPI chart"))
                _replace_history(conn, "index_history", "name", ASPI, {d: (v, None) for d, v in points.items()},
                                 today.isoformat())
            else:
                info = _post(session, COMPANY_URL, {"symbol": series}, series)
                sec_id = ((info or {}).get("reqSymbolInfo") or {}).get("id")
                if not isinstance(sec_id, int):
                    problems.append(f"{series}: the CSE doesn't know this symbol (delisted?)")
                    continue
                try:
                    store_info(conn, series, info, today.isoformat())
                except PriceAPIError as exc:
                    log.warning("security info not kept: %s", exc)
                time.sleep(delay)
                rows = parse_stock_chart_rows(series, _post(session, STOCK_CHART_URL,
                                                            {"stockId": str(sec_id), "period": YEAR_PERIOD}, series))
                _replace_history(conn, "price_history", "symbol", series, rows, today.isoformat())
        except PriceAPIError as exc:
            problems.append(f"{series}: {exc}")
            continue
        conn.execute("INSERT OR REPLACE INTO history_fetches (series, fetched_on) VALUES (?, ?)",
                     (series, today.isoformat()))
        conn.commit()
        done += 1
    for p in problems:
        log.warning("price history: %s", p)
    return done, problems
