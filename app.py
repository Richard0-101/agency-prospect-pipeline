import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash

from config import Config
from services.agency_clients import get_clients_for_agency
from services.apollo_service import fetch_top_marketing_contacts, fetch_company_size
from services.email_generator import generate_sequence
from services.gmail_service import build_auth_url, handle_oauth_callback, create_gmail_draft
from services.gpt_clients import gpt_refine_clients
from requests import HTTPError


app = Flask(__name__)
app.secret_key = Config.FLASK_SECRET_KEY

def db():
    os.makedirs(Config.DATA_DIR, exist_ok=True)
    con = sqlite3.connect(Config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS agencies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agency_id INTEGER NOT NULL,
        client_name TEXT,
        client_domain TEXT,
        evidence TEXT,
        confidence TEXT,
        source_page TEXT,
        company_size TEXT,
        logo_src TEXT,
        FOREIGN KEY (agency_id) REFERENCES agencies(id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER NOT NULL,
        name TEXT,
        title TEXT,
        email TEXT,
        linkedin_url TEXT,
        FOREIGN KEY (client_id) REFERENCES clients(id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sequences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER NOT NULL,
        contact_email TEXT NOT NULL,
        step INTEGER NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        gmail_draft_id TEXT,
        status TEXT DEFAULT 'GENERATED',
        FOREIGN KEY (client_id) REFERENCES clients(id)
    )
    """)
    con.commit()
    con.close()

# Ensure the database tables exist as soon as the app is imported, so the
# schema is created no matter how the server is started (python app.py,
# flask run, gunicorn, etc.) — not only when run as the main script.
init_db()

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/scan")
def scan():
    agency_url = request.form.get("agency_url", "").strip()
    if not agency_url.startswith("http"):
        flash("Please enter a valid agency URL starting with http/https.", "error")
        return redirect(url_for("index"))

    con = db()
    cur = con.cursor()
    cur.execute("INSERT INTO agencies (url) VALUES (?)", (agency_url,))
    agency_id = cur.lastrowid
    con.commit()
    con.close()

    raw_clients = get_clients_for_agency(agency_url)

    if not Config.OPENAI_API_KEY:
        flash("OPENAI_API_KEY missing in .env", "error")
        return redirect(url_for("agency_view", agency_id=agency_id))

    refined = gpt_refine_clients(raw_clients, agency_url, model=Config.OPENAI_MODEL)

    con = db()
    cur = con.cursor()

    # Clear any existing clients for this agency (safe)
    cur.execute("DELETE FROM clients WHERE agency_id = ?", (agency_id,))

    # Save GPT output
    for r in refined:
        cur.execute("""
            INSERT INTO clients (agency_id, client_name, client_domain, evidence, confidence, source_page, logo_src)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            agency_id,
            r["company"],
            r["domain"],
            "gpt_refined",
            "High",
            agency_url,
            None
        ))

    con.commit()
    con.close()

    return redirect(url_for("agency_view", agency_id=agency_id))

@app.get("/agency/<int:agency_id>")
def agency_view(agency_id: int):
    con = db()
    cur = con.cursor()
    agency = cur.execute("SELECT * FROM agencies WHERE id = ?", (agency_id,)).fetchone()
    clients = cur.execute("SELECT * FROM clients WHERE agency_id = ? ORDER BY confidence DESC", (agency_id,)).fetchall()
    con.close()
    return render_template("agency.html", agency=agency, clients=clients)

@app.get("/client/<int:client_id>")
def client_view(client_id: int):
    con = db()
    cur = con.cursor()
    client = cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    contacts = cur.execute("SELECT * FROM contacts WHERE client_id = ?", (client_id,)).fetchall()
    con.close()
    return render_template("client.html", client=client, contacts=contacts)

@app.post("/client/<int:client_id>/apollo")
def client_apollo(client_id: int):
    con = db()
    cur = con.cursor()
    client = cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    con.close()

    domain = (client["client_domain"] or "").strip()
    if not domain:
        flash("No client domain found. Apollo needs a domain.", "error")
        return redirect(url_for("client_view", client_id=client_id))

    try:
        size = fetch_company_size(Config.APOLLO_API_KEY, domain)
        people = fetch_top_marketing_contacts(Config.APOLLO_API_KEY, domain, limit=5)
    except HTTPError as e:
        flash(f"Apollo API error: {str(e)}", "error")
        return redirect(url_for("client_view", client_id=client_id))
    except Exception as e:
        flash(f"Unexpected Apollo error: {str(e)}", "error")
        return redirect(url_for("client_view", client_id=client_id))

    con = db()
    cur = con.cursor()

    if size:
        cur.execute("UPDATE clients SET company_size = ? WHERE id = ?", (size, client_id))

    cur.execute("DELETE FROM contacts WHERE client_id = ?", (client_id,))
    for p in people:
        cur.execute("""
            INSERT INTO contacts (client_id, name, title, email, linkedin_url)
            VALUES (?, ?, ?, ?, ?)
        """, (client_id, p["name"], p["title"], p["email"], p["linkedin_url"]))

    con.commit()
    con.close()

    return redirect(url_for("client_view", client_id=client_id))


@app.post("/client/<int:client_id>/sequence")
def create_sequence(client_id: int):
    selected_emails = request.form.getlist("contact_email")
    if not selected_emails:
        flash("Select at least one contact email.", "error")
        return redirect(url_for("client_view", client_id=client_id))

    con = db()
    cur = con.cursor()
    client = cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()

    for email_addr in selected_emails:
        contact = cur.execute("SELECT * FROM contacts WHERE client_id = ? AND email = ?", (client_id, email_addr)).fetchone()
        seq = generate_sequence(
            company=(client["client_name"] or client["client_domain"] or "this company"),
            person_name=(contact["name"] or "there"),
            persona_title=(contact["title"] or "marketing"),
        )
        for step in seq:
            cur.execute("""
                INSERT INTO sequences (client_id, contact_email, step, subject, body, status)
                VALUES (?, ?, ?, ?, ?, 'GENERATED')
            """, (client_id, email_addr, step["step"], step["subject"], step["body"]))
    con.commit()
    con.close()

    flash("Sequence generated. Now connect Gmail and sync drafts.", "ok")
    return redirect(url_for("outreach_view", client_id=client_id))

@app.get("/outreach/<int:client_id>")
def outreach_view(client_id: int):
    con = db()
    cur = con.cursor()
    client = cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    sequences = cur.execute("""
        SELECT * FROM sequences
        WHERE client_id = ?
        ORDER BY contact_email, step
    """, (client_id,)).fetchall()
    con.close()
    return render_template("outreach.html", client=client, sequences=sequences)

@app.get("/gmail/connect")
def gmail_connect():
    next_url = request.args.get("next") or url_for("index")
    # Pass next_url in state (simple + works for demos)
    url = build_auth_url(
        Config.GOOGLE_CLIENT_SECRETS_FILE,
        Config.GOOGLE_OAUTH_SCOPES,
        Config.GOOGLE_OAUTH_REDIRECT_URI,
        state=next_url
    )
    return redirect(url)

@app.get("/gmail/callback")
def gmail_callback():
    # `state` is whatever we passed as state (next URL)
    next_url = request.args.get("state") or url_for("index")

    handle_oauth_callback(
        Config.DB_PATH,
        Config.GOOGLE_CLIENT_SECRETS_FILE,
        Config.GOOGLE_OAUTH_SCOPES,
        Config.GOOGLE_OAUTH_REDIRECT_URI,
        request.url
    )
    flash("Gmail connected successfully.", "ok")
    return redirect(next_url)

@app.post("/outreach/<int:client_id>/sync_gmail")
def sync_gmail(client_id: int):
    con = db()
    cur = con.cursor()
    rows = cur.execute("""
        SELECT * FROM sequences
        WHERE client_id = ? AND (gmail_draft_id IS NULL OR gmail_draft_id = '')
    """, (client_id,)).fetchall()

    if not rows:
        con.close()
        flash("No new emails to sync (all drafts already created).", "ok")
        return redirect(url_for("outreach_view", client_id=client_id))

    for r in rows:
        draft_id = create_gmail_draft(
            Config.DB_PATH,
            Config.GOOGLE_OAUTH_SCOPES,
            r["contact_email"],
            r["subject"],
            r["body"]
        )
        cur.execute("""
            UPDATE sequences
            SET gmail_draft_id = ?, status = 'DRAFTED'
            WHERE id = ?
        """, (draft_id, r["id"]))

    con.commit()
    con.close()
    flash("Drafts created in Gmail ✅ (check Drafts folder).", "ok")
    return redirect(url_for("outreach_view", client_id=client_id))

@app.post("/contact/<int:contact_id>/draft")
def draft_email_to_contact(contact_id: int):
    con = db()
    cur = con.cursor()

    contact = cur.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        con.close()
        flash("Contact not found.", "error")
        return redirect(url_for("index"))

    client = cur.execute("SELECT * FROM clients WHERE id = ?", (contact["client_id"],)).fetchone()
    con.close()

    if not contact["email"]:
        flash("This contact has no email in Apollo. Try another contact.", "error")
        return redirect(url_for("client_view", client_id=client["id"]))

    # If Gmail not connected, redirect to OAuth first (and come back here)
    # We’ll use `next=` to return to the client page after auth.
    if not os.path.exists(Config.DB_PATH):
        flash("DB not found.", "error")
        return redirect(url_for("client_view", client_id=client["id"]))

    # Generate 1 email (step 1) using your existing generator
    seq = generate_sequence(
        company=(client["client_name"] or client["client_domain"] or "this company"),
        person_name=(contact["name"] or "there"),
        persona_title=(contact["title"] or "marketing"),
    )
    first = seq[0]  # step 1

    try:
        draft_id = create_gmail_draft(
            Config.DB_PATH,
            Config.GOOGLE_OAUTH_SCOPES,
            contact["email"],
            first["subject"],
            first["body"],
        )
        flash(f"Draft created for {contact['email']} ✅ (Gmail Drafts).", "ok")
    except Exception as e:
        # If token missing/expired, send to connect
        flash("Gmail not connected (or token expired). Connect Gmail and retry.", "error")
        return redirect(url_for("gmail_connect", next=url_for("client_view", client_id=client["id"])))

    return redirect(url_for("client_view", client_id=client["id"]))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)