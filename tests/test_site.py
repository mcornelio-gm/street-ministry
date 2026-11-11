"""Smoke tests for the Street Ministry site. Run: .venv/bin/pytest -q"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_welcome_renders_doc_and_announcements_sorted():
    r = client.get("/")
    assert r.status_code == 200
    assert "Loving Our Unhoused Neighbors" in r.text  # from Documents/Welcome.md
    # Announcements newest first
    nov = r.text.index("Urgent Need: Winter Tarps")
    oct28 = r.text.index("New Staging Location")
    oct15 = r.text.index("Quarterly Street Ministry Orientation")
    assert nov < oct28 < oct15


def test_documents_mkdocs_site():
    r = client.get("/documents/")
    assert r.status_code == 200
    assert "Street Ministry Documentation" in r.text
    assert client.get("/documents/Volunteer_Guide/").status_code == 200
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


def test_routes_page_and_api():
    r = client.get("/routes")
    assert r.status_code == 200
    assert "outreachMap" in r.text
    data = client.get("/api/routes").json()
    assert len(data) >= 4
    assert {"name", "city", "lat", "lng", "description"} <= set(data[0])


def test_generated_wiki_indexes_are_current():
    from hooks import (
        ANNOUNCEMENTS_MD, ROUTES_MD, STORIES_MD,
        render_announcements_md, render_routes_md, render_stories_md,
    )
    from app import md_engine
    from app.routes_data import get_all_routes

    expected = {
        ROUTES_MD: render_routes_md(get_all_routes()),
        STORIES_MD: render_stories_md(md_engine.get_stories()),
        ANNOUNCEMENTS_MD: render_announcements_md(md_engine.get_announcements()),
    }
    for path, content in expected.items():
        with open(path, encoding="utf-8") as f:
            assert f.read() == content, f"{path} is stale; run `mkdocs build`"


def test_about_and_contact():
    assert client.get("/about").status_code == 200
    r = client.post(
        "/contact",
        data={"name": "Test", "email": "t@example.com", "interest": "field", "message": "hi"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_static_and_health():
    assert client.get("/static/img/logo.png").status_code == 200
    assert client.get("/static/css/style.css").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}

