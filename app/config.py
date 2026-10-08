import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "solid-ground-card-secret")
    INITIAL_ADMIN_NAME = "Church Admin"
    INITIAL_ADMIN_EMAIL = "production@solidground.co.za"
    INITIAL_ADMIN_PASSWORD = "CardAdmin123"
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "15")))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_REFRESH_EACH_REQUEST = True
    WTF_CSRF_ENABLED = True
    CARD_CHECKOUT_MINUTES = int(os.getenv("CARD_CHECKOUT_MINUTES", "60"))
    EMAIL_HOST = os.getenv("SMTP_HOST")
    EMAIL_PORT = int(os.getenv("SMTP_PORT", "587"))
    EMAIL_USERNAME = os.getenv("SMTP_USERNAME")
    EMAIL_PASSWORD = os.getenv("SMTP_PASSWORD")
    EMAIL_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"}
    MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@solidground.co.za")
    FINANCE_EMAIL = os.getenv("FINANCE_EMAIL", "finance@solidground.co.za")
    SENIOR_PASTOR_EMAIL = os.getenv("SENIOR_PASTOR_EMAIL", "seniorpastor@solidground.co.za")
