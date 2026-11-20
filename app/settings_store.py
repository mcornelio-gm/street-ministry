"""Site settings stored in data/settings.json (symlinked to bucket Data/ on Cloud Run)."""
import json
import os
import threading
from urllib.parse import urlparse

from app import routes_data

_lock = threading.Lock()


def _settings_path() -> str:
    return os.path.join(routes_data.DATA_DIR, "settings.json")


def get_settings() -> dict:
    """Return site settings dict (defaults if settings.json does not exist yet)."""
    path = _settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def get_share_url() -> str:
    """Return the configured custom QR/share URL, or '' to use the current site's Home URL."""
    return (get_settings().get("share_url") or "").strip()


def save_share_url(raw_url: str) -> str:
    """Validate and save share_url ('', or an http/https URL). Returns the saved value."""
    url = (raw_url or "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Please enter a valid http:// or https:// URL (or leave blank).")
    with _lock:
        data = get_settings()
        data["share_url"] = url
        path = _settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    return url
