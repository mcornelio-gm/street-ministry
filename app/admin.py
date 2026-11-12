"""/admin: login plus create/edit/delete for Announcements and Stories."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app import auth, content_store, md_engine
from app.content_store import ContentError, KINDS

router = APIRouter(prefix="/admin")
templates = None  # set by main.py


def _user(request: Request) -> str | None:
    user = request.session.get("admin_user")
    # Re-check on every request so removing a user takes effect immediately
    return user if user and user in auth.load_users() else None


def _login_redirect():
    return RedirectResponse(url="/admin/login", status_code=303)


def _render(request: Request, name: str, ctx: dict, status: int = 200):
    ctx = {"active_tab": "admin", "admin_user": _user(request), **ctx}
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def _form_ctx(kind: str, item: dict, error: str | None = None, is_new: bool = False):
    return {
        "kind": kind,
        "kind_label": KINDS[kind]["label"],
        "item": item,
        "error": error,
        "is_new": is_new,
        "cities": content_store.CITIES,
        "categories": content_store.ANNOUNCEMENT_CATEGORIES,
    }


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _user(request):
        return RedirectResponse(url="/admin", status_code=303)
    return _render(request, "admin_login.html", {"error": None})


@router.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form("")):
    ip = request.client.host if request.client else "unknown"
    if auth.is_rate_limited(ip):
        return _render(request, "admin_login.html",
                       {"error": "Too many attempts. Please wait 15 minutes."}, 429)
    user = auth.authenticate(username, password)
    if not user:
        auth.record_failure(ip)
        return _render(request, "admin_login.html", {"error": "Incorrect username or password."}, 401)
    request.session.clear()
    request.session["admin_user"] = user
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return _login_redirect()


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _user(request):
        return _login_redirect()
    sections = [
        {"kind": "announcements", "url": "/"},
        {"kind": "stories", "url": "/stories"},
        {"kind": "guides", "url": "/guides"},
    ]
    for s in sections:
        s.update(label=KINDS[s["kind"]]["label"], plural=KINDS[s["kind"]]["plural"],
                 items=content_store.list_items(s["kind"]))
    return _render(request, "admin_index.html", {
        "sections": sections,
        "flash": request.query_params.get("msg"),
    })


@router.get("/{kind}/new", response_class=HTMLResponse)
async def new_item(request: Request, kind: str):
    if not _user(request):
        return _login_redirect()
    if kind not in KINDS:
        return RedirectResponse(url="/admin", status_code=303)
    from datetime import date
    item = {"slug": None, "meta": {"date": date.today().isoformat(), "priority": "standard",
                                    "category": "General"}, "body": ""}
    return _render(request, "admin_edit.html", _form_ctx(kind, item, is_new=True))


@router.get("/{kind}/{slug}", response_class=HTMLResponse)
async def edit_item(request: Request, kind: str, slug: str):
    if not _user(request):
        return _login_redirect()
    try:
        item = content_store.load_item(kind, slug)
    except ContentError:
        return RedirectResponse(url="/admin?msg=Not+found", status_code=303)
    return _render(request, "admin_edit.html", _form_ctx(kind, item))


@router.post("/{kind}/save")
async def save(request: Request, kind: str):
    if not _user(request):
        return _login_redirect()
    form = dict(await request.form())
    slug = form.get("slug") or None
    try:
        slug = content_store.save_item(kind, form, slug)
    except ContentError as e:
        item = {"slug": slug, "meta": form, "body": form.get("body", "")}
        return _render(request, "admin_edit.html", _form_ctx(kind, item, str(e), is_new=slug is None), 400)
    await run_in_threadpool(md_engine.build_mkdocs)  # don't block the event loop
    return RedirectResponse(url=f"/admin?msg=Saved+{KINDS[kind]['label']}", status_code=303)


@router.post("/{kind}/{slug}/delete")
def delete(request: Request, kind: str, slug: str):
    if not _user(request):
        return _login_redirect()
    try:
        content_store.delete_item(kind, slug)
    except ContentError:
        return RedirectResponse(url="/admin?msg=Not+found", status_code=303)
    md_engine.build_mkdocs()
    return RedirectResponse(url=f"/admin?msg=Deleted+{KINDS[kind]['label']}", status_code=303)


@router.post("/preview")
async def preview(request: Request, body: str = Form("")):
    if not _user(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({"html": md_engine.render_markdown(body)})
