"""Build a demo folder to try the dashboard without your own statements.

    python scripts/make_demo.py /tmp/smpa-demo
    smpa -c /tmp/smpa-demo/config.toml dashboard

The test fixtures' documents, plus made-up prices (with thin trading), an ASPI, trades typed in by hand,
dividends, CSE security info, sector indices and every optional data/ file, a trading journal included. Nothing in it is real; it exists
to look at the pages, not to check numbers (the tests do that). The folder is deleted and rebuilt.
"""
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from smpa.config import Config  # noqa: E402
from smpa.db import connect  # noqa: E402
from smpa.parsers import parse_all  # noqa: E402

if len(sys.argv) != 2:
    sys.exit(__doc__)
out = Path(sys.argv[1]).resolve()
if out == REPO or REPO in out.parents:
    sys.exit("pick a folder outside the repository (it is deleted and rebuilt)")
shutil.rmtree(out, ignore_errors=True)
(out / "data").mkdir(parents=True)
(out / "config.toml").write_text('[paths]\ndata_dir = "data"\n[tax]\ndividend_withholding = 0.15\n')
cfg = Config(data_dir=out / "data", dividend_withholding=0.15)
cfg.ensure_dirs()
conn = connect(cfg.db_path)
FIX = REPO / "tests/fixtures/ndb"
ALL = ["contract_note_2021-05_bought.txt", "trade_confirmation_2021-05.txt", "statement_2021-05.txt",
       "contract_note_2026-09_sold.txt", "trade_confirmation_2026-09.txt", "statement_2026-10.txt", "receipt.txt"]
for i, name in enumerate(ALL):
    (cfg.text_dir / name).write_text((FIX / name).read_text())
    conn.execute("""INSERT INTO documents (source, message_id, attachment_name, email_date, path, sha256, downloaded_at,
                    text_path, extracted_at) VALUES ('local', ?, ?, '2026-01-01', ?, ?, 'x', ?, 'x')""",
                 (f"m{i}", name, f"raw/{name}", f"s{i}", f"text/{name}"))
conn.commit()
parse_all(cfg, conn)

rng = np.random.default_rng(7)
days = pd.bdate_range("2021-03-01", "2026-11-05")
mkt = np.cumsum(rng.normal(0.0004, 0.011, len(days)))
mkt[(days >= "2022-03-01") & (days <= "2022-10-01")] -= np.linspace(0, 0.5, ((days >= "2022-03-01") & (days <= "2022-10-01")).sum())
mkt[days > "2022-10-01"] -= 0.5
aspi = 7000 * np.exp(mkt)
conn.executemany("INSERT INTO index_history (name, date, value) VALUES ('ASPI', ?, ?)",
                 [(d.strftime("%Y-%m-%d"), float(v)) for d, v in zip(days, aspi)])
start = {"AAA.N0000": 12.5, "BBB.N0000": 85.0, "CCC.N0000": 6.0, "DDD.N0000": 20.0, "EEE.N0000": 40.0}
beta = {"AAA.N0000": 1.2, "BBB.N0000": 0.8, "CCC.N0000": 1.5, "DDD.N0000": 0.6, "EEE.N0000": 1.0}
rows, series = [], {}
for sym, p0 in start.items():
    r = beta[sym] * np.diff(mkt, prepend=mkt[0]) + rng.normal(0.0002, 0.015, len(days))
    px = p0 * np.exp(np.cumsum(r))
    if sym == "CCC.N0000":
        px = px * (9.80 / px[days.get_indexer([pd.Timestamp("2026-09-02")])[0]])
    if sym == "DDD.N0000":
        px = px * (24.0 / px[days.get_indexer([pd.Timestamp("2026-10-06")])[0]])
    if sym == "AAA.N0000":
        px = px * (15.2 / px[days.get_indexer([pd.Timestamp("2026-09-02")])[0]])
    series[sym] = pd.Series(px, index=days)
    for d, v in zip(days, px):
        if d < pd.Timestamp("2025-11-01") and sym != "AAA.N0000" and rng.random() < 0.15:
            continue  # thin trading: some days without a trade
        rows.append((sym, d.strftime("%Y-%m-%d"), round(float(v), 2), float(rng.integers(2_000, 60_000))))
conn.executemany("INSERT INTO price_history (symbol, date, price, volume) VALUES (?, ?, ?, ?)", rows)
for i, (sym, s_) in enumerate(series.items()):
    conn.execute("""INSERT INTO security_info (symbol, fetched_on, name, isin, cse_id, market_cap, hi_52w, lo_52w,
                    beta_tri_aspi, beta_spsl, beta_period, beta_quarter) VALUES (?, '2026-11-05', ?, ?, ?, ?, ?, ?, ?, ?, '2026', 1)""",
                 (sym, sym[:3] + " HOLDINGS PLC", f"LK000{i}N00001", 100 + i, 5e9 * (i + 1),
                  float(s_[-245:].max()), float(s_[-245:].min()), beta[sym] * 0.9, beta[sym] * 1.05))
# every stock's day (the market), for index sector weights; and sector index levels
sectors = {"AAA.N0000": "Capital Goods", "BBB.N0000": "Banks", "CCC.N0000": "Banks", "DDD.N0000": "Food, Beverage & Tobacco",
           "EEE.N0000": "Materials", "ZZ1.N0000": "Banks", "ZZ2.N0000": "Capital Goods", "ZZ3.N0000": "Energy"}
for d in days[-300:]:
    ds = d.strftime("%Y-%m-%d")
    for sym, cap in (("AAA.N0000", 4e9), ("BBB.N0000", 9e9), ("CCC.N0000", 3e9), ("DDD.N0000", 2e9),
                     ("ZZ1.N0000", 40e9), ("ZZ2.N0000", 30e9), ("ZZ3.N0000", 12e9)):
        conn.execute("INSERT INTO daily_quotes (symbol, date, close, turnover, market_cap, fetched_at) VALUES (?, ?, 1, 1e6, ?, 'x')",
                     (sym, ds, cap))
k = len(days)
for name, b in (("Banks", 0.9), ("Capital Goods", 1.3), ("Food, Beverage & Tobacco", 0.6), ("Materials", 1.0), ("Energy", 1.1)):
    lv = 1000 * np.exp(b * mkt + np.cumsum(rng.normal(0, 0.004, k)))
    conn.executemany("INSERT INTO index_history (name, date, value) VALUES (?, ?, ?)",
                     [(f"sector:{name}", d.strftime("%Y-%m-%d"), float(v)) for d, v in zip(days[-300:], lv[-300:])])
last = {r[0]: r[2] for r in rows if r[1] == "2026-11-05"}
conn.executemany("""INSERT INTO prices (symbol, name, price, change, change_pct, traded_at, fetched_at)
                    VALUES (?, ?, ?, 0, 0.5, '2026-11-05T08:50:00+00:00', '2026-11-05T10:31:00+00:00')""",
                 [(s, s.split('.')[0] + " PLC", p) for s, p in last.items() if s != "EEE.N0000"])
conn.commit()
# Extra trades by hand, and corporate actions
manual = []
for d, side, sym, q in [("2022-06-01", "BUY", "EEE.N0000", 500), ("2023-02-01", "BUY", "AAA.N0000", 400),
                        ("2024-05-02", "SELL", "EEE.N0000", 300), ("2025-01-15", "SELL", "EEE.N0000", 200),
                        ("2025-02-03", "BUY", "BBB.N0000", 100), ("2025-08-04", "SELL", "BBB.N0000", 40),
                        ("2025-08-06", "BUY", "AAA.N0000", 100)]:
    px = round(float(series[sym][pd.Timestamp(d)]), 2)
    manual.append((d, side, sym, q, px, round(q * px * 0.0112, 2)))
pd.DataFrame(manual, columns=["trade_date", "side", "symbol", "quantity", "price", "fees"]).to_csv(cfg.manual_csv, index=False)
pd.DataFrame({"symbol": list(sectors), "sector": list(sectors.values())}).to_csv(cfg.data_dir / "sectors.csv", index=False)
wk = pd.date_range("2021-01-06", "2026-11-04", freq="W-WED")
yld = np.clip(5 + 25 * np.exp(-((wk - pd.Timestamp("2022-11-01")).days / 300.0) ** 2) + rng.normal(0, 0.2, len(wk)), 4, 33)
pd.DataFrame({"date": wk.strftime("%Y-%m-%d"), "yield": yld.round(2)}).to_csv(cfg.data_dir / "tbill.csv", index=False)
months = pd.period_range("2021-01", "2026-10", freq="M")
infl = np.where((months >= pd.Period("2022-01")) & (months <= pd.Period("2023-06")), 0.035, 0.004)
pd.DataFrame({"month": months.strftime("%Y-%m"), "index": (140 * np.cumprod(1 + infl)).round(1)}).to_csv(cfg.data_dir / "ccpi.csv", index=False)
fx_days = pd.bdate_range("2021-01-01", "2026-11-05")
fx = np.where(fx_days < pd.Timestamp("2022-03-08"), 200.0, np.where(fx_days < pd.Timestamp("2022-06-01"), 360.0, 300.0))
pd.DataFrame({"date": fx_days.strftime("%Y-%m-%d"), "lkr_per_usd": fx}).to_csv(cfg.data_dir / "fx.csv", index=False)
me = [d for d in days if d == days[(days.year == d.year) & (days.month == d.month)][-1]]
pd.DataFrame({"name": "ASTRI", "date": [d.strftime("%Y-%m-%d") for d in me],
              "value": [round(float(aspi[days.get_loc(d)]) * 1.035 ** ((d - days[0]).days / 365) * 1.3, 2) for d in me]}
             ).to_csv(cfg.data_dir / "indices.csv", index=False)
(cfg.journal_csv).write_text("contract_no,reason,target,stop,note\n2021900001,results play,16,10,\n"
                             "2021900002,dividend,,,\n2021900003,dividend,,,\n2026900011,a tip,12,8.5,from a friend\n"
                             "2026900010,took profit,,,\n2026900020,Results play,28,21,\n")
cfg.actions_csv.write_text("date,symbol,action,amount,per_share,ratio,note\n"
                           "2022-05-10,AAA.N0000,DIVIDEND,,0.75,,\n2023-05-10,BBB.N0000,DIVIDEND,,4.00,,\n"
                           "2024-05-10,AAA.N0000,DIVIDEND,425.00,,,final\n2025-06-01,BBB.N0000,DIVIDEND,,5.50,,\n"
                           "2026-03-01,AAA.N0000,DIVIDEND,,1.00,,\n")
print("demo at", out)
