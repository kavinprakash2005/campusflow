"""Environment-based configuration. Secrets are never hard-coded."""
import os
from datetime import timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    # mysql (default, production) | sqlite (development/testing fallback only)
    DB_BACKEND = os.environ.get("DB_BACKEND", "mysql").lower()
    MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
    MYSQL_USER = os.environ.get("MYSQL_USER", "campusflow")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
    MYSQL_DB = os.environ.get("MYSQL_DB", "campusflow")
    SQLITE_PATH = os.environ.get("SQLITE_PATH", os.path.join(BASE_DIR, "campusflow_dev.db"))

    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024          # hard request cap (6 MB)
    MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024        # per-file cap (5 MB)
    ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "doc", "docx"}

    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.environ.get("SESSION_MINUTES", "60")))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"

    MAX_FAILED_LOGINS = 5
    LOCKOUT_MINUTES = 10
    SHOW_DEMO_ACCOUNTS = os.environ.get("SHOW_DEMO_ACCOUNTS", "1") == "1"
    # cdn  -> Bootstrap / Bootstrap Icons from jsDelivr (default)
    # local -> files under static/vendor (offline use; see README)
    ASSET_MODE = os.environ.get("ASSET_MODE", "cdn").lower()
    COLLEGE_NAME = os.environ.get("COLLEGE_NAME", "Horizon Institute of Technology")
