"""Admin authentication: username + PBKDF2-hashed password list.

ADMIN_USERS env var format (comma-separated):
    alice:pbkdf2_sha256$200000$<salt>$<hash>,bob:pbkdf2_sha256$...
Locally it comes from .env (gitignored); on Cloud Run from Secret Manager.
Manage users with: .venv/bin/python scripts/manage_admins.py
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict

ITERATIONS = 200_000
MAX_FAILURES = 5
FAILURE_WINDOW_SEC = 15 * 60

_failures: dict[str, list[float]] = defaultdict(list)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    return f"pbkdf2_sha256${ITERATIONS}${b64(salt)}${b64(digest)}"


def _b64decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), _b64decode(salt), int(iters))
        return hmac.compare_digest(calc, _b64decode(digest))
    except Exception:
        return False


def parse_users(raw: str) -> dict[str, str]:
    users = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if ":" in entry:
            name, hashed = entry.split(":", 1)
            users[name.strip().lower()] = hashed.strip()
    return users


def load_users() -> dict[str, str]:
    return parse_users(os.environ.get("ADMIN_USERS", ""))


def is_rate_limited(client_ip: str) -> bool:
    now = time.time()
    _failures[client_ip] = [t for t in _failures[client_ip] if now - t < FAILURE_WINDOW_SEC]
    return len(_failures[client_ip]) >= MAX_FAILURES


def record_failure(client_ip: str):
    _failures[client_ip].append(time.time())


def authenticate(username: str, password: str) -> str | None:
    """Return the normalized username if credentials are valid, else None."""
    users = load_users()
    name = (username or "").strip().lower()
    stored = users.get(name)
    if stored is None:
        # Spend similar time on unknown users to avoid leaking which names exist
        verify_password(password or "", hash_password("dummy"))
        return None
    return name if verify_password(password or "", stored) else None

