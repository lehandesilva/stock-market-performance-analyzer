"""Streamlit dashboard. Start with `smpa dashboard`."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from smpa.analysis import compute, invested_over_time, load_prices, load_transactions, monthly
from smpa.config import load_config
from smpa.db import connect

st.set_page_config(page_title="Portfolio performance", layout="wide")

# Validated categorical/diverging steps, per theme (light, dark).
DARK = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"
BLUE = "#3987e5" if DARK else "#2a78d6"  # series 1 / gain pole
ORANGE = "#d95926" if DARK else "#eb6834"  # series 2
RED = "#e66767" if DARK else "#e34948"  # loss pole

cfg = load_config()
conn = connect(cfg.db_path)
cur = f"{cfg.currency} " if cfg.currency else ""


def money(x: float | None) -> str:
    return "—" if x is None or pd.isna(x) else f"{cur}{x:,.2f}"


def style(fig: go.Figure, height: int = 320) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8), barcornerradius=4, bargap=0.35,
        legend=dict(orientation="h", y=1.08, x=0), hoverlabel=dict(namelength=-1),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(tickformat=",.0f", zeroline=True)
    return fig


# --- Data ---------------------------------------------------------------------
docs = pd.read_sql_query(
    """SELECT id, email_date, attachment_name, doc_type, layout, parse_count, extract_error, parse_error,
              path, text_path FROM documents ORDER BY email_date""",
    conn,
)
issues = pd.read_sql_query("SELECT subject AS reference, message FROM issues ORDER BY subject", conn)
cash = pd.read_sql_query("SELECT date, kind, ref, amount, description, sources FROM cash_movements ORDER BY date",
                         conn)
tx_all = load_transactions(conn, cfg.manual_csv)

st.title("Portfolio performance")

if docs.empty:
    st.info(
        "No statements yet. Configure `config.toml`, then run `smpa auth` and `smpa run` "
        "(or `smpa import <folder>` for PDFs you already have)."
    )
    st.stop()

# --- Filters (one row, above the charts) ---------------------------------------
if not tx_all.empty:
    c1, c2 = st.columns([1, 3])
    min_d, max_d = tx_all["trade_date"].min().date(), tx_all["trade_date"].max().date()
    as_of = c1.date_input("As of", value=max_d, min_value=min_d, max_value=max_d)
    symbols = c2.multiselect("Symbols (tables only)", sorted(tx_all["symbol"].unique()))
    tx = tx_all[tx_all["trade_date"] <= pd.Timestamp(as_of)]
else:
    tx, symbols = tx_all, []

pf = compute(tx, load_prices(cfg.prices_csv))
h = pf.holdings
has_prices = h["market_value"].notna().any()

# --- KPIs ---------------------------------------------------------------------
k = [*st.columns(3), *st.columns(3)]
k[0].metric("Net invested", money(pf.net_invested), help="Buys (incl. fees) minus sell proceeds")
k[1].metric("Open cost basis", money(h["cost_basis"].sum()))
k[2].metric("Market value", money(h["market_value"].sum()) if has_prices else "—",
            help=None if has_prices else f"Add {cfg.prices_csv.name} (symbol,price) for market values")
k[3].metric("Unrealized P&L", money(h["unrealized_pnl"].sum()) if has_prices else "—")
k[4].metric("Realized P&L", money(pf.realized_pnl))
k[5].metric("Fees paid", money(pf.fees))

for w in pf.warnings:
    st.warning(w)

# --- Charts -------------------------------------------------------------------
if not tx.empty:
    left, right = st.columns(2)

    with left:
        st.subheader("Net invested over time")
        inv = invested_over_time(tx)
        # Carry the last value to the as-of date so the step line doesn't end abruptly.
        inv = pd.concat([inv, pd.DataFrame([{"trade_date": pd.Timestamp(as_of),
                                             "net_invested": inv["net_invested"].iloc[-1]}])])
        fig = go.Figure(go.Scatter(
            x=inv["trade_date"], y=inv["net_invested"], mode="lines", line_shape="hv",
            line=dict(color=BLUE, width=2), name="Net invested",
            hovertemplate="%{x|%d %b %Y}<br>" + cur + "%{y:,.2f}<extra></extra>",
        ))
        fig.update_yaxes(rangemode="tozero")
        st.plotly_chart(style(fig).update_layout(hovermode="x unified"), width="stretch")

    with right:
        st.subheader("Realized P&L by month")
        rm = monthly(pf.realized, "pnl")
        if rm.empty:
            st.caption("No sells yet.")
        else:
            fig = go.Figure(go.Bar(
                x=rm["month"], y=rm["pnl"], marker_color=[BLUE if v >= 0 else RED for v in rm["pnl"]],
                hovertemplate="%{x|%b %Y}<br>" + cur + "%{y:,.2f}<extra></extra>", name="Realized P&L",
            ))
            st.plotly_chart(style(fig), width="stretch")

    left, right = st.columns(2)

    with left:
        measure = "market_value" if has_prices else "cost_basis"
        st.subheader("Open positions by " + ("market value" if has_prices else "cost basis"))
        alloc = h[["symbol", measure]].dropna().sort_values(measure, ascending=False)
        if len(alloc) > 12:  # fold the tail into "Other"
            alloc = pd.concat([
                alloc.head(11),
                pd.DataFrame([{"symbol": "Other", measure: alloc[measure].iloc[11:].sum()}]),
            ])
        if alloc.empty:
            st.caption("No open positions.")
        else:
            share = alloc[measure] / alloc[measure].sum()
            fig = go.Figure(go.Bar(
                y=alloc["symbol"], x=alloc[measure], orientation="h", marker_color=BLUE,
                customdata=share, name="",
                hovertemplate="%{y}<br>" + cur + "%{x:,.2f} (%{customdata:.1%})<extra></extra>",
            ))
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(style(fig, 40 + 28 * len(alloc)).update_xaxes(tickformat=",.0f"), width="stretch")

    with right:
        st.subheader("Monthly trading volume")
        vol = monthly(tx, "gross_amount", by="side")
        fig = go.Figure()
        for side, color, label in (("BUY", BLUE, "Buys"), ("SELL", ORANGE, "Sells")):
            d = vol[vol["side"] == side]
            fig.add_bar(x=d["month"], y=d["gross_amount"], name=label, marker_color=color,
                        hovertemplate="%{x|%b %Y}<br>" + label + ": " + cur + "%{y:,.2f}<extra></extra>")
        st.plotly_chart(style(fig).update_layout(barmode="group", bargroupgap=0.1), width="stretch")

# --- Tables -------------------------------------------------------------------
num = st.column_config.NumberColumn
tabs = st.tabs(["Holdings", "Transactions", "Realized", "Cash", f"Documents ({len(issues)} warnings)"])


def by_symbol(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["symbol"].isin(symbols)] if symbols else df


with tabs[0]:
    st.dataframe(by_symbol(h), hide_index=True, width="stretch", column_config={
        "quantity": num(format="%,.0f"), "avg_cost": num(format="%,.2f"),
        "cost_basis": num(format="%,.2f"), "price": num(format="%,.2f"),
        "market_value": num(format="%,.2f"), "unrealized_pnl": num(format="%,.2f"),
        "unrealized_pct": num("unrealized %", format="percent"),
    })
with tabs[1]:
    st.dataframe(by_symbol(tx), hide_index=True, width="stretch",
                 column_config={"trade_date": st.column_config.DateColumn(format="YYYY-MM-DD"),
                                "price": num(format="%,.2f"), "gross_amount": num(format="%,.2f"),
                                "fees": num(format="%,.2f"), "net_amount": num(format="%,.2f")})
with tabs[2]:
    st.dataframe(by_symbol(pf.realized), hide_index=True, width="stretch",
                 column_config={"trade_date": st.column_config.DateColumn(format="YYYY-MM-DD"),
                                "proceeds": num(format="%,.2f"), "cost": num(format="%,.2f"),
                                "pnl": num(format="%,.2f")})
with tabs[3]:
    st.caption("Deposits and payments to your bank, from receipts and statements. Months without a "
               "statement before receipts started (2025) are missing here; trades are complete regardless.")
    st.dataframe(cash, hide_index=True, width="stretch",
                 column_config={"amount": num(format="%,.2f")})
with tabs[4]:
    n_err = (docs["extract_error"].notna() | docs["parse_error"].notna()).sum()
    st.caption(f"{len(docs)} documents · {n_err} with errors · {len(issues)} warnings")
    if len(issues):
        st.dataframe(issues, hide_index=True, width="stretch")
    st.dataframe(docs.drop(columns=["id", "path", "text_path"]), hide_index=True, width="stretch")
    with_text = docs[docs["text_path"].notna()].set_index("id")
    if not with_text.empty:
        pick = st.selectbox("View extracted text", with_text.index, format_func=lambda i: (
            f"{with_text.loc[i, 'email_date']} · {with_text.loc[i, 'attachment_name']}"))
        row = with_text.loc[pick]
        st.code((cfg.data_dir / row["text_path"]).read_text(), language=None)
