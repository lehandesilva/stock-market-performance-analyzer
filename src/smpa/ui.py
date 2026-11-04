"""Visual language for the dashboard: a monochrome, Swiss-style monitoring console.

Black ink on white paper, hairline rules, uppercase tracked labels, corner
brackets, and one accent colour reserved for alerts (losses, flags, unpriced).
"""

from __future__ import annotations

import math
from html import escape

import numpy as np
import pandas as pd
import plotly.graph_objects as go

INK = "#0B0B0B"
PAPER = "#FFFFFF"
MUTED = "#6B6B6B"  # labels and secondary text (5.3:1 on paper)
HAIR = "#D6D6D6"  # hairline rules
GRID = "#EFEFEF"  # chart grid and page grid
SECOND = "#A3A3A3"  # second data series
ACCENT = "#E8421B"  # the only colour: alerts, losses
FONT = "'Helvetica Neue', Helvetica, 'Nimbus Sans', 'Nimbus Sans L', Arial, 'Liberation Sans', sans-serif"

_bracket = f"linear-gradient({INK},{INK})"
CSS = f"""
<style>
:root {{ --ink:{INK}; --paper:{PAPER}; --muted:{MUTED}; --hair:{HAIR}; --grid:{GRID}; --accent:{ACCENT}; }}
.stApp {{
  background-color: var(--paper);
  background-image: linear-gradient(var(--grid) 1px, transparent 1px),
                    linear-gradient(90deg, var(--grid) 1px, transparent 1px);
  background-size: 48px 48px;
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stMainBlockContainer"] {{ max-width: 1440px; padding-top: 2rem; padding-bottom: 4rem; }}
.stApp, .stApp * {{ font-variant-numeric: tabular-nums; }}

/* Labels: uppercase, wide tracking */
[data-testid="stWidgetLabel"] p {{
  font-size: 10px; letter-spacing: .18em; text-transform: uppercase; color: var(--muted); font-weight: 500;
}}
[role="tablist"] {{ gap: 32px; border-bottom: 1px solid var(--ink); }}
[data-testid="stTab"] p {{ font-size: 11px; letter-spacing: .18em; text-transform: uppercase; }}
[data-testid="stTab"][data-selected="true"] p {{ font-weight: 700; }}
[data-testid="stExpander"] details {{ border: 1px solid var(--hair); border-radius: 0; background: var(--paper); }}
[data-testid="stExpander"] summary p {{ font-size: 11px; letter-spacing: .18em; text-transform: uppercase; }}
.stPlotlyChart text {{ letter-spacing: .06em; }}

/* Panels: keyed containers (st.container(key="panel_*")) framed by corner brackets */
[class*="st-key-panel"] {{
  background-color: var(--paper); padding: 16px 18px 12px;
  background-image: {", ".join([_bracket] * 8)};
  background-repeat: no-repeat;
  background-size: 14px 1px, 1px 14px, 14px 1px, 1px 14px, 14px 1px, 1px 14px, 14px 1px, 1px 14px;
  background-position: left top, left top, right top, right top, left bottom, left bottom, right bottom, right bottom;
}}

/* Header bar */
.smpa-top {{ display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; flex-wrap: wrap;
  border-top: 3px solid var(--ink); border-bottom: 1px solid var(--ink); padding: 14px 0 12px;
  background: var(--paper); margin-bottom: 6px; }}
.smpa-brand {{ display: flex; align-items: center; gap: 14px; }}
.smpa-title {{ font-size: 22px; font-weight: 700; letter-spacing: .22em; text-transform: uppercase; line-height: 1; }}
.smpa-sub, .smpa-meta .k {{ font-size: 10px; letter-spacing: .18em; text-transform: uppercase; color: var(--muted); }}
.smpa-sub {{ margin-top: 6px; }}
.smpa-meta {{ display: flex; gap: 28px; flex-wrap: wrap; }}
.smpa-meta > div {{ display: flex; flex-direction: column; gap: 3px; border-left: 1px solid var(--hair); padding-left: 10px; }}
.smpa-meta .v {{ font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }}
.smpa-alert {{ color: var(--accent); }}

/* Telemetry readouts */
.smpa-readouts {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); background: var(--paper);
  border-bottom: 1px solid var(--hair); margin: 4px 0 14px; }}
.smpa-readouts .cell {{ padding: 14px 14px 12px; border-left: 1px solid var(--hair); min-width: 0; }}
.smpa-readouts .cell:first-child {{ border-left: none; padding-left: 0; }}
.smpa-readouts .k {{ font-size: 10px; letter-spacing: .18em; text-transform: uppercase; color: var(--muted); }}
.smpa-readouts .v {{ font-size: clamp(20px, 1.9vw, 30px); font-weight: 300; letter-spacing: -.01em; margin: 8px 0 6px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.smpa-readouts .v .dec {{ font-size: .55em; color: var(--muted); }}
.smpa-readouts .s {{ font-size: 10px; letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }}
@media (max-width: 1100px) {{
  .smpa-readouts {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
  .smpa-readouts .cell:nth-child(4) {{ border-left: none; padding-left: 0; }}
}}
@media (max-width: 640px) {{
  /* Network callouts don't fit on a phone: keep the hub label, rely on tap-to-inspect */
  [class*="st-key-panel_network"] .annotation + .annotation {{ display: none; }}
  .smpa-index .i {{ width: 22px; padding-right: 6px; }}
  .smpa-readouts {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .smpa-readouts .cell {{ border-left: none; padding-left: 0; }}
  .smpa-readouts .v {{ font-size: 24px; }}
}}

/* Section titles: number / title / note, over a hairline */
.smpa-sec {{ display: flex; gap: 12px; align-items: baseline; border-bottom: 1px solid var(--hair);
  padding-bottom: 8px; margin-bottom: 4px; font-size: 11px; letter-spacing: .18em; text-transform: uppercase; }}
.smpa-sec .n {{ color: var(--muted); }}
.smpa-sec .t {{ font-weight: 700; }}
.smpa-sec .r {{ margin-left: auto; color: var(--muted); font-size: 10px; text-align: right; }}

/* Position index list */
.smpa-index {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.smpa-index td {{ padding: 6px 0; border-bottom: 1px solid var(--grid); white-space: nowrap; }}
.smpa-index .i {{ color: var(--muted); font-size: 10px; letter-spacing: .1em; width: 28px; }}
.smpa-index .sym {{ letter-spacing: .06em; }}
.smpa-index .num {{ text-align: right; padding-left: 12px; }}
.smpa-index .bar {{ width: 34%; padding-left: 12px; }}
.smpa-index .bar div {{ height: 2px; background: var(--ink); }}
.smpa-empty {{ font-size: 11px; letter-spacing: .16em; text-transform: uppercase; color: var(--muted); padding: 24px 0; }}

/* Flags */
.smpa-flag {{ display: flex; gap: 14px; padding: 7px 0; border-bottom: 1px solid var(--grid); font-size: 12px; }}
.smpa-flag .id {{ color: var(--accent); font-size: 10px; letter-spacing: .14em; min-width: 64px; padding-top: 2px; }}
</style>
"""

# A reticle: the dashboard's mark.
MARK = f"""<svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">
<circle cx="15" cy="15" r="9" fill="none" stroke="{INK}" stroke-width="1"/>
<circle cx="15" cy="15" r="2" fill="{INK}"/>
<path d="M15 0v9M15 21v9M0 15h9M21 15h9" stroke="{INK}" stroke-width="1"/></svg>"""


def header(subtitle: str, meta: list[tuple[str, str, bool]]) -> str:
    cells = "".join(
        f'<div><span class="k">{escape(k)}</span><span class="v{" smpa-alert" if alert else ""}">{escape(v)}</span></div>'
        for k, v, alert in meta
    )
    return (f'<div class="smpa-top"><div class="smpa-brand">{MARK}<div><div class="smpa-title">Portfolio Monitor</div>'
            f'<div class="smpa-sub">{escape(subtitle)}</div></div></div><div class="smpa-meta">{cells}</div></div>')


def readouts(items: list[tuple[str, float | None, str, bool]]) -> str:
    """(label, value, sub-line, alert) cells. Values are split into integer and decimal parts."""
    cells = []
    for label, value, sub, alert in items:
        if value is None or pd.isna(value):
            v = "—"
        else:
            whole, dec = f"{value:,.2f}".split(".")
            # Cents only while they fit; above a million they're noise.
            v = escape(f"{value:,.0f}") if abs(value) >= 1e6 else f'{escape(whole)}<span class="dec">.{dec}</span>'
        cls = ' smpa-alert' if alert else ''
        cells.append(f'<div class="cell"><div class="k">{escape(label)}</div><div class="v">{v}</div>'
                     f'<div class="s{cls}">{escape(sub)}</div></div>')
    return f'<div class="smpa-readouts">{"".join(cells)}</div>'


def section(number: int, title: str, note: str = "") -> str:
    return (f'<div class="smpa-sec"><span class="n">{number:02d}</span><span class="t">{escape(title)}</span>'
            f'<span class="r">{escape(note)}</span></div>')


def empty(text: str) -> str:
    return f'<div class="smpa-empty">{escape(text)}</div>'


def flags(items: list[tuple[str, str]]) -> str:
    return "".join(f'<div class="smpa-flag"><span class="id">{escape(i)}</span><span>{escape(m)}</span></div>'
                   for i, m in items)


def position_index(h: pd.DataFrame, value_col: str, limit: int = 15) -> str:
    """Ranked list of holdings with a hairline weight bar; losses in the accent colour."""
    df = h.sort_values(value_col, ascending=False)
    total, top = df[value_col].sum(), df[value_col].max()
    rows = []
    for i, (sym, value, pct) in enumerate(zip(df["symbol"], df[value_col], df["unrealized_pct"]), start=1):
        if i > limit:
            break
        has_pct = pct is not None and not pd.isna(pct)
        cls = " smpa-alert" if has_pct and pct < 0 else ""
        rows.append(
            f'<tr><td class="i">{i:02d}</td><td class="sym">{escape(sym)}</td>'
            f'<td class="bar"><div style="width:{value / top * 100 if top else 0:.1f}%"></div></td>'
            f'<td class="num">{value / total if total else 0:.1%}</td>'
            f'<td class="num{cls}">{f"{pct:+.1%}" if has_pct else "—"}</td></tr>'
        )
    more = len(df) - limit
    tail = f'<div class="smpa-empty" style="padding:10px 0 0">+ {more} more in the holdings table</div>' if more > 0 else ""
    return f'<table class="smpa-index">{"".join(rows)}</table>{tail}'


# --- Plotly -------------------------------------------------------------------

def style(fig: go.Figure, height: int = 300, legend: bool = False) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=10, b=8), paper_bgcolor=PAPER, plot_bgcolor=PAPER,
        font=dict(family=FONT, size=11, color=INK), bargap=0.3, barcornerradius=0, showlegend=legend,
        legend=dict(orientation="h", x=0, y=1.14, font=dict(size=10, color=MUTED), itemsizing="constant"),
        hoverlabel=dict(bgcolor=PAPER, bordercolor=INK, font=dict(family=FONT, size=11, color=INK), namelength=-1),
    )
    fig.update_xaxes(showgrid=False, showline=True, linecolor=INK, linewidth=1, ticks="outside", ticklen=4,
                     tickcolor=INK, tickfont=dict(size=10, color=MUTED), zeroline=False, automargin=True)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, showline=False, ticks="", zeroline=True,
                     zerolinecolor=INK, zerolinewidth=1, tickformat=",.0f", tickfont=dict(size=10, color=MUTED),
                     automargin=True)
    return fig


PLOTLY_CONFIG = {"displayModeBar": False}


def _van_der_corput(i: int) -> float:
    """Base-2 radical inverse: 0, .5, .25, .75, .125, ... (evenly fills [0, 1))."""
    out, denom = 0.0, 1.0
    while i:
        denom *= 2
        out += (i & 1) / denom
        i >>= 1
    return out


def network(h: pd.DataFrame, cur: str, label_top: int = 16) -> go.Figure:
    """Holdings as a hub-and-spoke node-link diagram.

    Hub = the portfolio; each node = a holding, area proportional to its weight
    (market value, or cost basis where there is no price). Spokes are hairlines;
    the largest holdings get leader-line callouts. Losses use the accent colour;
    unpriced holdings are grey.
    """
    df = h.copy()
    df["value"] = df["market_value"].fillna(df["cost_basis"])
    df = df.sort_values("value", ascending=False).reset_index(drop=True)
    n, total = len(df), df["value"].sum()
    w = df["value"] / total
    # Van der Corput ordering spreads the largest holdings evenly around the ring
    # (1st at 12 o'clock, 2nd at 6, 3rd at 3, 4th at 9, ...), so callouts don't collide.
    ang = np.array([math.pi / 2 - 2 * math.pi * _van_der_corput(i) for i in range(n)])
    x, y = np.cos(ang), np.sin(ang)
    size = 9 + 34 * np.sqrt(w / w.max())
    loss = df["unrealized_pnl"].fillna(0) < 0
    unpriced = df["market_value"].isna()
    edge = np.where(unpriced, SECOND, np.where(loss, ACCENT, INK))

    fig = go.Figure()

    def path(xs, ys, color, width):
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=color, width=width),
                                 hoverinfo="skip", showlegend=False))

    t = np.linspace(0, 2 * math.pi, 241)
    path(np.cos(t) * 1.0, np.sin(t) * 1.0, HAIR, 1)  # orbit
    path(np.cos(t) * 0.42, np.sin(t) * 0.42, GRID, 1)  # inner ring
    tx, ty = [], []
    for k in range(72):  # dial ticks every 5 degrees, longer every 30
        a = 2 * math.pi * k / 72
        r2 = 1.2 if k % 6 == 0 else 1.16
        tx += [1.12 * math.cos(a), r2 * math.cos(a), None]
        ty += [1.12 * math.sin(a), r2 * math.sin(a), None]
    path(tx, ty, MUTED, 1)
    path([-1.3, 1.3, None, 0, 0], [0, 0, None, -1.3, 1.3], GRID, 1)  # crosshair
    sx, sy = [], []
    for xi, yi in zip(x, y):
        sx += [0, xi, None]
        sy += [0, yi, None]
    path(sx, sy, "rgba(11,11,11,0.35)", 0.75)  # spokes

    pnl_txt = [("—" if pd.isna(p) else f"{p:+.1%}") for p in df["unrealized_pct"]]
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="markers", showlegend=False,
        marker=dict(size=size, color=PAPER, line=dict(color=edge, width=1)),
        customdata=np.stack([df["symbol"], df["quantity"], df["value"], w, pnl_txt], axis=-1),
        hovertemplate=("<b>%{customdata[0]}</b><br>QTY %{customdata[1]:,.0f}<br>VALUE " + cur
                       + "%{customdata[2]:,.2f}<br>WEIGHT %{customdata[3]:.1%}<br>UNREALIZED %{customdata[4]}"
                       "<extra></extra>"),
    ))
    fig.add_trace(go.Scatter(x=x, y=y, mode="markers", hoverinfo="skip", showlegend=False,
                             marker=dict(size=3, color=list(edge))))  # node cores
    fig.add_trace(go.Scatter(x=[0], y=[0], mode="markers", hoverinfo="skip", showlegend=False,
                             marker=dict(size=11, symbol="square", color=INK)))
    fig.add_annotation(x=0, y=-0.1, text=f"PORTFOLIO<br><b>{cur}{total:,.0f}</b>", showarrow=False,
                       font=dict(size=10, color=INK), yanchor="top", bgcolor=PAPER, borderpad=3)

    for i in range(min(label_top, n)):
        c, s = math.cos(ang[i]), math.sin(ang[i])
        length = 34 if i < 8 else 58  # later labels sit between earlier ones; push them further out
        color = edge[i]
        fig.add_annotation(
            x=x[i], y=y[i], ax=c * length, ay=-s * length, axref="pixel", ayref="pixel",
            text=f"<b>{escape(df['symbol'][i])}</b><br>{w[i]:.1%} · {pnl_txt[i]}",
            showarrow=True, arrowhead=0, arrowwidth=0.75, arrowcolor=color, standoff=size[i] / 2 + 1,
            xanchor="left" if c > 0.2 else "right" if c < -0.2 else "center",
            yanchor="bottom" if s > 0.2 else "top" if s < -0.2 else "middle",
            font=dict(size=10, color=ACCENT if loss[i] else INK), align="left" if c > 0.2 else "right",
        )
    fig.update_layout(height=520, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor=PAPER, plot_bgcolor=PAPER,
                      font=dict(family=FONT, size=10, color=INK),
                      hoverlabel=dict(bgcolor=PAPER, bordercolor=INK, font=dict(family=FONT, size=11, color=INK)))
    fig.update_xaxes(visible=False, range=[-1.75, 1.75])
    fig.update_yaxes(visible=False, range=[-1.45, 1.45], scaleanchor="x", scaleratio=1)
    return fig
