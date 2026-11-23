"""Visitor comments & questions stored privately outside git.

On Cloud Run: <CONTENT_MOUNT>/Private/messages.json
Locally:      .messages.json (gitignored & dockerignored)
"""
import json
import os
import secrets
import threading
import time
from collections import defaultdict
from datetime import datetime

MESSAGE_TYPES = ["Question", "Comment", "Volunteer", "Event", "Story", "Donations", "Prayer"]
REPLY_REQUIRED_TYPES = {"Question", "Volunteer"}

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_lock = threading.Lock()
_rate: dict[str, list[float]] = defaultdict(list)
MAX_PER_WINDOW = 5
WINDOW_SEC = 15 * 60


def messages_path() -> str:
    mount = os.environ.get("CONTENT_MOUNT")
    if mount:
        return os.path.join(mount, "Private", "messages.json")
    return os.path.join(PROJECT_ROOT, ".messages.json")


def _load_raw() -> list[dict]:
    try:
        with open(messages_path(), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError):
        return []


def _write_raw(items: list[dict]):
    path = messages_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def is_rate_limited(client_ip: str) -> bool:
    now = time.time()
    _rate[client_ip] = [t for t in _rate[client_ip] if now - t < WINDOW_SEC]
    return len(_rate[client_ip]) >= MAX_PER_WINDOW


def record_submission(client_ip: str):
    _rate[client_ip].append(time.time())


def list_messages() -> list[dict]:
    """Return messages with unread first, then newest first."""
    items = _load_raw()
    items.sort(key=lambda m: (not m.get("read", False), m.get("created_at", "")), reverse=True)
    return items


def unread_count() -> int:
    return sum(1 for m in _load_raw() if not m.get("read", False))


def add_message(name: str, contact: str, types: list[str], body: str, honeypot: str = "") -> dict | None:
    """Validate and save a visitor message. Returns None if caught by honeypot."""
    if (honeypot or "").strip():
        return None
    clean_types = [t for t in types if t in MESSAGE_TYPES] or ["Comment"]
    clean_name = (name or "").strip()[:100]
    clean_contact = (contact or "").strip()[:160]
    clean_body = (body or "").strip()
    if not clean_body:
        raise ValueError("Please write a comment or question.")
    if len(clean_body) > 2000:
        raise ValueError("Message is too long (2000 character limit).")
    if any(t in REPLY_REQUIRED_TYPES for t in clean_types) and not clean_contact:
        raise ValueError("Please include an email or phone number so we can reply.")

    now = datetime.now()
    entry = {
        "id": "m-" + secrets.token_hex(4),
        "name": clean_name or "Anonymous",
        "contact": clean_contact,
        "types": clean_types,
        "body": clean_body,
        "created_at": now.isoformat(timespec="seconds"),
        "created_display": now.strftime("%b %d, %Y %I:%M %p").replace(" 0", " "),
        "read": False,
    }
    with _lock:
        items = _load_raw()
        items.append(entry)
        _write_raw(items)
    return entry


def toggle_read(msg_id: str) -> bool:
    """Flip read status of a message. Returns True if found."""
    with _lock:
        items = _load_raw()
        for m in items:
            if m.get("id") == msg_id:
                m["read"] = not m.get("read", False)
                _write_raw(items)
                return True
    return False


def delete_message(msg_id: str) -> bool:
    with _lock:
        items = _load_raw()
        remaining = [m for m in items if m.get("id") != msg_id]
        if len(remaining) == len(items):
            return False
        _write_raw(remaining)
    return True
