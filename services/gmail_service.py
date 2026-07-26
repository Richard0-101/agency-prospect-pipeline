import os
import json
import base64
import sqlite3
import email.message
from typing import Optional

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def _db_init(db_path: str):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gmail_tokens (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            token_json TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


def _save_token(db_path: str, creds: Credentials):
    _db_init(db_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO gmail_tokens (id, token_json)
        VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET token_json=excluded.token_json
    """, (creds.to_json(),))
    con.commit()
    con.close()


def _load_token(db_path: str, scopes: list[str]) -> Optional[Credentials]:
    _db_init(db_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT token_json FROM gmail_tokens WHERE id = 1")
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    token_info = json.loads(row[0])
    return Credentials.from_authorized_user_info(token_info, scopes=scopes)


def build_auth_url(
    client_secrets_file: str,
    scopes: list[str],
    redirect_uri: str,
    state: str | None = None,   # <-- ADD THIS
) -> str:
    flow = Flow.from_client_secrets_file(
        client_secrets_file=client_secrets_file,
        scopes=scopes,
        redirect_uri=redirect_uri,
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,             # <-- PASS IT THROUGH
    )
    return auth_url


def handle_oauth_callback(db_path: str, client_secrets_file: str, scopes: list[str], redirect_uri: str, full_request_url: str):
    flow = Flow.from_client_secrets_file(
        client_secrets_file,
        scopes=scopes,
        redirect_uri=redirect_uri,
    )
    flow.fetch_token(authorization_response=full_request_url)
    _save_token(db_path, flow.credentials)


def create_gmail_draft(db_path: str, scopes: list[str], to_email: str, subject: str, body: str) -> str:
    creds = _load_token(db_path, scopes)
    if not creds:
        raise RuntimeError("Gmail not connected. Please connect Gmail first.")

    service = build("gmail", "v1", credentials=creds)

    msg = email.message.EmailMessage()
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    draft = service.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw}}
    ).execute()
    return draft["id"]
