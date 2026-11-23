"""/admin/events: create, edit, delete calendar events (data/events.json)."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import admin, calendar_data
from app.calendar_data import CalendarError

router = APIRouter(prefix="/admin")


def _event_from_form(form) -> dict:
    weekdays = [int(w) for w in form.getlist("weekdays") if str(w).isdigit()]
    mweeks = []
    for w in form.getlist("monthly_weeks"):
        try:
            mweeks.append(int(w))
        except ValueError:
            pass
    skips = [s.strip() for s in str(form.get("skip_dates", "")).replace("\n", ",").split(",") if s.strip()]
    return {
        "id": str(form.get("id") or calendar_data.new_id()),
        "title": str(form.get("title", "")),
        "recurrence": str(form.get("recurrence", "once")),
        "start_date": str(form.get("start_date", "")),
        "end_date": str(form.get("end_date", "")),
        "weekdays": weekdays,
        "monthly_weeks": mweeks,
        "time": str(form.get("time", "")),
        "city": str(form.get("city", "")),
        "location": str(form.get("location", "")),
        "notes": str(form.get("notes", "")),
        "skip_dates": skips,
    }


def _edit(request: Request, event: dict, is_new: bool, error: str | None = None, status: int = 200):
    return admin._render(request, "admin_event_edit.html", {
        "event": event,
        "is_new": is_new,
        "error": error,
        "cities": calendar_data.CITIES,
        "weekday_names": calendar_data.WEEKDAY_NAMES,
        "monthly_week_labels": calendar_data.MONTHLY_WEEK_LABELS,
    }, status)


@router.get("/events/new", response_class=HTMLResponse)
async def new_event(request: Request):
    if not admin._user(request):
        return admin._login_redirect()
    from datetime import date
    ev = {
        "id": "",
        "title": "",
        "recurrence": "weekly",
        "start_date": date.today().isoformat(),
        "end_date": "",
        "weekdays": [],
        "monthly_weeks": [],
        "time": "",
        "city": "",
        "location": "",
        "notes": "",
        "skip_dates": [],
    }
    return _edit(request, ev, is_new=True)


@router.get("/events/{event_id}", response_class=HTMLResponse)
async def edit_event(request: Request, event_id: str):
    if not admin._user(request):
        return admin._login_redirect()
    ev = calendar_data.get_event(event_id)
    if not ev:
        return RedirectResponse(url="/admin?err=Event+not+found", status_code=303)
    return _edit(request, ev, is_new=False)


@router.post("/events/save")
async def save_event(request: Request):
    if not admin._user(request):
        return admin._login_redirect()
    form = await request.form()
    is_new = not bool(form.get("id"))
    ev = _event_from_form(form)
    try:
        calendar_data.save_event(ev)
    except CalendarError as e:
        return _edit(request, ev, is_new=is_new, error=str(e), status=400)
    return RedirectResponse(url="/admin?msg=Event+saved", status_code=303)


@router.post("/events/{event_id}/delete")
def delete_event(request: Request, event_id: str):
    if not admin._user(request):
        return admin._login_redirect()
    calendar_data.delete_event(event_id)
    return RedirectResponse(url="/admin?msg=Event+deleted", status_code=303)
