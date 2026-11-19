"""Admin authentication: username + PBKDF2-hashed password list.

ADMIN_USERS env var format (comma-separated):
    alice:pbkdf2_sha256$200000$<salt>$<hash>,bob:pbkdf2_sha256$...
Locally it comes from .env (gitignored); on Cloud Run from Secret Manager.
Manage users with: .venv/bin/python scripts/manage_admins.py
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
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


def base_users() -> dict[str, str]:
    """Admins and their owner-set password hashes (ADMIN_USERS: .env / Secret Manager)."""
    return parse_users(os.environ.get("ADMIN_USERS", ""))


# ---------- self-service password changes ----------
# Stored outside the repo so hashes never reach git: on Cloud Run in the bucket's private
# "Private/" folder (not synced to GitHub); locally in .admin_passwords.json (gitignored).
# Each entry records which owner-set hash it replaces, so an owner reset (new hash in
# ADMIN_USERS) automatically wins over a password the user chose earlier.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MIN_PASSWORD_LENGTH = 10
_pw_lock = threading.Lock()


def overrides_path() -> str:
    mount = os.environ.get("CONTENT_MOUNT")
    if mount:
        return os.path.join(mount, "Private", "admin_passwords.json")
    return os.path.join(PROJECT_ROOT, ".admin_passwords.json")


def _load_overrides() -> dict:
    try:
        with open(overrides_path(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def load_users() -> dict[str, str]:
    """Current password hash per admin: a self-chosen one if set (and not superseded by an
    owner reset), otherwise the owner-set one. Only users in ADMIN_USERS exist."""
    users = base_users()
    for name, entry in _load_overrides().items():
        if name in users and entry.get("replaces") == users[name]:
            users[name] = entry["hash"]
    return users


def change_password(username: str, current: str, new: str, confirm: str):
    """Raise ValueError with a user-facing message if the change isn't allowed."""
    name = (username or "").strip().lower()
    if authenticate(name, current) != name:
        raise ValueError("Your current password is incorrect.")
    if len(new) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if new != confirm:
        raise ValueError("The new passwords don't match.")
    if new == current:
        raise ValueError("Choose a password different from your current one.")
    base = base_users()
    with _pw_lock:
        data = _load_overrides()
        data[name] = {"hash": hash_password(new), "replaces": base[name], "changed": int(time.time())}
        path = overrides_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def password_fingerprint(username: str) -> str | None:
    """Short fingerprint of the user's current hash, stored in the session cookie.
    When the password changes (or the user is removed) older sessions stop working."""
    stored = load_users().get((username or "").strip().lower())
    return hashlib.sha256(stored.encode()).hexdigest()[:16] if stored else None


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

