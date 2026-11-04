import base64

from smpa.config import Config
from smpa.db import connect
from smpa.gmail import build_query, fetch, iter_pdf_parts

PDF = b"%PDF-1.4 fake"


def test_iter_pdf_parts_nested():
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "text/plain", "body": {"data": "aGk="}},
        {"mimeType": "multipart/alternative", "parts": [
            {"filename": "Statement.PDF", "mimeType": "application/octet-stream", "body": {"attachmentId": "a1"}},
        ]},
        {"filename": "logo.png", "mimeType": "image/png", "body": {"attachmentId": "a2"}},
    ]}
    assert [p["body"]["attachmentId"] for p in iter_pdf_parts(payload)] == ["a1"]


class FakeService:
    """Minimal stand-in for the googleapiclient Gmail resource."""

    def __init__(self):
        self.calls = []

    def users(self): return self
    def messages(self): return self
    def attachments(self): return self

    def list(self, **kw):
        self.calls.append(("list", kw))
        return _Exec({"messages": [{"id": "m1"}]})

    def get(self, **kw):
        if "messageId" in kw:  # attachment
            return _Exec({"data": base64.urlsafe_b64encode(PDF).decode().rstrip("=")})
        return _Exec({"internalDate": "1710201600000", "payload": {
            "headers": [{"name": "Subject", "value": "Monthly statement"}],
            "parts": [{"filename": "stmt.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "a1"}}],
        }})


class _Exec:
    def __init__(self, v): self.v = v
    def execute(self): return self.v


def test_fetch_saves_once(tmp_path):
    cfg = Config(data_dir=tmp_path, gmail_query="from:broker", gmail_after="2024-01-01")
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    svc = FakeService()
    assert build_query(cfg) == "from:broker after:2024/01/01"
    assert fetch(cfg, conn, svc) == (1, 1)
    assert fetch(cfg, conn, svc) == (0, 0)  # already processed
    [row] = conn.execute("SELECT * FROM documents").fetchall()
    assert row["email_date"] == "2024-03-12" and row["subject"] == "Monthly statement"
    assert (tmp_path / row["path"]).read_bytes() == PDF
