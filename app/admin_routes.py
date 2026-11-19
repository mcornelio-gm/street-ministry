"""/admin/routes: create, edit, delete outreach routes (data/routes.json)."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app import admin, md_engine, routes_data
from app.routes_data import RoutesError, VersionConflict

router = APIRouter(prefix="/admin")


def _float(v):
    try:
        return round(float(v), 6) if str(v).strip() != "" else None
    except ValueError:
        return None


def _route_from_form(form) -> dict:
    names = form.getlist("stop_name")
    addrs = form.getlist("stop_address")
    lats = form.getlist("stop_lat")
    lngs = form.getlist("stop_lng")
    approx = form.getlist("stop_approx")
    stops = []
    for i in range(len(names)):
        name, addr = names[i].strip(), (addrs[i] if i < len(addrs) else "").strip()
        if not (name or addr):
            continue  # skip blank rows
        stops.append({
            "name": name,
            "address": addr,
            "lat": _float(lats[i]) if i < len(lats) else None,
            "lng": _float(lngs[i]) if i < len(lngs) else None,
            "approx": (approx[i] == "1") if i < len(approx) else False,
        })
    return {
        "id": form.get("id") or routes_data.new_id(),
        "name": form.get("name", ""),
        "leaders": form.get("leaders", ""),
        "schedule": form.get("schedule", ""),
        "summary": form.get("summary", ""),
        "notes": form.get("notes", ""),
        "stops": stops,
    }


def _edit(request: Request, route: dict, version: str, is_new: bool, error: str | None = None,
          status: int = 200):
    return admin._render(request, "admin_route_edit.html", {
        "route": route, "version": version, "is_new": is_new, "error": error,
    }, status)


@router.get("/routes/new", response_class=HTMLResponse)
async def new_route(request: Request):
    if not admin._user(request):
        return admin._login_redirect()
    _, version = routes_data.load()
    route = {"id": "", "name": "", "leaders": "", "schedule": "", "summary": "", "notes": "",
             "stops": [{"name": "", "address": "", "lat": None, "lng": None, "approx": False}]}
    return _edit(request, route, version, is_new=True)


@router.get("/routes/{route_id}", response_class=HTMLResponse)
async def edit_route(request: Request, route_id: str):
    if not admin._user(request):
        return admin._login_redirect()
    routes, version = routes_data.load()
    route = next((r for r in routes if r["id"] == route_id), None)
    if not route:
        return RedirectResponse(url="/admin?msg=Route+not+found", status_code=303)
    return _edit(request, route, version, is_new=False)


@router.post("/routes/save")
async def save_route(request: Request):
    if not admin._user(request):
        return admin._login_redirect()
    form = await request.form()
    route = _route_from_form(form)
    is_new = not form.get("id")
    version = form.get("version") or None
    # Look up coordinates for stops that don't have them yet (new or changed addresses)
    missing = await run_in_threadpool(routes_data.fill_missing_coordinates, route)
    try:
        routes_data.save_route(route, version)
    except VersionConflict as e:
        _, current = routes_data.load()
        return _edit(request, route, current, is_new, str(e), 409)
    except RoutesError as e:
        return _edit(request, route, version or "", is_new, str(e), 400)
    await run_in_threadpool(md_engine.build_mkdocs)  # refresh the wiki's Routes page
    msg = "Saved route"
    if missing:
        msg += f" (couldn't place: {', '.join(missing)}; edit to set the pin)"
    return RedirectResponse(url="/admin?msg=" + msg.replace(" ", "+"), status_code=303)


@router.post("/routes/{route_id}/delete")
async def delete_route(request: Request, route_id: str):
    if not admin._user(request):
        return admin._login_redirect()
    form = await request.form()
    try:
        routes_data.delete_route(route_id, form.get("version") or None)
    except VersionConflict as e:
        return RedirectResponse(url="/admin?msg=" + str(e).replace(" ", "+"), status_code=303)
    except RoutesError:
        return RedirectResponse(url="/admin?msg=Route+not+found", status_code=303)
    await run_in_threadpool(md_engine.build_mkdocs)
    return RedirectResponse(url="/admin?msg=Deleted+route", status_code=303)


@router.get("/geocode")
async def geocode(request: Request, name: str = "", address: str = ""):
    """Used by the route editor's 'Find on map' button."""
    if not admin._user(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    hit = await run_in_threadpool(routes_data.geocode_stop, name.strip(), address.strip())
    if not hit:
        return JSONResponse({"found": False})
    return JSONResponse({"found": True, **hit, "city": routes_data.city_from_address(address)})
