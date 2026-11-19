"""Import routes from a CSV (replaces data/routes.json) and look up stop coordinates.

CSV columns: Route, Leaders, Locations
  Locations: stops separated by ';'. Each stop is "Name, street, city, CA zip";
  the first comma-separated part is the stop name, the rest is the address.

Usage:
  .venv/bin/python scripts/import_routes_csv.py routes.csv            # replace all routes
  .venv/bin/python scripts/import_routes_csv.py routes.csv --dry-run  # preview only

Needs internet access (OpenStreetMap lookup, ~1 second per stop). Stops it can't
place precisely are flagged "approx"; fix them by dragging the pin in /admin.
On the live site, edit routes in /admin instead, or push with scripts/sync_content.sh.
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import routes_data as rd  # noqa: E402


def parse_stop(text: str) -> dict:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    name, rest = (parts[0], parts[1:]) if parts else ("", [])
    if rest and rest[0].lower() == name.lower():  # "Irvington Park, Irvington Park, 41825 ..."
        rest = rest[1:]
    return {"name": name, "address": ", ".join(rest)}


def parse_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, skipinitialspace=True))
    header = [h.strip().lower() for h in rows[0]]
    idx = {k: header.index(k) for k in ("route", "leaders", "locations")}
    routes = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        stops = [parse_stop(s) for s in row[idx["locations"]].split(";") if s.strip()]
        routes.append(rd._normalize({
            "name": row[idx["route"]], "leaders": row[idx["leaders"]], "stops": stops,
        }))
    return routes


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    routes = parse_csv(sys.argv[1])
    for r in routes:
        missing = rd.fill_missing_coordinates(r)
        print(f"\n{r['name']}  ({r['leaders']})")
        for i, s in enumerate(r["stops"], 1):
            where = "NOT FOUND" if s["lat"] is None else f"{s['lat']:.5f},{s['lng']:.5f}"
            flag = "  <- approximate, check in /admin" if s["approx"] else ""
            print(f"  {i}. {s['name']} | {s['address']} | {s['city'] or '?'} | {where}{flag}")
        if missing:
            print(f"  !! could not locate: {', '.join(missing)}")
    if "--dry-run" in sys.argv:
        print("\n(dry run: nothing written)")
        return
    rd._write(routes)
    print(f"\nWrote {len(routes)} routes to {os.path.relpath(rd.DATA_FILE, ROOT)}")


if __name__ == "__main__":
    main()
