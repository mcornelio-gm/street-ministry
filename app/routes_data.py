import json
import os

DATA_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "routes.json"))


def get_all_routes() -> list:
    """Load routes from data/routes.json."""
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_cities() -> list:
    """Return distinct sorted list of cities from routes."""
    routes = get_all_routes()
    cities = sorted(list(set(r.get("city", "East Bay") for r in routes)))
    return cities


def get_routes_summary() -> dict:
    """Return aggregated stats for hero banner."""
    routes = get_all_routes()
    cities = get_cities()
    return {
        "total_routes": len(routes),
        "cities": cities,
        "city_count": len(cities),
    }

