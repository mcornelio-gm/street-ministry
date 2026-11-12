"""Resonate Church Street Ministry - FastAPI application.

Custom Jinja2 pages: Welcome, Stories, Routes, About.
Documents tab: MkDocs (Material) static site built from ./Documents and served at /documents.
"""
import os
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))  # local only; Cloud Run uses Secret Manager

from app import admin, content_store, md_engine, routes_data  # noqa: E402

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
admin.templates = templates
app.include_router(admin.router)

# Point Announcements/Stories at the Cloud Storage bucket (Cloud Run only),
# then build the wiki so it reflects the bucket's current content.
content_store.setup_content_mount()
if (
    not os.path.isdir(SITE_DOCS_DIR)
    or os.environ.get("CONTENT_MOUNT")
    or os.environ.get("REBUILD_DOCS") == "1"
):
    md_engine.build_mkdocs()

if os.path.isdir(SITE_DOCS_DIR):
    app.mount("/documents", StaticFiles(directory=SITE_DOCS_DIR, html=True), name="documents")


@app.get("/", response_class=HTMLResponse)
async def welcome(request: Request):
    return templates.TemplateResponse(
        request,
        "welcome.html",
        {
            "active_tab": "welcome",
            "welcome_doc": md_engine.get_welcome_doc(),
            "announcements": md_engine.get_announcements(),
        },
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
        raise HTTPException(status_code=404, detail="Story not found")
    return templates.TemplateResponse(
        request,
        "story_detail.html",
        {"active_tab": "stories", "story": story},
    )


@app.get("/guides", response_class=HTMLResponse)
async def guides(request: Request):
    return templates.TemplateResponse(
        request, "guides.html", {"active_tab": "guides", "guides": md_engine.get_guides()}
    )


@app.get("/guides/{slug}", response_class=HTMLResponse)
async def guide_detail(request: Request, slug: str):
    guide = md_engine.get_guide_by_slug(slug)
    if not guide:
        raise HTTPException(status_code=404, detail="Guide not found")
    return templates.TemplateResponse(
        request, "guide_detail.html", {"active_tab": "guides", "guide": guide}
    )


@app.get("/routes", response_class=HTMLResponse)
async def routes(request: Request):
    return templates.TemplateResponse(
        request,
        "routes.html",
        {"active_tab": "routes", "routes": routes_data.get_all_routes()},
    )


@app.get("/api/routes")
async def api_routes():
    return routes_data.get_all_routes()


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {"active_tab": "about"})


@app.post("/contact")
async def contact(
    name: str = Form(...),
    email: str = Form(...),
    interest: str = Form("general"),
    message: str = Form(""),
):
    # TODO: wire to email / Firestore / Google Sheet before production
    print(f"[contact] {name} <{email}> interest={interest}: {message}")
    return RedirectResponse(url="/about?sent=1#volunteer", status_code=303)


# Note: Cloud Run reserves paths ending in "z" (e.g. /healthz), so use /health.
@app.get("/health")
async def health():
    return {"status": "ok"}

