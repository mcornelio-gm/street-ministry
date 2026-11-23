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
import re
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


def users_path() -> str:
    mount = os.environ.get("CONTENT_MOUNT")
    if mount:
        return os.path.join(mount, "Private", "admin_users.json")
    return os.path.join(PROJECT_ROOT, ".admin_users.json")


def _load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, type(default)) else default
    except (FileNotFoundError, ValueError):
        return default


def _write_private_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_overrides() -> dict:
    return _load_json(overrides_path(), {})


def _load_user_store() -> dict:
    data = _load_json(users_path(), {})
    return {
        "added": data.get("added") if isinstance(data.get("added"), dict) else {},
        "removed": data.get("removed") if isinstance(data.get("removed"), dict) else {},
    }


def load_users() -> dict[str, str]:
    """Current password hash per admin: base ADMIN_USERS minus any UI-removed entries
    (unless superseded by a new hash in ADMIN_USERS), plus UI-added admins, with
    self-chosen password overrides applied."""
    users = base_users()
    store = _load_user_store()
    for name, base_hash in store["removed"].items():
        if users.get(name) == base_hash:
            users.pop(name, None)
    for name, entry in store["added"].items():
        if isinstance(entry, dict) and entry.get("hash"):
            users[name] = entry["hash"]
    for name, entry in _load_overrides().items():
        if name in users and entry.get("replaces") == users[name]:
            users[name] = entry["hash"]
    return users


def list_admins() -> list[dict]:
    base = base_users()
    users = load_users()
    return [
        {"username": name, "source": "Owner" if name in base else "Added"}
        for name in sorted(users)
    ]


_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")


def add_admin(username: str, password: str, confirm: str, added_by: str = "") -> str:
    """Create a new admin user in the private user store. Raises ValueError on invalid input."""
    name = (username or "").strip().lower()
    if not _USERNAME_RE.match(name):
        raise ValueError("Username must be 2–32 characters (letters, numbers, - or _).")
    if name in load_users():
        raise ValueError(f"Admin '{name}' already exists.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if password != confirm:
        raise ValueError("The passwords don't match.")
    with _pw_lock:
        store = _load_user_store()
        store["removed"].pop(name, None)
        store["added"][name] = {
            "hash": hash_password(password),
            "added_by": (added_by or "").strip().lower(),
            "created": int(time.time()),
        }
        _write_private_json(users_path(), store)
    return name


def delete_admin(username: str):
    """Remove an admin user (including self-deletion). Prevents deleting the last admin."""
    name = (username or "").strip().lower()
    users = load_users()
    if name not in users:
        raise ValueError(f"Admin '{name}' was not found.")
    if len(users) <= 1:
        raise ValueError("Cannot delete the last remaining admin.")
    base = base_users()
    with _pw_lock:
        store = _load_user_store()
        store["added"].pop(name, None)
        if name in base:
            store["removed"][name] = base[name]
        _write_private_json(users_path(), store)
        overrides = _load_overrides()
        if name in overrides:
            overrides.pop(name, None)
            _write_private_json(overrides_path(), overrides)


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
        store = _load_user_store()
        replaces = base.get(name) if name in base and store["removed"].get(name) != base[name] else store["added"].get(name, {}).get("hash")
        data = _load_overrides()
        data[name] = {"hash": hash_password(new), "replaces": replaces, "changed": int(time.time())}
        _write_private_json(overrides_path(), data)


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

