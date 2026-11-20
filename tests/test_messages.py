"""Tests for the popup comments/questions form, unread badge, and admin Inbox."""
import pytest
from fastapi.testclient import TestClient

from app import auth, messages_store
from app.main import app

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_USERS", f"tester:{auth.hash_password(PASSWORD)}")
    monkeypatch.setattr(messages_store, "messages_path", lambda: str(tmp_path / "messages.json"))
    auth._failures.clear()
    messages_store._rate.clear()
    return TestClient(app)


def login(c):
    return c.post(
        "/admin/login",
        data={"username": "tester", "password": PASSWORD},
        follow_redirects=False,
    )


def test_post_message_validation(client):
    r = client.post("/messages", data={"types": ["Comment"], "body": "   "})
    assert r.status_code == 400
    assert r.json()["ok"] is False

    r = client.post("/messages", data={"types": ["Question"], "body": "When do you go out?"})
    assert r.status_code == 400
    assert r.json()["ok"] is False

    r = client.post("/messages", data={"types": ["Volunteer"], "body": "I want to help"})
    assert r.status_code == 400
    assert r.json()["ok"] is False

    r = client.post("/messages", data={"types": ["Story"], "body": "Met someone who was encouraged today"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = client.post("/messages", data={"types": ["Prayer"], "body": "Praying for the team tonight"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = client.post(
        "/messages",
        data={"types": ["Comment"], "name": "Alex", "body": "Thank you for serving!"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(messages_store.list_messages()) == 3
    assert messages_store.unread_count() == 3


def test_honeypot_and_rate_limit(client):
    r = client.post(
        "/messages",
        data={"types": ["Comment"], "body": "Spam link", "website": "https://spam.example"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert messages_store.list_messages() == []

    for i in range(5):
        ok = client.post(
            "/messages",
            data={"types": ["Comment"], "body": f"Note {i}"},
        )
        assert ok.status_code == 200

    blocked = client.post(
        "/messages",
        data={"types": ["Comment"], "body": "One too many"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["ok"] is False


def test_footer_badge_and_admin_inbox_lifecycle(client):
    assert 'admin-unread-badge' not in client.get("/").text

    r = client.post(
        "/messages",
        data={
            "types": ["Question"],
            "name": "Sam",
            "contact": "sam@example.com",
            "body": "Where do we meet?",
        },
    )
    assert r.status_code == 200

    home_html = client.get("/").text
    assert 'class="admin-unread-badge"' in home_html
    assert ">1<" in home_html

    msg_id = messages_store.list_messages()[0]["id"]

    # Auth required for inbox actions
    assert client.post(f"/admin/messages/{msg_id}/toggle", follow_redirects=False).headers["location"] == "/admin/login"
    assert client.post(f"/admin/messages/{msg_id}/delete", follow_redirects=False).headers["location"] == "/admin/login"

    assert login(client).status_code == 303
    dash = client.get("/admin")
    assert dash.status_code == 200
    assert "Where do we meet?" in dash.text
    assert "mailto:sam@example.com" in dash.text
    assert "copy-msg-btn" in dash.text
    assert "data-copy-text=" in dash.text

    # Toggle to read -> badge disappears
    t1 = client.post(f"/admin/messages/{msg_id}/toggle", follow_redirects=False)
    assert t1.status_code == 303
    assert messages_store.unread_count() == 0
    assert 'admin-unread-badge' not in client.get("/").text

    # Toggle back to unread -> badge returns
    t2 = client.post(f"/admin/messages/{msg_id}/toggle", follow_redirects=False)
    assert t2.status_code == 303
    assert messages_store.unread_count() == 1

    # Delete removes message
    d = client.post(f"/admin/messages/{msg_id}/delete", follow_redirects=False)
    assert d.status_code == 303
    assert messages_store.list_messages() == []
    assert messages_store.unread_count() == 0
