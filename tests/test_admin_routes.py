"""Admin route editor tests. Uses a temporary routes.json and a fake geocoder
(no network, real data untouched)."""
import json

import pytest
from fastapi.testclient import TestClient

from app import auth, md_engine, routes_data
from app.main import app

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_USERS", f"tester:{auth.hash_password(PASSWORD)}")
    auth._failures.clear()
    data_file = tmp_path / "routes.json"
    data_file.write_text(json.dumps([{
        "id": "r-aaaaaa", "name": "Islanders", "leaders": "Michael & Henry",
        "stops": [{"name": "Islander Motel", "address": "29083 Mission Blvd, Hayward, CA 94544",
                   "lat": 37.63, "lng": -122.05}],
    }]))
    monkeypatch.setattr(routes_data, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes_data, "DATA_FILE", str(data_file))
    calls = []

    def fake_geocode(name, address):
        calls.append((name, address))
        if "nowhere" in address.lower():
            return None
        return {"lat": 37.5, "lng": -121.9, "approx": "CA 9" in address and not address[0].isdigit()}

    monkeypatch.setattr(routes_data, "geocode_stop", fake_geocode)
    monkeypatch.setattr(md_engine, "build_mkdocs", lambda: True)  # keep the real wiki untouched
    c = TestClient(app)
    c.post("/admin/login", data={"username": "tester", "password": PASSWORD})
    c.geocode_calls = calls
    return c


def form(version, **over):
    data = {
        "id": "", "version": version, "name": "Win O Bagels", "leaders": "Kent & Julian",
        "schedule": "Saturdays 9 AM", "summary": "", "notes": "Park behind Walmart",
        "stop_name": ["Walmart", "Warm Springs BART"],
        "stop_address": ["44009 Osgood Rd, Fremont, CA 94539", "Fremont, CA 94539"],
        "stop_lat": ["", ""], "stop_lng": ["", ""], "stop_approx": ["0", "0"],
    }
    data.update(over)
    return data


def test_requires_login():
    c = TestClient(app)
    assert c.get("/admin/routes/new", follow_redirects=False).headers["location"] == "/admin/login"
    assert c.post("/admin/routes/save", data={}, follow_redirects=False).headers["location"] == "/admin/login"
    assert c.get("/admin/geocode?address=x").status_code == 401


def test_dashboard_and_editor(client):
    r = client.get("/admin")
    assert "Routes" in r.text and "/admin/routes/r-aaaaaa" in r.text
    e = client.get("/admin/routes/r-aaaaaa")
    assert e.status_code == 200 and 'value="Islander Motel"' in e.text and 'name="version"' in e.text
    assert client.get("/admin/routes/new").status_code == 200


def test_create_route_geocodes_missing_stops(client):
    _, version = routes_data.load()
    r = client.post("/admin/routes/save", data=form(version), follow_redirects=False)
    assert r.status_code == 303
    route = next(x for x in routes_data.get_all_routes() if x["name"] == "Win O Bagels")
    assert [s["name"] for s in route["stops"]] == ["Walmart", "Warm Springs BART"]
    assert all(s["lat"] == 37.5 for s in route["stops"])
    assert route["stops"][1]["approx"] is True and route["stops"][0]["approx"] is False
    assert route["stops"][0]["city"] == "Fremont" and route["id"].startswith("r-")
    assert len(client.geocode_calls) == 2
    # Public page shows it
    assert "Win O Bagels" in client.get("/routes").text


def test_edit_keeps_hand_placed_pins_and_id(client):
    _, version = routes_data.load()
    data = form(version, id="r-aaaaaa", name="Islanders (renamed)",
                stop_name=["Islander Motel"], stop_address=["29083 Mission Blvd, Hayward, CA 94544"],
                stop_lat=["37.6400"], stop_lng=["-122.0600"], stop_approx=["0"])
    client.post("/admin/routes/save", data=data)
    route = routes_data.get_route("r-aaaaaa")
    assert route["name"] == "Islanders (renamed)"
    assert (route["stops"][0]["lat"], route["stops"][0]["lng"]) == (37.64, -122.06)
    assert client.geocode_calls == []  # coordinates present: no lookup


def test_stop_order_and_blank_rows(client):
    _, version = routes_data.load()
    data = form(version, stop_name=["B", "", "A"], stop_address=["2 B St, Newark, CA", "", "1 A St, Newark, CA"],
                stop_lat=["1", "", "2"], stop_lng=["1", "", "2"], stop_approx=["0", "0", "0"])
    client.post("/admin/routes/save", data=data)
    route = next(x for x in routes_data.get_all_routes() if x["name"] == "Win O Bagels")
    assert [s["name"] for s in route["stops"]] == ["B", "A"]  # order kept, blank row skipped


def test_validation_and_unplaceable_stop(client):
    _, version = routes_data.load()
    bad = client.post("/admin/routes/save", data=form(version, name=""))
    assert bad.status_code == 400 and "Route name is required" in bad.text
    none = client.post("/admin/routes/save", data=form(version, stop_name=[""], stop_address=[""],
                                                       stop_lat=[""], stop_lng=[""], stop_approx=["0"]))
    assert none.status_code == 400 and "at least one stop" in none.text
    r = client.post("/admin/routes/save", data=form(version, stop_name=["Lost"], stop_address=["Nowhere"],
                                                    stop_lat=[""], stop_lng=[""], stop_approx=["0"]),
                    follow_redirects=False)
    assert r.status_code == 303 and "couldn" in r.headers["location"]  # saved, but flagged


def test_concurrent_edit_is_rejected(client):
    _, stale = routes_data.load()
    client.post("/admin/routes/save", data=form(stale))           # admin A saves
    r = client.post("/admin/routes/save", data=form(stale, name="Other"))  # admin B, stale form
    assert r.status_code == 409 and "Someone else just edited" in r.text
    assert "Other" not in [x["name"] for x in routes_data.get_all_routes()]
    # Stale delete is refused too
    d = client.post("/admin/routes/r-aaaaaa/delete", data={"version": stale}, follow_redirects=False)
    assert "Someone+else" in d.headers["location"] and routes_data.get_route("r-aaaaaa")


def test_delete(client):
    _, version = routes_data.load()
    r = client.post("/admin/routes/r-aaaaaa/delete", data={"version": version}, follow_redirects=False)
    assert r.status_code == 303 and routes_data.get_route("r-aaaaaa") is None


def test_file_is_readable_json(client):
    _, version = routes_data.load()
    client.post("/admin/routes/save", data=form(version))
    text = open(routes_data.DATA_FILE, encoding="utf-8").read()
    assert text.startswith("[\n  {\n    \"id\"")  # pretty-printed, stable key order
    assert json.loads(text)[0]["stops"][0]["name"] == "Islander Motel"


def test_geocode_order_census_then_openstreetmap(monkeypatch):
    calls = []
    monkeypatch.setattr(routes_data, "_census", lambda q: calls.append(("census", q)) or (37.6366, -122.0544))
    monkeypatch.setattr(routes_data, "_nominatim", lambda q: calls.append(("osm", q)) or None)
    hit = routes_data.geocode_stop("76 Union Gas Station", "28590 Mission Blvd, Hayward, CA 94544")
    assert hit == {"lat": 37.6366, "lng": -122.0544, "approx": False}
    assert calls == [("census", "28590 Mission Blvd, Hayward, CA 94544")]  # street address: Census first

    calls.clear()
    monkeypatch.setattr(routes_data, "_nominatim", lambda q: calls.append(("osm", q)) or (37.5, -121.9))
    hit = routes_data.geocode_stop("Warm Springs BART", "Fremont, CA 94539")
    assert calls == [("osm", "Warm Springs BART, Fremont, CA 94539")]  # no street number: skip Census
    assert hit["approx"] is False  # found by place name


def test_geocode_unreachable_vs_not_found(monkeypatch):
    def down(q):
        raise routes_data.GeocodeUnavailable("blocked")
    monkeypatch.setattr(routes_data, "_census", down)
    monkeypatch.setattr(routes_data, "_nominatim", down)
    with pytest.raises(routes_data.GeocodeUnavailable):
        routes_data.geocode_stop("X", "1 Main St, Fremont, CA")
    route = {"stops": [{"name": "X", "address": "1 Main St, Fremont, CA", "lat": None, "lng": None}]}
    assert routes_data.fill_missing_coordinates(route) == ["X"]  # save still works, stop flagged

    monkeypatch.setattr(routes_data, "_census", lambda q: None)
    monkeypatch.setattr(routes_data, "_nominatim", lambda q: None)
    assert routes_data.geocode_stop("X", "1 Main St, Fremont, CA") is None  # genuinely not found


def test_geocode_endpoint_reports_unavailable(client, monkeypatch):
    def down(name, address):
        raise routes_data.GeocodeUnavailable("blocked")
    monkeypatch.setattr(routes_data, "geocode_stop", down)
    assert client.get("/admin/geocode?address=1+Main+St").json() == {"found": False, "unavailable": True}


def test_geocode_rejects_far_away_and_handles_intersections(monkeypatch):
    calls = []
    monkeypatch.setattr(routes_data, "_census", lambda q: None)

    def osm(q):
        calls.append(q)
        if q == "The Slabs":
            return (44.05, -71.17)  # New Hampshire: must never be used
        if q == "Washington Blvd, Fremont CA 94538":
            return (37.53, -121.95)
        return None
    monkeypatch.setattr(routes_data, "_nominatim", osm)
    hit = routes_data.geocode_stop("The Slabs", "Washington Blvd & Driscoll Road, Fremont CA 94538")
    assert hit == {"lat": 37.53, "lng": -121.95, "approx": True}  # first street, flagged approximate
    assert "The Slabs" not in calls  # a bare name is never searched when there's an address

    # Results outside the Bay Area are discarded even if a service returns them
    monkeypatch.setattr(routes_data, "_nominatim", lambda q: (44.05, -71.17))
    assert routes_data.geocode_stop("The Slabs", "Somewhere, Fremont, CA") is None
