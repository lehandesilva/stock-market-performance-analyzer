"""Streamlit dashboard. Start with `smpa dashboard`."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from smpa import ui
from smpa.analysis import compute, invested_over_time, load_prices, load_transactions, monthly
from smpa.config import load_config
from smpa.db import connect

st.set_page_config(page_title="Portfolio Monitor", layout="wide")
st.html(ui.CSS)
top = st.empty()  # header, filled in once the data is loaded

cfg = load_config()
conn = connect(cfg.db_path)
cur = f"{cfg.currency} " if cfg.currency else ""
num = st.column_config.NumberColumn


def chart(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", theme=None, config=ui.PLOTLY_CONFIG)


def pad_months(fig: go.Figure, months: pd.Series) -> go.Figure:
    """With only a few months of data, give monthly bars some context instead of a degenerate axis."""
    if len(months) and (months.max() - months.min()).days < 180:
        fig.update_xaxes(range=[months.min() - pd.DateOffset(months=2), months.max() + pd.DateOffset(months=3)],
                         tickformat="%b %Y", dtick="M1")
    return fig


# --- Data ---------------------------------------------------------------------
docs = pd.read_sql_query(
    """SELECT id, email_date, attachment_name, doc_type, layout, account, parse_count, extract_error, parse_error,
              path, text_path FROM documents ORDER BY email_date""",
    conn,
)
issues = pd.read_sql_query("SELECT subject AS reference, message FROM issues ORDER BY subject", conn)
cash = pd.read_sql_query("SELECT date, kind, ref, amount, description, sources FROM cash_movements ORDER BY date",
                         conn)
tx_all = load_transactions(conn, cfg.manual_csv)
prices = load_prices(conn, cfg.prices_csv)

account = next(iter(docs["account"].dropna()), "")
masked = "".join("•" if ch.isdigit() and 4 <= i < len(account) - 5 else ch for i, ch in enumerate(account))
fetched = prices["fetched_at"].dropna().max() if len(prices) else None
fetched_txt = (f"{pd.Timestamp(fetched).tz_convert('Asia/Colombo'):%d %b %Y %H:%M}" if isinstance(fetched, str)
               else "Not fetched")

if docs.empty:
    top.html(ui.header("No data", []))
    st.html(ui.empty("No statements yet. Configure config.toml, then run `smpa auth` and `smpa run` "
                     "(or `smpa import <folder>` for PDFs you already have)."))
    st.stop()

# --- Filters --------------------------------------------------------------------
if not tx_all.empty:
    c1, c2, _ = st.columns([1, 2, 1])
    min_d, max_d = tx_all["trade_date"].min().date(), tx_all["trade_date"].max().date()
    as_of = c1.date_input("As of", value=max_d, min_value=min_d, max_value=max_d)
    symbols = c2.multiselect("Filter tables by symbol", sorted(tx_all["symbol"].unique()))
    tx = tx_all[tx_all["trade_date"] <= pd.Timestamp(as_of)]
else:
    tx, symbols, as_of = tx_all, [], None

pf = compute(tx, dict(zip(prices["symbol"], prices["price"])))
h = pf.holdings.merge(prices[["symbol", "change_pct", "traded_at", "source"]], on="symbol", how="left")
h["day_change"] = h["change_pct"] / 100  # CSE reports percent units
h = h.drop(columns=["change_pct"])
unpriced = sorted(h.loc[h["price"].isna(), "symbol"])
flag_items = [(f"POS-{i:02d}", w) for i, w in enumerate(pf.warnings, 1)] + \
             [(f"DOC-{i:02d}", m) for i, m in enumerate(issues["message"], 1)]

# --- Header + readouts ------------------------------------------------------------
top.html(ui.header(
    f"NDB Securities · Account {masked} · {cfg.currency}",
    [("Trades through", f"{tx_all['trade_date'].max():%d %b %Y}" if len(tx_all) else "—", False),
     ("Prices", fetched_txt, not isinstance(fetched, str)),
     ("Status", f"● {len(flag_items)} flags" if flag_items else "● Nominal", bool(flag_items))],
))

mv, cost = h["market_value"].sum(min_count=1), h["cost_basis"].sum()
unreal = h["unrealized_pnl"].sum(min_count=1)
priced_cost = h.loc[h["market_value"].notna(), "cost_basis"].sum()
unreal_pct = unreal / priced_cost if pd.notna(unreal) and priced_cost else None
turnover = tx["gross_amount"].sum()
n_sells = int((tx["side"] == "SELL").sum())
st.html(ui.readouts([
    (f"Net invested · {cfg.currency}", pf.net_invested, "Buys − sell proceeds", False),
    (f"Open cost basis · {cfg.currency}", cost, f"{len(h)} position{'' if len(h) == 1 else 's'}", False),
    (f"Market value · {cfg.currency}", mv, f"{len(h) - len(unpriced)}/{len(h)} priced", bool(unpriced)),
    (f"Unrealized P&L · {cfg.currency}", unreal,
     "—" if unreal_pct is None else f"{'▲' if unreal_pct >= 0 else '▼'} {unreal_pct:+.2%} vs cost",
     unreal_pct is not None and unreal_pct < 0),
    (f"Realized P&L · {cfg.currency}", pf.realized_pnl, f"{n_sells} sell{'' if n_sells == 1 else 's'}", pf.realized_pnl < 0),
    (f"Fees paid · {cfg.currency}", pf.fees, f"{pf.fees / turnover:.2%} of turnover" if turnover else "—", False),
]))

# --- 01 Holdings network + 02 index ---------------------------------------------------
left, right = st.columns([3, 2], gap="medium")
value_note = "Node area = market value" + (f" · {len(unpriced)} unpriced, sized by cost" if unpriced else "")
with left, st.container(key="panel_network"):
    st.html(ui.section(1, "Holdings network", value_note))
    if h.empty:
        st.html(ui.empty("No open positions"))
    else:
        chart(ui.network(h, cur))
with right, st.container(key="panel_index"):
    st.html(ui.section(2, "Position index", "Weight · unrealized"))
    if h.empty:
        st.html(ui.empty("No open positions"))
    else:
        st.html(ui.position_index(h.assign(value=h["market_value"].fillna(h["cost_basis"])), "value"))

# --- 03 Capital deployed + 04 Realized + 05 Turnover ------------------------------------
if not tx.empty:
    left, right = st.columns(2, gap="medium")
    with left, st.container(key="panel_capital"):
        st.html(ui.section(3, "Capital deployed", f"Cumulative buys − sell proceeds · {cfg.currency}"))
        inv = invested_over_time(tx)
        inv = pd.concat([inv, pd.DataFrame([{"trade_date": pd.Timestamp(as_of),
                                             "net_invested": inv["net_invested"].iloc[-1]}])])
        fig = go.Figure(go.Scatter(
            x=inv["trade_date"], y=inv["net_invested"], mode="lines", line_shape="hv",
            line=dict(color=ui.INK, width=1.25), name="Net invested",
            hovertemplate="%{x|%d %b %Y}<br>" + cur + "%{y:,.2f}<extra></extra>",
        ))
        fig = ui.style(fig).update_layout(hovermode="x")
        fig.update_xaxes(showspikes=True, spikemode="across", spikethickness=1, spikecolor=ui.MUTED,
                         spikedash="solid", spikesnap="cursor")
        fig.update_yaxes(rangemode="tozero")
        chart(fig)

    with right, st.container(key="panel_realized"):
        st.html(ui.section(4, "Realized P&L", f"Per month · {cfg.currency}"))
        rm = monthly(pf.realized, "pnl")
        if rm.empty or not rm["pnl"].abs().gt(0.005).any():
            st.html(ui.empty("No realized gains or losses yet"))
        else:
            fig = go.Figure(go.Bar(
                x=rm["month"], y=rm["pnl"], xperiod="M1", xperiodalignment="middle",
                marker=dict(color=[ui.INK if v >= 0 else ui.ACCENT for v in rm["pnl"]], line=dict(width=0)),
                hovertemplate="%{x|%b %Y}<br>" + cur + "%{y:,.2f}<extra></extra>", name="Realized P&L",
            ))
            chart(pad_months(ui.style(fig), rm["month"]))

    with st.container(key="panel_turnover"):
        st.html(ui.section(5, "Turnover", f"Gross value traded per month · {cfg.currency}"))
        vol = monthly(tx, "gross_amount", by="side")
        fig = go.Figure()
        for side, color, label in (("BUY", ui.INK, "BUYS"), ("SELL", ui.SECOND, "SELLS")):
            d = vol[vol["side"] == side]
            fig.add_bar(x=d["month"], y=d["gross_amount"], name=label, marker=dict(color=color, line=dict(width=0)),
                        xperiod="M1", xperiodalignment="middle",
                        hovertemplate="%{x|%b %Y}<br>" + label + " " + cur + "%{y:,.2f}<extra></extra>")
        chart(pad_months(ui.style(fig, height=260, legend=True), vol["month"])
              .update_layout(barmode="group", bargroupgap=0.05))

# --- Flags ----------------------------------------------------------------------------
if flag_items:
    with st.expander(f"Flags · {len(flag_items)} — positions that don't add up, trades missing a document"):
        st.html(ui.flags(flag_items))


# --- Records --------------------------------------------------------------------------
def by_symbol(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["symbol"].isin(symbols)] if symbols else df


money = lambda label: num(label, format="%,.2f")  # noqa: E731
date_col = lambda label: st.column_config.DateColumn(label, format="YYYY-MM-DD")  # noqa: E731
tabs = st.tabs(["Holdings", "Transactions", "Realized", "Cash", "Documents"])
with tabs[0]:
    st.dataframe(by_symbol(h), hide_index=True, width="stretch", column_config={
        "symbol": "SYMBOL", "quantity": num("QTY", format="%,.0f"), "avg_cost": money("AVG COST"),
        "cost_basis": money("COST BASIS"), "price": money("PRICE"), "market_value": money("MKT VALUE"),
        "unrealized_pnl": money("UNREALIZED"), "unrealized_pct": num("UNREAL. %", format="percent"),
        "day_change": num("TODAY", format="percent"),
        "traded_at": st.column_config.DatetimeColumn("LAST TRADE", format="YYYY-MM-DD HH:mm"),
        "source": "PRICE SOURCE",
    })
with tabs[1]:
    st.dataframe(by_symbol(tx), hide_index=True, width="stretch", column_config={
        "trade_date": date_col("DATE"), "side": "SIDE", "symbol": "SYMBOL", "quantity": num("QTY", format="%,.0f"),
        "price": money("PRICE"), "gross_amount": money("GROSS"), "fees": money("FEES"), "net_amount": money("NET"),
        "contract_no": "CONTRACT", "sources": "SOURCES", "company": "COMPANY",
    })
with tabs[2]:
    st.dataframe(by_symbol(pf.realized), hide_index=True, width="stretch", column_config={
        "trade_date": date_col("DATE"), "symbol": "SYMBOL", "quantity": num("QTY", format="%,.0f"),
        "proceeds": money("PROCEEDS"), "cost": money("COST"), "pnl": money("P&L"),
    })
with tabs[3]:
    st.caption("Deposits and payments to your bank, from receipts and statements. Months without a statement "
               "before receipts started (2025) are missing here; trades are complete regardless.")
    st.dataframe(cash, hide_index=True, width="stretch", column_config={
        "date": "DATE", "kind": "KIND", "ref": "REFERENCE", "amount": money("AMOUNT"),
        "description": "DESCRIPTION", "sources": "SOURCES",
    })
with tabs[4]:
    n_err = int((docs["extract_error"].notna() | docs["parse_error"].notna()).sum())
    st.caption(f"{len(docs)} documents · {n_err} with errors · {len(issues)} document flags")
    st.dataframe(docs.drop(columns=["id", "path", "text_path", "account"]), hide_index=True, width="stretch",
                 column_config={"email_date": "EMAIL DATE", "attachment_name": "FILE", "doc_type": "TYPE",
                                "layout": "LAYOUT", "parse_count": num("RECORDS"), "extract_error": "EXTRACT ERROR",
                                "parse_error": "PARSE ERROR"})
    with_text = docs[docs["text_path"].notna()].set_index("id")
    if not with_text.empty:
        pick = st.selectbox("View extracted text", with_text.index, format_func=lambda i: (
            f"{with_text.loc[i, 'email_date']} · {with_text.loc[i, 'attachment_name']}"))
        st.code((cfg.data_dir / with_text.loc[pick, "text_path"]).read_text(), language=None)
