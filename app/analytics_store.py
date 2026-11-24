"""Privacy-friendly page visit analytics stored privately outside git.

On Cloud Run: <CONTENT_MOUNT>/Private/analytics.json
Locally:      .analytics.json (gitignored & dockerignored)

Stores salted hashes of visitor IPs (never raw IPs) to compute Unique Visitors
and Total Views per public page/article.
"""
import hashlib
import json
import os
import secrets
import threading
from datetime import date, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_lock = threading.Lock()

SKIP_PREFIXES = ("/static/", "/admin", "/api/", "/documents/", "/health", "/calendar.ics", "/favicon", "/robots.txt")
BOT_MARKERS = ("bot", "crawl", "spider", "slurp", "headless", "curl/", "wget/", "python-", "uptime", "probe")
MAX_DAYS_KEPT = 90


def analytics_path() -> str:
    mount = os.environ.get("CONTENT_MOUNT")
    if mount:
        return os.path.join(mount, "Private", "analytics.json")
    return os.path.join(PROJECT_ROOT, ".analytics.json")


def _default_data() -> dict:
    return {"salt": secrets.token_hex(16), "days": {}, "totals": {}}


def _load_raw() -> dict:
    try:
        with open(analytics_path(), encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("days"), dict) and isinstance(data.get("totals"), dict):
                if not data.get("salt"):
                    data["salt"] = secrets.token_hex(16)
                return data
    except (FileNotFoundError, ValueError):
        pass
    return _default_data()


def _write_raw(data: dict):
    path = analytics_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def should_track(path: str, user_agent: str = "") -> bool:
    if not path or any(path.startswith(p) for p in SKIP_PREFIXES):
        return False
    ua = (user_agent or "").lower()
    if any(b in ua for b in BOT_MARKERS):
        return False
    return True


def _hash_ip(salt: str, ip: str) -> str:
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()[:12]


def record_visit(path: str, client_ip: str, user_agent: str = "", today: date | None = None):
    if not should_track(path, user_agent):
        return
    day_key = (today or date.today()).isoformat()
    cutoff = ((today or date.today()) - timedelta(days=MAX_DAYS_KEPT)).isoformat()
    with _lock:
        data = _load_raw()
        vid = _hash_ip(data["salt"], client_ip or "unknown")

        day_bucket = data["days"].setdefault(day_key, {})
        p_day = day_bucket.setdefault(path, {"views": 0, "visitors": []})
        p_day["views"] += 1
        if vid not in p_day["visitors"] and len(p_day["visitors"]) < 5000:
            p_day["visitors"].append(vid)

        p_tot = data["totals"].setdefault(path, {"views": 0, "visitors": []})
        p_tot["views"] += 1
        if vid not in p_tot["visitors"] and len(p_tot["visitors"]) < 10000:
            p_tot["visitors"].append(vid)

        # Prune old daily buckets beyond MAX_DAYS_KEPT
        for old_k in [k for k in data["days"] if k < cutoff]:
            data["days"].pop(old_k, None)

        _write_raw(data)


def _canonical_path(path: str) -> str:
    """Resolve renamed article slugs via content_store aliases so views stay combined."""
    parts = path.strip("/").split("/")
    if len(parts) == 2 and parts[0] in ("announcements", "stories", "guides", "teams", "about"):
        from app import content_store
        kind, slug = parts
        aliases = content_store.all_aliases(kind)
        seen = set()
        while slug in aliases and slug not in seen:
            seen.add(slug)
            slug = aliases[slug]
        return f"/{kind}/{slug}"
    return path


def summary(today: date | None = None) -> dict:
    ref = today or date.today()
    d7 = (ref - timedelta(days=6)).isoformat()
    d30 = (ref - timedelta(days=29)).isoformat()
    today_str = ref.isoformat()

    with _lock:
        data = _load_raw()

    days = data["days"]
    totals = data["totals"]

    # Group raw paths by their canonical (post-rename) path
    by_canon: dict[str, list[str]] = {}
    raw_paths = set(totals.keys())
    for day_map in days.values():
        raw_paths.update(day_map.keys())
    for rp in raw_paths:
        by_canon.setdefault(_canonical_path(rp), []).append(rp)

    u_all, u_7, u_30 = set(), set(), set()
    v_all = v_7 = v_30 = 0
    pages = []

    for canon_path, group in by_canon.items():
        p_all_v = 0
        p_all_u = set()
        p_7_v = p_30_v = 0
        p_7_u, p_30_u = set(), set()

        for rp in group:
            tot_entry = totals.get(rp, {})
            p_all_v += int(tot_entry.get("views", 0))
            p_all_u.update(tot_entry.get("visitors", []))

            for dk, day_map in days.items():
                if dk > today_str or rp not in day_map:
                    continue
                entry = day_map[rp]
                cnt = int(entry.get("views", 0))
                vis = entry.get("visitors", [])
                if dk >= d30:
                    p_30_v += cnt
                    p_30_u.update(vis)
                if dk >= d7:
                    p_7_v += cnt
                    p_7_u.update(vis)

        v_all += p_all_v
        v_30 += p_30_v
        v_7 += p_7_v
        u_all.update(p_all_u)
        u_30.update(p_30_u)
        u_7.update(p_7_u)

        pages.append({
            "path": canon_path,
            "views_7d": p_7_v,
            "unique_7d": len(p_7_u),
            "views_30d": p_30_v,
            "unique_30d": len(p_30_u),
            "views_all": p_all_v,
            "unique_all": len(p_all_u),
        })

    pages.sort(key=lambda r: (-r["views_30d"], -r["views_all"], r["path"]))

    daily = []
    for i in range(14):
        d_str = (ref - timedelta(days=i)).isoformat()
        day_map = days.get(d_str, {})
        dv = sum(int(e.get("views", 0)) for e in day_map.values())
        du = set()
        for e in day_map.values():
            du.update(e.get("visitors", []))
        if dv or i == 0:
            daily.append({"date": d_str, "views": dv, "unique": len(du)})

    return {
        "views_7d": v_7,
        "unique_7d": len(u_7),
        "views_30d": v_30,
        "unique_30d": len(u_30),
        "views_all": v_all,
        "unique_all": len(u_all),
        "pages": pages,
        "daily": daily,
    }


def reset():
    with _lock:
        data = _load_raw()
        _write_raw({"salt": data["salt"], "days": {}, "totals": {}})
