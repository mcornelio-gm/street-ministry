"""Smoke tests for the Street Ministry site. Run: .venv/bin/pytest -q"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_renders_announcements_pinned_then_newest():
    from app import md_engine
    r = client.get("/")
    assert r.status_code == 200
    assert "Stepping beyond walls" in r.text
    items = md_engine.get_announcements()
    assert items[0]["pinned"] is True  # Welcome announcement is pinned at top
    # Pinned first, then newest first
    keys = [(a["pinned"], a["date_sort"], a["filename"]) for a in items]
    assert keys == sorted(keys, reverse=True)
    positions = [r.text.index(a["title"].replace("&", "&amp;")) for a in items]
    assert positions == sorted(positions)
    # Announcement detail page works
    d = client.get(f"/announcements/{items[0]['slug']}")
    assert d.status_code == 200 and items[0]["title"].replace("&", "&amp;") in d.text
    assert client.get("/announcements/nope").status_code == 404


def test_documents_mkdocs_site():
    r = client.get("/documents/")
    assert r.status_code == 200
    assert "Street Ministry Documentation" in r.text
    assert client.get("/documents/Guides/volunteer-guide/").status_code == 200
    assert client.get("/documents/search/search_index.json").status_code == 200


def test_stories_list_and_detail():
    r = client.get("/stories")
    assert r.status_code == 200
    for city in ("Fremont", "Newark", "Union City", "Hayward"):
        assert city in r.text
    d = client.get("/stories/2026-11-04-marcus-peralta-creek")
    assert d.status_code == 200
    assert "Broken Tent Zipper" in d.text
    assert client.get("/stories/does-not-exist").status_code == 404


def test_guides_list_and_detail():
    r = client.get("/guides")
    assert r.status_code == 200
    assert "Volunteer Orientation &amp; Safety Briefing" in r.text
    d = client.get("/guides/volunteer-guide")
    assert d.status_code == 200
    assert "Kent Mitchell, (510) 709-9338" in d.text
    assert '<pre class="mermaid">' in d.text  # flowchart renders via mermaid.js
    assert "Christlike Posture" in d.text
    assert client.get("/guides/nope").status_code == 404
    # Guides tab in the nav, and the Welcome announcement links to the guide
    assert 'href="/guides"' in client.get("/").text
    assert "/guides/volunteer-guide" in client.get("/announcements/2026-11-11-welcome-to-resonate-street-ministry").text


def test_theme_toggle_markup():
    for path in ("/", "/stories", "/guides", "/teams", "/routes", "/about", "/admin/login"):
        html = client.get(path).text
        meta = html.index('<meta name="color-scheme" content="light dark">')
        # FOUC guard: inline script applies the stored choice right after the meta,
        # before the stylesheet loads
        script = html.index('localStorage.getItem("sm-color-scheme")')
        assert meta < script < html.index('/static/css/style.css'), path
        assert 'id="themeToggle"' in html and "Switch to dark theme" in html, path
    css = client.get("/static/css/style.css").text
    assert ":root { color-scheme: light dark; }" in css  # base must stay "light dark"
    # Cache-busting: the stylesheet URL carries a hash of its content
    import hashlib, re
    v = re.search(r'/static/css/style\.css\?v=([0-9a-f]+)"', client.get("/").text).group(1)
    assert v == hashlib.sha256(open("app/static/css/style.css", "rb").read()).hexdigest()[:10]
    wiki = client.get("/documents/").text
    assert "sm-color-scheme" in wiki and "__palette" in wiki  # wiki shares the choice


def test_routes_page_and_api():
    from app import routes_data
    routes = routes_data.get_all_routes()
    r = client.get("/routes")
    assert r.status_code == 200
    assert "outreachMap" in r.text
    for route in routes:  # every route and stop is listed (content-independent)
        assert route["name"].replace("&", "&amp;") in r.text
        for s in route["stops"]:
            assert s["name"].replace("&", "&amp;").replace("'", "&#39;") in r.text
    for city in routes_data.get_cities():  # city filter comes from the addresses
        assert f'data-city="{city}"' in r.text
    data = client.get("/api/routes").json()
    assert len(data) == len(routes)
    assert {"id", "name", "leaders", "stops"} <= set(data[0])
    assert {"name", "address", "city", "lat", "lng"} <= set(data[0]["stops"][0])


def test_city_from_address():
    from app.routes_data import city_from_address as c
    assert c("29083 Mission Blvd, Hayward, CA 94544") == "Hayward"
    assert c("3560 Washington Blvd, Fremont CA 94538") == "Fremont"
    assert c("Union City, CA 94587") == "Union City"
    assert c("160 N Main St, Milpitas, CA 95035") == "Milpitas"


def test_csv_import_parsing(tmp_path):
    import sys
    sys.path.insert(0, "scripts")
    from import_routes_csv import parse_csv
    p = tmp_path / "r.csv"
    p.write_text('Route, Leaders, Locations\n'
                 'Irvington Campers, Karen & Joe, "Irvington Park, Irvington Park, 41825 Blacow Rd, '
                 'Fremont, CA 94538; The Slabs, 3560 Washington Blvd, Fremont CA 94538"\n')
    (route,) = parse_csv(str(p))
    assert route["name"] == "Irvington Campers" and route["leaders"] == "Karen & Joe"
    assert [s["name"] for s in route["stops"]] == ["Irvington Park", "The Slabs"]
    assert route["stops"][0]["address"] == "41825 Blacow Rd, Fremont, CA 94538"  # dup name dropped
    assert route["stops"][1]["city"] == "Fremont"


def test_teams_and_about_list_and_detail():
    r_teams = client.get("/teams")
    assert r_teams.status_code == 200
    assert "Volunteer With Our Teams" in r_teams.text
    assert client.get("/teams/volunteer-with-our-teams").status_code == 200
    assert client.get("/teams/nope").status_code == 404

    r_about = client.get("/about")
    assert r_about.status_code == 200
    for title in (
        "Who We Are",
        "The Street Reality in Alameda County",
        "Critical Needs Checklist",
        "Contact Ministry Leadership",
        "Emergency Contacts",
    ):
        assert title in r_about.text
    assert client.get("/about/who-we-are").status_code == 200
    assert client.get("/about/emergency-contacts").status_code == 200
    assert client.get("/about/nope").status_code == 404

    # Header order: Welcome -> Guides -> Stories -> Teams -> Routes -> About (no Documents links)
    home = client.get("/").text
    assert 'href="/documents/' not in home
    assert (
        home.index('href="/guides"')
        < home.index('href="/stories"')
        < home.index('href="/teams"')
        < home.index('href="/routes"')
        < home.index('href="/about"')
    )
    assert "Join a Route" not in home


def test_generated_wiki_indexes_are_current():
    from hooks import (
        ABOUT_MD, ANNOUNCEMENTS_MD, GUIDES_MD, ROUTES_MD, STORIES_MD, TEAMS_MD,
        render_about_md, render_announcements_md, render_guides_md,
        render_routes_md, render_stories_md, render_teams_md,
    )
    from app import md_engine
    from app.routes_data import get_all_routes

    expected = {
        ROUTES_MD: render_routes_md(get_all_routes()),
        STORIES_MD: render_stories_md(md_engine.get_stories()),
        ANNOUNCEMENTS_MD: render_announcements_md(md_engine.get_announcements()),
        GUIDES_MD: render_guides_md(md_engine.get_guides()),
        TEAMS_MD: render_teams_md(md_engine.get_teams()),
        ABOUT_MD: render_about_md(md_engine.get_about_docs()),
    }
    for path, content in expected.items():
        with open(path, encoding="utf-8") as f:
            assert f.read() == content, f"{path} is stale; run `mkdocs build`"


def test_static_and_health():
    assert client.get("/static/img/logo.png").status_code == 200
    assert client.get("/static/css/style.css").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}

