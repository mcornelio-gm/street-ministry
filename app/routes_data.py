"""Routes data: data/routes.json (on Cloud Run, a symlink into the content bucket).

Format (a JSON list, pretty-printed so humans can read diffs):
[
  {
    "id": "r-7f3a9c",            # permanent; never changes when the route is renamed
    "name": "Irvington Campers",
    "leaders": "Karen & Joe",
    "schedule": "", "summary": "", "notes": "",
    "stops": [
      {"name": "Irvington Park", "address": "41825 Blacow Rd, Fremont, CA 94538",
       "city": "Fremont", "lat": 37.52, "lng": -121.96, "approx": false}
    ]
  }
]
"approx": true means the address lookup only found the general area; drag the pin in /admin.
"""
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DATA_FILE = os.path.join(DATA_DIR, "routes.json")

ROUTE_FIELDS = ("id", "name", "leaders", "schedule", "summary", "notes", "stops")
STOP_FIELDS = ("name", "address", "city", "lat", "lng", "approx")
CITY_ORDER = ["Fremont", "Newark", "Union City", "Hayward"]

_lock = threading.Lock()


class RoutesError(ValueError):
    pass


class VersionConflict(RoutesError):
    pass


# ---------- reading ----------

def _read_bytes() -> bytes:
    if not os.path.exists(DATA_FILE):
        return b"[]"
    with open(DATA_FILE, "rb") as f:
        return f.read()


def _version(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def _normalize(route: dict) -> dict:
    stops = []
    for s in route.get("stops") or []:
        stops.append({
            "name": str(s.get("name") or "").strip(),
            "address": str(s.get("address") or "").strip(),
            "city": s.get("city") or city_from_address(s.get("address") or ""),
            "lat": s.get("lat"),
            "lng": s.get("lng"),
            "approx": bool(s.get("approx")),
        })
    return {
        "id": route.get("id") or new_id(),
        "name": str(route.get("name") or "").strip(),
        "leaders": str(route.get("leaders") or "").strip(),
        "schedule": str(route.get("schedule") or "").strip(),
        "summary": str(route.get("summary") or "").strip(),
        "notes": str(route.get("notes") or "").strip(),
        "stops": stops,
    }


def load() -> tuple[list, str]:
    """Return (routes, version). Version changes whenever the file changes."""
    raw = _read_bytes()
    data = json.loads(raw.decode("utf-8") or "[]")
    return [_normalize(r) for r in data], _version(raw)


def get_all_routes() -> list:
    return load()[0]


def get_route(route_id: str) -> dict | None:
    return next((r for r in get_all_routes() if r["id"] == route_id), None)


def get_cities() -> list:
    cities = {s["city"] for r in get_all_routes() for s in r["stops"] if s["city"]}
    return sorted(cities, key=lambda c: (CITY_ORDER.index(c) if c in CITY_ORDER else len(CITY_ORDER), c))


def route_cities(route: dict) -> list:
    seen = []
    for s in route["stops"]:
        if s["city"] and s["city"] not in seen:
            seen.append(s["city"])
    return seen


# ---------- writing ----------

def _write(routes: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    ordered = [{k: r[k] for k in ROUTE_FIELDS} for r in routes]
    for r in ordered:
        r["stops"] = [{k: s[k] for k in STOP_FIELDS} for s in r["stops"]]
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, DATA_FILE)


def new_id() -> str:
    return "r-" + secrets.token_hex(3)


def save_route(route: dict, expected_version: str | None) -> dict:
    """Create or update a route. Raises VersionConflict if the file changed since
    the editor loaded it (another admin saved in the meantime)."""
    route = _normalize(route)
    if not route["name"]:
        raise RoutesError("Route name is required")
    if not route["stops"]:
        raise RoutesError("Add at least one stop")
    for s in route["stops"]:
        if not (s["name"] or s["address"]):
            raise RoutesError("Each stop needs a name or an address")
    with _lock:
        routes, version = load()
        if expected_version is not None and expected_version != version:
            raise VersionConflict("Someone else just edited the routes. Please reload and try again.")
        for i, r in enumerate(routes):
            if r["id"] == route["id"]:
                routes[i] = route
                break
        else:
            routes.append(route)
        _write(routes)
    return route


def delete_route(route_id: str, expected_version: str | None):
    with _lock:
        routes, version = load()
        if expected_version is not None and expected_version != version:
            raise VersionConflict("Someone else just edited the routes. Please reload and try again.")
        remaining = [r for r in routes if r["id"] != route_id]
        if len(remaining) == len(routes):
            raise RoutesError("Route not found")
        _write(remaining)


# ---------- addresses ----------

_CITY_RE = re.compile(r"(?:^|,)\s*([A-Za-z][A-Za-z .'-]*?)\s*,?\s+CA\b", re.I)


def city_from_address(address: str) -> str:
    m = _CITY_RE.search(address or "")
    return m.group(1).strip().title() if m else ""


def _has_street_number(text: str) -> bool:
    return any(re.match(r"^\s*\d+\s+\w", seg) for seg in (text or "").split(","))


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
USER_AGENT = "ResonateStreetMinistry/1.0 (route admin; contact mcornelio@gmail.com)"
# Greater Bay Area (west,north,east,south): lookups outside this box are rejected
SERVICE_AREA_VIEWBOX = "-122.7,38.1,-121.4,37.1"
SERVICE_AREA = {"lat": (37.1, 38.1), "lng": (-122.7, -121.4)}
_geo_lock = threading.Lock()
_last_call = [0.0]


class GeocodeUnavailable(RoutesError):
    """The lookup services couldn't be reached (network problem), as opposed to 'not found'."""


def _census(address: str) -> tuple[float, float] | None:
    """US Census geocoder: precise for US street addresses (free, no key)."""
    url = CENSUS_URL + "?" + urllib.parse.urlencode(
        {"address": address, "benchmark": "Public_AR_Current", "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            matches = json.loads(resp.read().decode("utf-8"))["result"]["addressMatches"]
    except Exception as e:
        print(f"census geocode failed for {address!r}: {e}")
        raise GeocodeUnavailable(str(e))
    if matches:
        c = matches[0]["coordinates"]
        return float(c["y"]), float(c["x"])
    return None


def _nominatim(query: str) -> tuple[float, float] | None:
    """One OpenStreetMap lookup. Respects the 1 request/second usage policy."""
    with _geo_lock:
        wait = 1.1 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        url = NOMINATIM_URL + "?" + urllib.parse.urlencode(
            {"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "us",
             # Only accept results inside the greater Bay Area (never "The Slabs, New Hampshire")
             "viewbox": SERVICE_AREA_VIEWBOX, "bounded": 1})
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                hits = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"geocode failed for {query!r}: {e}")
            raise GeocodeUnavailable(str(e))
    if hits:
        return float(hits[0]["lat"]), float(hits[0]["lon"])
    return None


def _in_service_area(lat: float, lng: float) -> bool:
    return (SERVICE_AREA["lat"][0] <= lat <= SERVICE_AREA["lat"][1]
            and SERVICE_AREA["lng"][0] <= lng <= SERVICE_AREA["lng"][1])


def geocode_stop(name: str, address: str) -> dict | None:
    """Find coordinates for a stop. Returns {"lat", "lng", "approx"}, or None if not found.
    Raises GeocodeUnavailable only if every lookup failed to connect.

    Order: US Census for street addresses (exact); then OpenStreetMap, limited to the
    Bay Area: 'name, address', then 'address'; for intersections ('A & B, City'),
    'A, City' (approximate). A bare stop name is never searched on its own."""
    attempts, failures = 0, 0

    def tried(fn, q):
        nonlocal attempts, failures
        attempts += 1
        try:
            hit = fn(q)
        except GeocodeUnavailable:
            failures += 1
            return None
        return hit if hit and _in_service_area(*hit) else None

    if address and _has_street_number(address):
        hit = tried(_census, address)
        if hit:
            return {"lat": round(hit[0], 6), "lng": round(hit[1], 6), "approx": False}

    candidates = []  # (query, approx)
    if name and address:
        candidates.append((f"{name}, {address}", False))
    if address:
        candidates.append((address, not _has_street_number(address)))
        first, _, rest = address.partition(",")
        if "&" in first and rest.strip():  # intersection: try the first street in that city
            candidates.append((f"{first.split('&')[0].strip()}, {rest.strip()}", True))
    elif name:
        candidates.append((name, True))
    seen = set()
    for q, approx in candidates:
        q = q.strip(", ")
        if not q or q in seen:
            continue
        seen.add(q)
        hit = tried(_nominatim, q)
        if hit:
            return {"lat": round(hit[0], 6), "lng": round(hit[1], 6), "approx": approx}
    if attempts and failures == attempts:
        raise GeocodeUnavailable("Address lookup services are unreachable")
    return None


def fill_missing_coordinates(route: dict, geocoder=None) -> list[str]:
    """Geocode stops without coordinates. Returns names of stops that couldn't be placed
    (not found, or lookup unavailable). Saving still succeeds; fix pins in /admin."""
    geocoder = geocoder or geocode_stop
    missing = []
    for s in route["stops"]:
        if s.get("lat") is None or s.get("lng") is None:
            try:
                hit = geocoder(s["name"], s["address"])
            except GeocodeUnavailable:
                hit = None
            if hit:
                s.update(hit)
            else:
                missing.append(s["name"] or s["address"])
    return missing


def get_routes_summary() -> dict:
    routes = get_all_routes()
    cities = get_cities()
    return {"total_routes": len(routes), "cities": cities, "city_count": len(cities)}
