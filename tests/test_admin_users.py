"""Tests for managing admin accounts (/admin/users)."""
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_USERS", f"owner:{auth.hash_password(PASSWORD)}")
    monkeypatch.setattr(auth, "users_path", lambda: str(tmp_path / "admin_users.json"))
    monkeypatch.setattr(auth, "overrides_path", lambda: str(tmp_path / "admin_passwords.json"))
    auth._failures.clear()
    return TestClient(app)


def login(c, username="owner", password=PASSWORD):
    return c.post(
        "/admin/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_users_requires_login(client):
    assert client.get("/admin/users", follow_redirects=False).headers["location"] == "/admin/login"
    assert client.post("/admin/users/add", data={"username": "bob"}, follow_redirects=False).headers["location"] == "/admin/login"


def test_add_login_change_password_and_delete_self(client):
    assert login(client).status_code == 303

    # Cannot delete the last remaining admin
    last = client.post("/admin/users/owner/delete", follow_redirects=False)
    assert last.status_code == 400
    assert "last remaining admin" in last.text

    # Add a new admin
    r = client.post(
        "/admin/users/add",
        data={"username": "helper", "password": "helper-pass-123", "confirm": "helper-pass-123"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # New admin can sign in and change their own password
    helper_client = TestClient(app)
    assert login(helper_client, "helper", "helper-pass-123").status_code == 303
    pw = helper_client.post(
        "/admin/password",
        data={
            "current": "helper-pass-123",
            "new": "helper-new-pass-456",
            "confirm": "helper-new-pass-456",
        },
        follow_redirects=False,
    )
    assert pw.status_code == 303
    assert auth.authenticate("helper", "helper-new-pass-456") == "helper"

    # Owner can delete themselves; they get signed out and cannot access /admin
    d = client.post("/admin/users/owner/delete", follow_redirects=False)
    assert d.status_code == 303
    assert d.headers["location"] == "/admin/login"
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    assert auth.authenticate("owner", PASSWORD) is None
