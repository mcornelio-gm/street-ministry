"""Read/write/delete editable content (Announcements and Stories).

On Cloud Run, CONTENT_MOUNT points at a Cloud Storage bucket volume. At startup,
Documents/Announcements and Documents/Stories are replaced by symlinks into that
bucket (seeding it from the image the first time), so edits persist across
restarts and deploys. Locally, files are edited in place under Documents/.
"""
import os
import re
import shutil
from datetime import date

import frontmatter

from app.md_engine import DOCUMENTS_DIR

KINDS = {
    "announcements": {"folder": "Announcements", "label": "Announcement"},
    "stories": {"folder": "Stories", "label": "Story"},
}
CITIES = ["Fremont", "Newark", "Union City", "Hayward"]
ANNOUNCEMENT_CATEGORIES = ["General", "Supplies & Donations", "Route Logistics", "Training", "Events"]
PRIORITIES = ["standard", "urgent"]
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,120}$")


class ContentError(ValueError):
    pass


def setup_content_mount():
    """If CONTENT_MOUNT is set, point editable folders at the bucket volume."""
    mount = os.environ.get("CONTENT_MOUNT")
    if not mount:
        return
    for kind in KINDS.values():
        local = os.path.join(DOCUMENTS_DIR, kind["folder"])
        remote = os.path.join(mount, kind["folder"])
        if os.path.islink(local):
            continue
        os.makedirs(remote, exist_ok=True)
        has_content = any(f.endswith(".md") and f != "index.md" for f in os.listdir(remote))
        if not has_content and os.path.isdir(local):
            print(f"Seeding {remote} from image")
            for name in os.listdir(local):
                if name.endswith(".md"):
                    shutil.copy2(os.path.join(local, name), os.path.join(remote, name))
        shutil.rmtree(local, ignore_errors=True)
        os.symlink(remote, local)


def _folder(kind: str) -> str:
    if kind not in KINDS:
        raise ContentError("Unknown content type")
    return os.path.join(DOCUMENTS_DIR, KINDS[kind]["folder"])


def _path(kind: str, slug: str) -> str:
    if not SLUG_RE.match(slug or "") or slug == "index":
        raise ContentError("Invalid file name")
    return os.path.join(_folder(kind), f"{slug}.md")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80].rstrip("-") or "untitled"


def _strip_title_heading(body: str) -> str:
    """Drop a leading '# Title' line; the site renders the title from the form."""
    lines = body.lstrip("\n").split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip("\n")


def list_items(kind: str) -> list[dict]:
    items = []
    folder = _folder(kind)
    if not os.path.isdir(folder):
        return items
    for name in os.listdir(folder):
        if not name.endswith(".md") or name == "index.md":
            continue
        post = frontmatter.load(os.path.join(folder, name))
        d = post.metadata.get("date")
        items.append({
            "slug": name[:-3],
            "title": post.metadata.get("title", name[:-3]),
            "date": str(d)[:10] if d else "",
            "city": post.metadata.get("city", ""),
            "priority": post.metadata.get("priority", ""),
            "author": post.metadata.get("author", ""),
        })
    items.sort(key=lambda x: (x["date"], x["slug"]), reverse=True)
    return items


def load_item(kind: str, slug: str) -> dict:
    path = _path(kind, slug)
    if not os.path.exists(path):
        raise ContentError("Not found")
    post = frontmatter.load(path)
    meta = dict(post.metadata)
    d = meta.get("date")
    meta["date"] = str(d)[:10] if d else ""
    tags = meta.get("tags") or []
    meta["tags"] = ", ".join(tags) if isinstance(tags, list) else str(tags)
    return {"slug": slug, "meta": meta, "body": _strip_title_heading(post.content)}


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        raise ContentError("Please enter a valid date")


def save_item(kind: str, form: dict, slug: str | None = None) -> str:
    """Create (slug=None) or update an item. Returns the slug."""
    title = (form.get("title") or "").strip()
    if not title:
        raise ContentError("Title is required")
    d = _parse_date(form.get("date", ""))
    body = _strip_title_heading((form.get("body") or "").replace("\r\n", "\n"))
    if not body.strip():
        raise ContentError("Please write some content")

    meta = {"title": title, "date": d, "author": (form.get("author") or "").strip()}
    if kind == "stories":
        city = form.get("city", "")
        if city not in CITIES:
            raise ContentError("Please choose a city")
        meta["city"] = city
        meta["tags"] = [t.strip() for t in (form.get("tags") or "").split(",") if t.strip()]
        meta["summary"] = (form.get("summary") or "").strip()
    else:
        category = (form.get("category") or "General").strip()
        meta["category"] = category or "General"
        priority = form.get("priority", "standard")
        meta["priority"] = priority if priority in PRIORITIES else "standard"

    if slug is None:
        base = f"{d.isoformat()}-{slugify(title)}"
        slug, n = base, 2
        while os.path.exists(_path(kind, slug)):
            slug, n = f"{base}-{n}", n + 1
    path = _path(kind, slug)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(frontmatter.Post(body + "\n", **meta), sort_keys=False))
        f.write("\n")
    os.replace(tmp, path)
    return slug


def delete_item(kind: str, slug: str):
    path = _path(kind, slug)
    if not os.path.exists(path):
        raise ContentError("Not found")
    os.remove(path)

