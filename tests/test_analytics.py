"""Tests for privacy-friendly page traffic analytics (/admin/analytics)."""
import pytest
from fastapi.testclient import TestClient

from app import analytics_store, auth
from app.main import app

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_USERS", f"tester:{auth.hash_password(PASSWORD)}")
    monkeypatch.setattr(analytics_store, "analytics_path", lambda: str(tmp_path / "analytics.json"))
    auth._failures.clear()
    return TestClient(app)


def login(c):
    return c.post(
        "/admin/login",
        data={"username": "tester", "password": PASSWORD},
        follow_redirects=False,
    )


def test_analytics_requires_login(client):
    assert client.get("/admin/analytics", follow_redirects=False).headers["location"] == "/admin/login"
    assert client.post("/admin/analytics/reset", follow_redirects=False).headers["location"] == "/admin/login"


def test_tracks_public_views_unique_ips_and_skips_bots(client):
    # Two visits from IP1, one from IP2, plus a bot visit (ignored)
    client.get("/", headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1", "user-agent": "Mozilla/5.0"})
    client.get("/", headers={"x-forwarded-for": "1.2.3.4", "user-agent": "Mozilla/5.0"})
    client.get("/stories", headers={"x-forwarded-for": "5.6.7.8", "user-agent": "Mozilla/5.0"})
    client.get("/stories", headers={"x-forwarded-for": "9.9.9.9", "user-agent": "Googlebot/2.1"})
    client.get("/health", headers={"x-forwarded-for": "5.6.7.8", "user-agent": "Mozilla/5.0"})

    stats = analytics_store.summary()
    assert stats["views_all"] == 3
    assert stats["unique_all"] == 2
    by_path = {p["path"]: p for p in stats["pages"]}
    assert by_path["/"]["views_all"] == 2
    assert by_path["/"]["unique_all"] == 1
    assert by_path["/stories"]["views_all"] == 1
    assert by_path["/stories"]["unique_all"] == 1
    assert "/health" not in by_path

    # Raw IPs must never be stored on disk
    with open(analytics_store.analytics_path(), encoding="utf-8") as f:
        raw = f.read()
    assert "1.2.3.4" not in raw
    assert "5.6.7.8" not in raw

    # Admin can view and reset
    assert login(client).status_code == 303
    dash = client.get("/admin/analytics")
    assert dash.status_code == 200
    assert "/stories" in dash.text

    r = client.post("/admin/analytics/reset", follow_redirects=False)
    assert r.status_code == 303
    assert analytics_store.summary()["views_all"] == 0


def test_analytics_merges_renamed_article_aliases(client, monkeypatch):
    from app import content_store
    monkeypatch.setattr(
        content_store,
        "all_aliases",
        lambda kind: {"old-story": "new-story"} if kind == "stories" else {},
    )
    analytics_store.record_visit("/stories/old-story", "1.2.3.4", "Mozilla/5.0")
    analytics_store.record_visit("/stories/new-story", "1.2.3.4", "Mozilla/5.0")
    analytics_store.record_visit("/stories/new-story", "5.6.7.8", "Mozilla/5.0")

    stats = analytics_store.summary()
    by_path = {p["path"]: p for p in stats["pages"]}
    assert "/stories/old-story" not in by_path
    assert by_path["/stories/new-story"]["views_all"] == 3
    assert by_path["/stories/new-story"]["unique_all"] == 2
