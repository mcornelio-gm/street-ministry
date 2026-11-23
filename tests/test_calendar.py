"""Tests for the perpetual outreach calendar (/calendar, /calendar.ics, /admin/events)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import auth, calendar_data
from app.main import app

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_USERS", f"tester:{auth.hash_password(PASSWORD)}")
    monkeypatch.setattr(calendar_data, "DATA_FILE", str(tmp_path / "events.json"))
    auth._failures.clear()
    return TestClient(app)


def login(c):
    return c.post(
        "/admin/login",
        data={"username": "tester", "password": PASSWORD},
        follow_redirects=False,
    )


def test_monthly_and_skip_recurrence():
    ev = {
        "id": "ev-1",
        "title": "Fremont Outreach",
        "recurrence": "monthly",
        "start_date": "2026-11-01",
        "weekdays": [5],  # Saturday
        "monthly_weeks": [1, 3],  # 1st & 3rd Saturday
        "skip_dates": ["2026-11-21"],  # skip 3rd Sat in Nov 2026
    }
    occs = calendar_data.expand_range([ev], date(2026, 11, 1), date(2026, 11, 30))
    assert [o["date_str"] for o in occs] == ["2026-11-07"]

    # Dec 2026 has 1st Sat = Dec 5, 3rd Sat = Dec 19
    dec = calendar_data.expand_range([ev], date(2026, 12, 1), date(2026, 12, 31))
    assert [o["date_str"] for o in dec] == ["2026-12-05", "2026-12-19"]


def test_calendar_admin_and_public_views(client):
    assert client.get("/admin/events/new", follow_redirects=False).headers["location"] == "/admin/login"

    assert login(client).status_code == 303
    r = client.post(
        "/admin/events/save",
        data={
            "title": "Weekly Meal Prep",
            "recurrence": "weekly",
            "start_date": "2026-11-01",
            "weekdays": ["2"],  # Wednesday
            "time": "6:00 PM",
            "city": "Newark",
            "location": "Kitchen",
            "notes": "Bring aprons",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    page = client.get("/calendar?month=2026-11")
    assert page.status_code == 200
    assert "Weekly Meal Prep" in page.text
    assert "November 2026" in page.text

    ics = client.get("/calendar.ics")
    assert ics.status_code == 200
    assert "BEGIN:VCALENDAR" in ics.text
    assert "Weekly Meal Prep" in ics.text

    ev_id = calendar_data.list_events()[0]["id"]
    d = client.post(f"/admin/events/{ev_id}/delete", follow_redirects=False)
    assert d.status_code == 303
    assert calendar_data.list_events() == []
