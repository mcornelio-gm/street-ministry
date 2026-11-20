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
    # Re-checked on every request: removing a user, an owner reset, or a password change
    # (from another device) ends this session immediately.
    if user and request.session.get("pw") == auth.password_fingerprint(user):
        return user
    return None


def _start_session(request: Request, user: str):
    request.session.clear()
    request.session["admin_user"] = user
    request.session["pw"] = auth.password_fingerprint(user)


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
    _start_session(request, user)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return _login_redirect()


@router.get("/password", response_class=HTMLResponse)
async def password_page(request: Request):
    if not _user(request):
        return _login_redirect()
    return _render(request, "admin_password.html", {"error": None, "min_length": auth.MIN_PASSWORD_LENGTH})


@router.post("/password")
def change_password(request: Request, current: str = Form(""), new: str = Form(""), confirm: str = Form("")):
    user = _user(request)
    if not user:
        return _login_redirect()
    ip = request.client.host if request.client else "unknown"
    if auth.is_rate_limited(ip):
        return _render(request, "admin_password.html",
                       {"error": "Too many attempts. Please wait 15 minutes.",
                        "min_length": auth.MIN_PASSWORD_LENGTH}, 429)
    try:
        auth.change_password(user, current, new, confirm)
    except ValueError as e:
        if "current password" in str(e):
            auth.record_failure(ip)
        return _render(request, "admin_password.html",
                       {"error": str(e), "min_length": auth.MIN_PASSWORD_LENGTH}, 400)
    _start_session(request, user)  # keep this device signed in; others are signed out
    return RedirectResponse(url="/admin?msg=Password+changed", status_code=303)


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _user(request):
        return _login_redirect()
    sections = [
        {"kind": "announcements", "url": "/"},
        {"kind": "stories", "url": "/stories"},
        {"kind": "guides", "url": "/guides"},
        {"kind": "teams", "url": "/teams"},
        {"kind": "about", "url": "/about"},
    ]
    for s in sections:
        s.update(label=KINDS[s["kind"]]["label"], plural=KINDS[s["kind"]]["plural"],
                 items=content_store.list_items(s["kind"]))
    from app import messages_store, routes_data, settings_store
    routes, routes_version = routes_data.load()
    messages = messages_store.list_messages()
    return _render(request, "admin_index.html", {
        "sections": sections,
        "routes": routes,
        "routes_version": routes_version,
        "messages": messages,
        "unread_count": sum(1 for m in messages if not m.get("read")),
        "share_url": settings_store.get_share_url(),
        "flash": request.query_params.get("msg"),
        "error": request.query_params.get("err"),
    })


@router.post("/messages/{msg_id}/toggle")
def toggle_message_read(request: Request, msg_id: str):
    if not _user(request):
        return _login_redirect()
    from app import messages_store
    messages_store.toggle_read(msg_id)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/messages/{msg_id}/delete")
def delete_inbox_message(request: Request, msg_id: str):
    if not _user(request):
        return _login_redirect()
    from app import messages_store
    messages_store.delete_message(msg_id)
    return RedirectResponse(url="/admin?msg=Deleted+message", status_code=303)


@router.post("/settings/share")
def save_share_settings(request: Request, share_url: str = Form("")):
    if not _user(request):
        return _login_redirect()
    from app import settings_store
    try:
        saved = settings_store.save_share_url(share_url)
    except ValueError as e:
        from urllib.parse import quote_plus
        return RedirectResponse(url=f"/admin?err={quote_plus(str(e))}", status_code=303)
    msg = "Share+QR+URL+saved" if saved else "Share+QR+reset+to+current+site+URL"
    return RedirectResponse(url=f"/admin?msg={msg}", status_code=303)


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
        new_slug = content_store.all_aliases(kind).get(slug) if kind in KINDS else None
        if new_slug:
            return RedirectResponse(url=f"/admin/{kind}/{new_slug}", status_code=303)
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
