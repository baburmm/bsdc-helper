"""Configuration, read from environment variables."""
import os
import secrets
from pathlib import Path


def _load_dotenv() -> None:
    """Local convenience: load a .env sitting next to the project, if present.

    Real environment variables always win, so this is a no-op in Azure.
    """
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# --- Auth -------------------------------------------------------------------
# The single login. Set this in Container Apps as a secret.
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")

# Signs the session cookie. If unset a random one is generated, which means
# sessions are dropped on every restart / new replica.
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
SECRET_KEY_WAS_GENERATED = not os.getenv("SECRET_KEY")

SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
COOKIE_NAME = "fx_session"
# Container Apps terminates TLS at the ingress, so cookies are always over https
# in practice. Set COOKIE_SECURE=false only for plain-http local testing.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() != "false"

# --- Storage ----------------------------------------------------------------
# Either a full connection string, or an account name + managed identity.
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_STORAGE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT", "")
BLOB_CONTAINER = os.getenv("BLOB_CONTAINER", "uploads")

# Falls back to this local folder when no Azure storage is configured (dev only).
LOCAL_STORAGE_DIR = os.getenv("LOCAL_STORAGE_DIR", "./_local_files")

# --- Uploads ----------------------------------------------------------------
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "512"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ext.strip().lower().lstrip(".")
    for ext in os.getenv("ALLOWED_EXTENSIONS", "zip,pptx,ppt,xlsx,xls,xlsm,csv").split(",")
    if ext.strip()
}
