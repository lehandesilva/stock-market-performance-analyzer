"""Download PDF statement attachments from Gmail (read-only access)."""

from __future__ import annotations

import base64
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .ingest import store_pdf

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_service(secrets_dir: Path):
    """Build an authenticated Gmail client, running the OAuth flow if needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token = secrets_dir / "token.json"
    client = secrets_dir / "credentials.json"
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:  # revoked/expired refresh token -> re-consent
                creds = None
        if not creds or not creds.valid:
            if not client.exists():
                raise SystemExit(
                    f"Missing OAuth client file: {client}\n"
                    "Create a Desktop OAuth client in Google Cloud Console and save "
                    "the JSON there (see README)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
            creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json())
        token.chmod(0o600)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def build_query(cfg: Config) -> str:
    q = cfg.gmail_query.strip()
    if cfg.gmail_after:
        q += f" after:{cfg.gmail_after.replace('-', '/')}"
    return q


def iter_message_ids(service, query: str) -> Iterator[str]:
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token, maxResults=500)
            .execute()
        )
        for m in resp.get("messages", []):
            yield m["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


def iter_pdf_parts(part: dict) -> Iterator[dict]:
    """Yield every MIME part (recursively) that is a PDF attachment."""
    filename = part.get("filename") or ""
    mime = part.get("mimeType") or ""
    body = part.get("body") or {}
    if (filename.lower().endswith(".pdf") or mime == "application/pdf") and (
        body.get("attachmentId") or body.get("data")
    ):
        yield part
    for sub in part.get("parts") or []:
        yield from iter_pdf_parts(sub)


def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers") or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def fetch(cfg: Config, conn: sqlite3.Connection, service=None) -> tuple[int, int]:
    """Download new PDF attachments. Returns (messages_scanned, pdfs_saved)."""
    service = service or get_service(cfg.secrets_dir)
    query = build_query(cfg)
    scanned = saved = 0
    for mid in iter_message_ids(service, query):
        if conn.execute("SELECT 1 FROM gmail_messages WHERE message_id = ?", (mid,)).fetchone():
            continue
        msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
        payload = msg.get("payload") or {}
        email_date = datetime.fromtimestamp(
            int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc
        ).date()
        subject, sender = _header(payload, "Subject"), _header(payload, "From")

        for i, part in enumerate(iter_pdf_parts(payload)):
            name = part.get("filename") or f"attachment-{i}.pdf"
            body = part.get("body") or {}
            if body.get("attachmentId"):
                att = (
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=mid, id=body["attachmentId"])
                    .execute()
                )
                data = _decode(att["data"])
            else:
                data = _decode(body["data"])
            if store_pdf(
                cfg,
                conn,
                data,
                source="gmail",
                message_id=mid,
                attachment_name=name,
                email_date=email_date,
                subject=subject,
                sender=sender,
            ):
                saved += 1

        conn.execute(
            "INSERT INTO gmail_messages (message_id, processed_at) VALUES (?, ?)",
            (mid, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        conn.commit()
        scanned += 1
    return scanned, saved
