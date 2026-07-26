import os
from dotenv import load_dotenv
from pathlib import Path

# Explicitly load .env from project root
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

class Config:
    FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
    APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY")

    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini"

    GOOGLE_CLIENT_SECRETS_FILE = os.environ.get("GOOGLE_CLIENT_SECRETS_FILE")
    GOOGLE_OAUTH_REDIRECT_URI = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
    GOOGLE_OAUTH_SCOPES = [
        s.strip() for s in (os.environ.get("GOOGLE_OAUTH_SCOPES") or "").split(",") if s.strip()
    ]

    if not FLASK_SECRET_KEY:
        raise RuntimeError("FLASK_SECRET_KEY not loaded from .env")

    BASE_DIR = str(BASE_DIR)
    DATA_DIR = os.path.join(BASE_DIR, "data")
    DB_PATH = os.path.join(DATA_DIR, "app.db")
