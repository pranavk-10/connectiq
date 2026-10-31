"""Environment configuration. Secrets are never rendered in the application."""
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Config:
    database_url: str
    environment: str
    admin_email: str
    password_hash: str
    timezone: str
    company_csv: Path
    session_hours: int


def get_config() -> Config:
    url = os.getenv("DATABASE_URL", f"sqlite:///{(ROOT / 'data/connectiq.db').as_posix()}")
    if url.startswith(("postgres://", "postgresql://")):
        url = "postgresql+psycopg://" + url.split("://", 1)[1]
    env = os.getenv("APP_ENV", "development")
    if env == "production" and not url.startswith("postgresql+psycopg://"):
        raise ValueError("Production requires a PostgreSQL DATABASE_URL.")
    tz = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")
    ZoneInfo(tz)
    path = Path(os.getenv("COMPANY_CSV_PATH", "data/companies.csv"))
    return Config(url, env, os.getenv("ADMIN_EMAIL", "").strip().lower(),
                  os.getenv("ADMIN_PASSWORD_HASH", ""), tz,
                  path if path.is_absolute() else ROOT / path,
                  max(1, min(int(os.getenv("SESSION_HOURS", "8")), 24)))
