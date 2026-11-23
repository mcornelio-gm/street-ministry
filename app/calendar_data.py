"""Perpetual outreach calendar: data/events.json (synced with Cloud Storage Data/events.json)."""
import calendar as pycal
import hashlib
import json
import os
import secrets
import threading
from datetime import date, datetime, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DATA_FILE = os.path.join(DATA_DIR, "events.json")

RECURRENCE_TYPES = ("once", "weekly", "biweekly", "monthly")
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHLY_WEEK_LABELS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", -1: "Last"}
CITIES = ["", "Fremont", "Newark", "Union City", "Hayward", "All Cities"]

_lock = threading.Lock()


class CalendarError(ValueError):
    pass


def new_id() -> str:
    return "ev-" + secrets.token_hex(3)


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _normalize(ev: dict) -> dict:
    rec = ev.get("recurrence", "once")
    if rec not in RECURRENCE_TYPES:
        rec = "once"
    weekdays = sorted({int(w) for w in (ev.get("weekdays") or []) if str(w).isdigit() and 0 <= int(w) <= 6})
    mweeks = []
    for w in (ev.get("monthly_weeks") or []):
        try:
            wi = int(w)
            if wi in (1, 2, 3, 4, -1) and wi not in mweeks:
                mweeks.append(wi)
        except (TypeError, ValueError):
            pass
    skips = sorted({s.strip() for s in (ev.get("skip_dates") or []) if _parse_date(s)})
    return {
        "id": (ev.get("id") or new_id()).strip(),
        "title": (ev.get("title") or "").strip(),
        "recurrence": rec,
        "start_date": (ev.get("start_date") or "").strip(),
        "end_date": (ev.get("end_date") or "").strip(),
        "weekdays": weekdays,
        "monthly_weeks": mweeks,
        "time": (ev.get("time") or "").strip(),
        "city": (ev.get("city") or "").strip(),
        "location": (ev.get("location") or "").strip(),
        "notes": (ev.get("notes") or "").strip(),
        "skip_dates": skips,
    }


def load() -> tuple[list[dict], str]:
    if not os.path.exists(DATA_FILE):
        return [], hashlib.sha256(b"[]").hexdigest()[:16]
    with open(DATA_FILE, "rb") as f:
        raw = f.read()
    ver = hashlib.sha256(raw).hexdigest()[:16]
    try:
        data = json.loads(raw.decode("utf-8"))
        items = [_normalize(e) for e in data] if isinstance(data, list) else []
    except ValueError:
        items = []
    return items, ver


def list_events() -> list[dict]:
    events, _ = load()
    return sorted(events, key=lambda e: (e["start_date"], e["title"].lower()))


def get_event(event_id: str) -> dict | None:
    for ev in list_events():
        if ev["id"] == event_id:
            return ev
    return None


def _save_all(events: list[dict]):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)


def save_event(ev: dict) -> dict:
    norm = _normalize(ev)
    if not norm["title"]:
        raise CalendarError("Event title is required.")
    sd = _parse_date(norm["start_date"])
    if not sd:
        raise CalendarError("Valid start date (YYYY-MM-DD) is required.")
    if norm["end_date"]:
        ed = _parse_date(norm["end_date"])
        if not ed:
            raise CalendarError("End date must be YYYY-MM-DD.")
        if ed < sd:
            raise CalendarError("End date cannot be before start date.")
    with _lock:
        events, _ = load()
        idx = next((i for i, x in enumerate(events) if x["id"] == norm["id"]), None)
        if idx is None:
            events.append(norm)
        else:
            events[idx] = norm
        _save_all(events)
    return norm


def delete_event(event_id: str) -> bool:
    with _lock:
        events, _ = load()
        kept = [e for e in events if e["id"] != event_id]
        if len(kept) == len(events):
            return False
        _save_all(kept)
    return True


def recurrence_label(ev: dict) -> str:
    rec = ev.get("recurrence", "once")
    sd = _parse_date(ev.get("start_date", ""))
    days = ev.get("weekdays") or ([sd.weekday()] if sd else [])
    day_str = ", ".join(WEEKDAY_NAMES[d] for d in days)
    if rec == "once":
        return "One-time"
    if rec == "weekly":
        return f"Every {day_str}" if day_str else "Weekly"
    if rec == "biweekly":
        return f"Every 2 weeks ({day_str})" if day_str else "Every 2 weeks"
    if rec == "monthly":
        mweeks = ev.get("monthly_weeks") or []
        if mweeks:
            wk_str = ", ".join(MONTHLY_WEEK_LABELS.get(w, str(w)) for w in mweeks)
            return f"Monthly ({wk_str} {day_str})"
        return f"Monthly (day {sd.day})" if sd else "Monthly"
    return rec.title()


def _matches_monthly(d: date, sd: date, weekdays: list[int], mweeks: list[int]) -> bool:
    if not mweeks:
        return d.day == sd.day
    target_days = weekdays or [sd.weekday()]
    if d.weekday() not in target_days:
        return False
    nth = (d.day - 1) // 7 + 1
    _, last_day = pycal.monthrange(d.year, d.month)
    is_last = (d.day + 7) > last_day
    return (nth in mweeks) or (-1 in mweeks and is_last)


def occurs_on(ev: dict, d: date) -> bool:
    sd = _parse_date(ev.get("start_date", ""))
    if not sd or d < sd:
        return False
    ed = _parse_date(ev.get("end_date", ""))
    if ed and d > ed:
        return False
    if d.isoformat() in (ev.get("skip_dates") or []):
        return False
    rec = ev.get("recurrence", "once")
    if rec == "once":
        return d == sd
    weekdays = ev.get("weekdays") or [sd.weekday()]
    if rec == "weekly":
        return d.weekday() in weekdays
    if rec == "biweekly":
        if d.weekday() not in weekdays:
            return False
        sd_mon = sd - timedelta(days=sd.weekday())
        d_mon = d - timedelta(days=d.weekday())
        return ((d_mon - sd_mon).days // 7) % 2 == 0
    if rec == "monthly":
        return _matches_monthly(d, sd, weekdays, ev.get("monthly_weeks") or [])
    return False


def expand_range(events: list[dict], range_start: date, range_end: date) -> list[dict]:
    out = []
    cur = range_start
    while cur <= range_end:
        for ev in events:
            if occurs_on(ev, cur):
                out.append({
                    "event_id": ev["id"],
                    "date": cur,
                    "date_str": cur.isoformat(),
                    "date_display": cur.strftime("%a, %b %-d"),
                    "title": ev.get("title", ""),
                    "time": ev.get("time", ""),
                    "city": ev.get("city", ""),
                    "location": ev.get("location", ""),
                    "notes": ev.get("notes", ""),
                    "recurrence": ev.get("recurrence", "once"),
                    "recurrence_label": recurrence_label(ev),
                })
        cur += timedelta(days=1)
    return out


def month_grid(events: list[dict], year: int, month: int, today: date | None = None) -> dict:
    today = today or date.today()
    cal = pycal.Calendar(firstweekday=6)  # Sunday first
    weeks_dates = cal.monthdatescalendar(year, month)
    first_d, last_d = weeks_dates[0][0], weeks_dates[-1][-1]
    occs = expand_range(events, first_d, last_d)
    by_day: dict[str, list[dict]] = {}
    for o in occs:
        by_day.setdefault(o["date_str"], []).append(o)
    weeks = []
    for wk in weeks_dates:
        row = []
        for d in wk:
            ds = d.isoformat()
            row.append({
                "date": d,
                "date_str": ds,
                "day": d.day,
                "in_month": d.month == month,
                "is_today": d == today,
                "occurrences": by_day.get(ds, []),
            })
        weeks.append(row)
    month_occs = [o for o in occs if o["date"].month == month]
    return {
        "year": year,
        "month": month,
        "month_name": date(year, month, 1).strftime("%B %Y"),
        "weeks": weeks,
        "occurrences": month_occs,
    }


def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(events: list[dict], days_ahead: int = 180) -> str:
    today = date.today()
    occs = expand_range(events, today - timedelta(days=7), today + timedelta(days=days_ahead))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Resonate Church Street Ministry//Calendar//EN",
        "X-WR-CALNAME:Resonate Street Ministry",
    ]
    for o in occs:
        d_compact = o["date"].strftime("%Y%m%d")
        d_next = (o["date"] + timedelta(days=1)).strftime("%Y%m%d")
        summary = f"{o['title']} ({o['time']})" if o["time"] else o["title"]
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{o['event_id']}-{d_compact}@resonate-street-ministry",
            f"DTSTART;VALUE=DATE:{d_compact}",
            f"DTEND;VALUE=DATE:{d_next}",
            f"SUMMARY:{_ics_escape(summary)}",
        ])
        if o["location"]:
            lines.append(f"LOCATION:{_ics_escape(o['location'])}")
        desc_parts = [p for p in (o["city"], o["notes"]) if p]
        if desc_parts:
            lines.append(f"DESCRIPTION:{_ics_escape(' — '.join(desc_parts))}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
