"""Resonate Church Street Ministry - FastAPI application.

Custom Jinja2 pages: Welcome, Stories, Routes, About.
Documents tab: MkDocs (Material) static site built from ./Documents and served at /documents.
"""
import os
import secrets
import threading

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))  # local only; Cloud Run uses Secret Manager

from app import admin, admin_routes, content_store, md_engine, routes_data, settings_store  # noqa: E402

SITE_DOCS_DIR = os.path.join(PROJECT_ROOT, "site", "documents")
ON_CLOUD_RUN = bool(os.environ.get("K_SERVICE"))

app = FastAPI(title="Resonate Church Street Ministry")

session_secret = os.environ.get("SESSION_SECRET")
if not session_secret:
    print("WARNING: SESSION_SECRET not set; admin logins will reset on restart.")
    session_secret = secrets.token_urlsafe(48)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="sm_admin",
    max_age=8 * 60 * 60,
    same_site="strict",
    https_only=ON_CLOUD_RUN,
)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def _file_version(path: str) -> str:
    """Short content hash, appended as ?v=... so browsers fetch changed CSS instead of
    reusing a stale cached copy."""
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:10]
    except OSError:
        return "0"


templates.env.globals["css_v"] = _file_version(os.path.join(BASE_DIR, "static", "css", "style.css"))
templates.env.globals["site_share_url"] = settings_store.get_share_url
admin.templates = templates
# Route-editor endpoints first, so /admin/routes/... isn't caught by the generic /admin/{kind}/...
app.include_router(admin_routes.router)
app.include_router(admin.router)

# Point editable folders at the Cloud Storage bucket (Cloud Run only), then build the
# MkDocs wiki in a background thread so cold starts don't block the first page load.
content_store.setup_content_mount()
os.makedirs(SITE_DOCS_DIR, exist_ok=True)
if (
    not os.path.exists(os.path.join(SITE_DOCS_DIR, "index.html"))
    or os.environ.get("CONTENT_MOUNT")
    or os.environ.get("REBUILD_DOCS") == "1"
):
    threading.Thread(target=md_engine.build_mkdocs, daemon=True).start()

app.mount("/documents", StaticFiles(directory=SITE_DOCS_DIR, html=True), name="documents")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "active_tab": "welcome",
            "announcements": md_engine.get_announcements(),
        },
    )


@app.get("/announcements/{slug}", response_class=HTMLResponse)
async def announcement_detail(request: Request, slug: str):
    announcement = md_engine.get_announcement_by_slug(slug)
    if not announcement:
        new_slug = content_store.all_aliases("announcements").get(slug)
        if new_slug:
            return RedirectResponse(url=f"/announcements/{new_slug}", status_code=301)
        raise HTTPException(status_code=404, detail="Announcement not found")
    return templates.TemplateResponse(
        request,
        "announcement_detail.html",
        {"active_tab": "welcome", "announcement": announcement},
    )


@app.get("/documents")
async def documents_redirect():
    return RedirectResponse(url="/documents/")


@app.get("/stories", response_class=HTMLResponse)
async def stories(request: Request):
    return templates.TemplateResponse(
        request,
        "stories.html",
        {"active_tab": "stories", "stories": md_engine.get_stories()},
    )


@app.get("/stories/{slug}", response_class=HTMLResponse)
async def story_detail(request: Request, slug: str):
    story = md_engine.get_story_by_slug(slug)
    if not story:
        new_slug = content_store.all_aliases("stories").get(slug)
        if new_slug:  # renamed in /admin: send old links to the new address
            return RedirectResponse(url=f"/stories/{new_slug}", status_code=301)
        raise HTTPException(status_code=404, detail="Story not found")
    return templates.TemplateResponse(
        request,
        "story_detail.html",
        {"active_tab": "stories", "story": story},
    )


SECTION_CONFIG = {
    "guides": {
        "folder": "Guides",
        "section_title": "Guides",
        "page_title": "Guides",
        "header_pill": "Documents/Guides",
        "header_icon": "compass",
        "item_label": "Guide",
        "item_icon": "book-open-check",
        "page_subtitle": (
            "Orientation, safety, and how-to guides for our outreach teams. Please read the "
            "Volunteer Orientation & Safety Briefing before your first ride-along."
        ),
    },
    "teams": {
        "folder": "Teams",
        "section_title": "Teams",
        "page_title": "Volunteer With Our Teams",
        "header_pill": "Get Involved",
        "header_icon": "handshake",
        "item_label": "Team",
        "item_icon": "users",
        "page_subtitle": (
            "There are many ways to serve depending on your comfort level, schedule, and gifts. "
            "Explore our outreach and support teams below."
        ),
    },
    "about": {
        "folder": "About",
        "section_title": "About",
        "page_title": "Loving Our Neighbors Across the East Bay",
        "header_pill": "About Our Outreach",
        "header_icon": "info",
        "item_label": "About",
        "item_icon": "info",
        "page_subtitle": (
            "Resonate Church Street Ministry is an intentional, relational outreach serving our "
            "unhoused neighbors across Fremont, Newark, Union City, and Hayward."
        ),
    },
}


def _render_section_list(request: Request, kind: str):
    cfg = SECTION_CONFIG[kind]
    return templates.TemplateResponse(
        request,
        "section_list.html",
        {
            "active_tab": kind,
            "base_url": f"/{kind}",
            "docs": md_engine.get_section_docs(cfg["folder"]),
            **cfg,
        },
    )


def _render_section_detail(request: Request, kind: str, slug: str):
    cfg = SECTION_CONFIG[kind]
    doc = md_engine.get_section_doc_by_slug(cfg["folder"], slug)
    if not doc:
        new_slug = content_store.all_aliases(kind).get(slug)
        if new_slug:
            return RedirectResponse(url=f"/{kind}/{new_slug}", status_code=301)
        raise HTTPException(status_code=404, detail=f"{cfg['item_label']} not found")
    return templates.TemplateResponse(
        request,
        "section_detail.html",
        {
            "active_tab": kind,
            "base_url": f"/{kind}",
            "doc": doc,
            **cfg,
        },
    )


@app.get("/guides", response_class=HTMLResponse)
async def guides(request: Request):
    return _render_section_list(request, "guides")


@app.get("/guides/{slug}", response_class=HTMLResponse)
async def guide_detail(request: Request, slug: str):
    return _render_section_detail(request, "guides", slug)


@app.get("/teams", response_class=HTMLResponse)
async def teams(request: Request):
    return _render_section_list(request, "teams")


@app.get("/teams/{slug}", response_class=HTMLResponse)
async def team_detail(request: Request, slug: str):
    return _render_section_detail(request, "teams", slug)


@app.get("/routes", response_class=HTMLResponse)
async def routes(request: Request):
    return templates.TemplateResponse(
        request,
        "routes.html",
        {"active_tab": "routes", "routes": routes_data.get_all_routes(), "cities": routes_data.get_cities()},
    )


@app.get("/api/routes")
async def api_routes():
    return routes_data.get_all_routes()


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return _render_section_list(request, "about")


@app.get("/about/{slug}", response_class=HTMLResponse)
async def about_detail(request: Request, slug: str):
    return _render_section_detail(request, "about", slug)


# Note: Cloud Run reserves paths ending in "z" (e.g. /healthz), so use /health.
@app.get("/health")
async def health():
    return {"status": "ok"}

