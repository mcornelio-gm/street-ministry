"""Admin tests. Creates a temporary story/announcement and always removes it."""
import os

import pytest
from fastapi.testclient import TestClient

from app import auth, content_store
from app.main import app

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USERS", f"tester:{auth.hash_password(PASSWORD)}")
    auth._failures.clear()
    return TestClient(app)


def login(c):
    return c.post("/admin/login", data={"username": "tester", "password": PASSWORD},
                  follow_redirects=False)


def test_password_hashing():
    h = auth.hash_password("s3cret-pass")
    assert auth.verify_password("s3cret-pass", h)
    assert not auth.verify_password("wrong", h)
    assert "s3cret" not in h


def test_admin_requires_login(client):
    for path in ("/admin", "/admin/stories/new"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
    r = client.post("/admin/stories/save", data={"title": "x"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/login"
    assert client.post("/admin/preview", data={"body": "x"}).status_code == 401


def test_bad_login_and_rate_limit(client):
    assert client.post("/admin/login", data={"username": "tester", "password": "nope"}).status_code == 401
    for _ in range(5):
        client.post("/admin/login", data={"username": "tester", "password": "nope"})
    assert client.post("/admin/login", data={"username": "tester", "password": PASSWORD}).status_code == 429


def test_dashboard_lists_content_but_not_index(client):
    assert login(client).status_code == 303
    r = client.get("/admin")
    assert r.status_code == 200
    for kind in ("announcements", "stories", "guides"):
        for it in content_store.list_items(kind):
            assert f"/admin/{kind}/{it['slug']}" in r.text
        assert f"/admin/{kind}/index" not in r.text


def test_path_traversal_rejected(client):
    login(client)
    for bad in ("index", "..%2F..%2Fapp%2Fmain", "UPPER"):
        r = client.get(f"/admin/stories/{bad}", follow_redirects=False)
        # Either the router doesn't match (404) or the editor rejects it (303)
        assert r.status_code in (303, 404)
        if r.status_code == 303:
            assert "Not+found" in r.headers["location"]
    with pytest.raises(content_store.ContentError):
        content_store._path("stories", "../../etc/passwd")


def test_story_create_edit_delete(client):
    login(client)
    form = {"title": "Zz Test Story", "date": "2026-11-11", "city": "Fremont",
            "author": "Tester", "tags": "Test, Admin", "summary": "A test.",
            "body": "Hello **world**."}
    slug = None
    try:
        # Validation: city is required
        bad = client.post("/admin/stories/save", data={**form, "city": ""})
        assert bad.status_code == 400 and "choose a city" in bad.text

        r = client.post("/admin/stories/save", data=form, follow_redirects=False)
        assert r.status_code == 303
        slug = "2026-11-11-zz-test-story"
        assert os.path.exists(content_store._path("stories", slug))

        # Visible on the public site and the rebuilt wiki
        assert "Zz Test Story" in client.get("/stories").text
        assert client.get(f"/stories/{slug}").status_code == 200
        assert client.get(f"/documents/Stories/{slug}/").status_code == 200
        assert "Zz Test Story" in client.get("/documents/Stories/").text

        # Edit keeps the same file
        client.post("/admin/stories/save", data={**form, "slug": slug, "title": "Zz Edited"})
        item = content_store.load_item("stories", slug)
        assert item["meta"]["title"] == "Zz Edited"
        assert item["meta"]["tags"] == "Test, Admin"

        r = client.post(f"/admin/stories/{slug}/delete", follow_redirects=False)
        assert r.status_code == 303
        assert not os.path.exists(content_store._path("stories", slug))
        assert client.get(f"/stories/{slug}").status_code == 404
        slug = None
    finally:
        if slug:
            content_store.delete_item("stories", slug)


def test_guide_create_edit_delete(client):
    login(client)
    form = {"title": "Zz Test Guide", "date": "2026-11-12", "author": "Tester",
            "summary": "How to test.", "body": "Step **one**.\n\n```mermaid\nflowchart TD\n  A --> B\n```"}
    slug = "zz-test-guide"  # guides have no date prefix
    try:
        r = client.post("/admin/guides/save", data=form, follow_redirects=False)
        assert r.status_code == 303
        assert "Zz Test Guide" in client.get("/admin").text
        page = client.get(f"/guides/{slug}")
        assert page.status_code == 200 and '<pre class="mermaid">' in page.text
        assert client.get(f"/documents/Guides/{slug}/").status_code == 200
        client.post("/admin/guides/save", data={**form, "slug": slug, "summary": "Edited."})
        assert content_store.load_item("guides", slug)["meta"]["summary"] == "Edited."
        client.post(f"/admin/guides/{slug}/delete")
        assert client.get(f"/guides/{slug}").status_code == 404
    finally:
        if os.path.exists(content_store._path("guides", slug)):
            content_store.delete_item("guides", slug)


def test_announcement_create_and_delete(client):
    login(client)
    form = {"title": "Zz Test Announcement", "date": "2030-01-01", "author": "Tester",
            "category": "Training", "priority": "urgent", "body": "# Zz Test Announcement\n\nBring gloves."}
    slug = "2030-01-01-zz-test-announcement"
    try:
        client.post("/admin/announcements/save", data=form)
        item = content_store.load_item("announcements", slug)
        assert item["meta"]["priority"] == "urgent"
        assert item["body"] == "Bring gloves."  # leading '# Title' stripped
        home = client.get("/").text
        assert "Zz Test Announcement" in home
        # Dated 2030, so it must be listed before every real announcement
        others = [a for a in content_store.list_items("announcements") if a["slug"] != slug]
        for a in others:
            assert home.index("Zz Test Announcement") < home.index(a["title"].replace("&", "&amp;"))
    finally:
        if os.path.exists(content_store._path("announcements", slug)):
            client.post(f"/admin/announcements/{slug}/delete")
    assert "Zz Test Announcement" not in client.get("/").text
