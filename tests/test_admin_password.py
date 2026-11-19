"""Change Password tests. Uses a temporary overrides file (real data untouched)."""
import os

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app

OLD = "correct-horse-battery"
NEW = "brand-new-password-42"


@pytest.fixture
def setup(monkeypatch, tmp_path):
    owner_hash = auth.hash_password(OLD)
    monkeypatch.setenv("ADMIN_USERS", f"tester:{owner_hash},other:{auth.hash_password('other-password-1')}")
    path = tmp_path / "Private" / "admin_passwords.json"
    monkeypatch.setattr(auth, "overrides_path", lambda: str(path))
    auth._failures.clear()
    return path


def signed_in(password=OLD):
    c = TestClient(app)
    r = c.post("/admin/login", data={"username": "tester", "password": password}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


def change(c, current=OLD, new=NEW, confirm=None):
    return c.post("/admin/password", data={"current": current, "new": new, "confirm": confirm or new},
                  follow_redirects=False)


def test_requires_login(setup):
    c = TestClient(app)
    assert c.get("/admin/password", follow_redirects=False).headers["location"] == "/admin/login"
    assert change(c).headers["location"] == "/admin/login"


def test_page_and_dashboard_link(setup):
    c = signed_in()
    assert 'href="/admin/password"' in c.get("/admin").text
    page = c.get("/admin/password")
    assert page.status_code == 200 and 'name="current"' in page.text and 'autocomplete="new-password"' in page.text


def test_change_password_flow(setup):
    c = signed_in()
    other_device = signed_in()
    r = change(c)
    assert r.status_code == 303 and "Password+changed" in r.headers["location"]
    assert c.get("/admin").status_code == 200  # this device stays signed in
    assert other_device.get("/admin", follow_redirects=False).status_code == 303  # others signed out
    assert TestClient(app).post("/admin/login", data={"username": "tester", "password": OLD}).status_code == 401
    signed_in(NEW)
    # Only a hash is stored, outside the repo's data/ folder
    text = setup.read_text()
    assert NEW not in text and "pbkdf2_sha256$" in text
    assert "data" not in os.path.relpath(str(setup)).split(os.sep)[:1]


@pytest.mark.parametrize("current,new,confirm,msg", [
    ("wrong-password-x", NEW, NEW, "current password is incorrect"),
    (OLD, "short", "short", "at least 10"),
    (OLD, NEW, NEW + "x", "don&#39;t match"),
    (OLD, OLD, OLD, "different from your current"),
])
def test_rejections(setup, current, new, confirm, msg):
    c = signed_in()
    r = change(c, current, new, confirm)
    assert r.status_code == 400 and msg in r.text
    signed_in(OLD)  # unchanged


def test_owner_reset_wins_and_removal_revokes(setup, monkeypatch):
    c = signed_in()
    change(c)
    # Owner resets tester's password with manage_admins.py (new hash in ADMIN_USERS)
    monkeypatch.setenv("ADMIN_USERS", f"tester:{auth.hash_password('owner-reset-pass')}")
    assert c.get("/admin", follow_redirects=False).status_code == 303  # old session ends
    assert TestClient(app).post("/admin/login", data={"username": "tester", "password": NEW}).status_code == 401
    signed_in("owner-reset-pass")
    # Owner removes tester entirely: no password works
    monkeypatch.setenv("ADMIN_USERS", f"other:{auth.hash_password('other-password-1')}")
    assert TestClient(app).post("/admin/login", data={"username": "tester", "password": "owner-reset-pass"}).status_code == 401


def test_wrong_current_password_is_rate_limited(setup):
    c = signed_in()
    for _ in range(5):
        change(c, current="nope-nope-nope")
    assert change(c).status_code == 429
