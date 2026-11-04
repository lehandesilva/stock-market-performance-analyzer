"""parse_all + reconcile on synthetic documents."""

from pathlib import Path

import pytest

from smpa.config import Config
from smpa.db import connect
from smpa.errors import PipelineStop
from smpa.parsers import parse_all

FIX = Path(__file__).parent / "fixtures" / "ndb"
ALL = ["contract_note_2021-05_bought.txt", "trade_confirmation_2021-05.txt", "statement_2021-05.txt",
       "contract_note_2026-09_sold.txt", "trade_confirmation_2026-09.txt", "statement_2026-10.txt", "receipt.txt"]


def setup(tmp_path, texts: dict[str, str], **cfg_kw):
    cfg = Config(data_dir=tmp_path, **cfg_kw)
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    for i, (name, body) in enumerate(texts.items()):
        (cfg.text_dir / name).write_text(body)
        conn.execute(
            """INSERT INTO documents (source, message_id, attachment_name, path, sha256, downloaded_at,
                                      text_path, extracted_at) VALUES ('local', ?, ?, ?, ?, 'x', ?, 'x')""",
            (f"m{i}", name.replace(".txt", ".pdf"), f"raw/{name}.pdf", f"sha{i}", f"text/{name}"),
        )
    conn.commit()
    return cfg, conn


def fixtures(*names):
    return {n: (FIX / n).read_text() for n in names}


def test_reconciles_one_trade_per_contract(tmp_path):
    cfg, conn = setup(tmp_path, fixtures(*ALL))
    docs, warnings = parse_all(cfg, conn)
    assert docs == 7
    trades = {r["contract_no"]: r for r in conn.execute("SELECT * FROM trades")}
    assert set(trades) == {"2021900001", "2021900002", "2021900003", "2026900010", "2026900011", "2026900020"}
    t = trades["2021900001"]
    assert t["sources"] == "contract_note,trade_confirmation,statement"
    assert (t["isin"], t["company"], t["fees_total"]) == ("LK0000N00001", "ALPHA PLC", 42.0)
    assert trades["2026900011"]["sources"] == "trade_confirmation"
    assert trades["2026900020"]["sources"] == "statement" and trades["2026900020"]["brokerage"] is None
    assert sorted(w.split(":")[0] for w in warnings) == ["2026-09-02 BUY 1000 CCC.N0000",
                                                        "2026-10-06 BUY 200 DDD.N0000"]
    refs = [r["ref"] for r in conn.execute("SELECT ref FROM cash_movements ORDER BY date")]
    assert refs == ["RCT202103000001", "RCT202610000001", "RCT202611000001"]
    # Re-running is idempotent.
    assert parse_all(cfg, conn, force=True)[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 6


def test_conflicting_documents_stop(tmp_path):
    tc = (FIX / "trade_confirmation_2021-05.txt").read_text()
    # Same contract, different (but internally consistent) brokerage and net.
    tc = tc.replace("3,750.00 24.00", "3,750.00 25.00").replace("3,792.00", "3,793.00")
    tc = tc.replace("Total 300 12.6400", "Total 300 12.6433").replace("8,949.13", "8,950.13")
    cfg, conn = setup(tmp_path, {**fixtures("contract_note_2021-05_bought.txt"), "tc.txt": tc})
    with pytest.raises(PipelineStop, match="2021900001.*disagree.*brokerage: 24.00 vs 25.00"):
        parse_all(cfg, conn)


def test_unparseable_document_stops_and_is_recorded(tmp_path):
    cfg, conn = setup(tmp_path, {"report.txt": "NDB SECURITIES (PVT) LTD.\nWeekly market report"})
    with pytest.raises(PipelineStop, match="unrecognised NDB document type"):
        parse_all(cfg, conn)
    assert conn.execute("SELECT parse_error FROM documents").fetchone()[0]


def test_ignored_attachments_are_skipped(tmp_path):
    cfg, conn = setup(tmp_path, {"Market_Report.txt": "NDB SECURITIES (PVT) LTD.\nWeekly market report"},
                      ignore_attachments=["^Market_Report"])
    parse_all(cfg, conn)
    assert conn.execute("SELECT doc_type FROM documents").fetchone()[0] == "ignored"


def test_other_account_stops(tmp_path):
    cfg, conn = setup(tmp_path, fixtures("receipt.txt"), account="NBS-11111-YY/00")
    with pytest.raises(PipelineStop, match="account NBS-00000-XX/00 differs"):
        parse_all(cfg, conn)
