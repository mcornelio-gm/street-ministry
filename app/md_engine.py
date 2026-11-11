import os
import glob
import shutil
import subprocess
import threading
from datetime import datetime, date
import frontmatter
import markdown

DOCUMENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Documents"))
SITE_DOCS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "site", "documents"))

MD_EXTENSIONS = [
    "extra",
    "admonition",
    "tables",
    "toc",
    "codehilite",
]


def render_markdown(text: str) -> str:
    """Render markdown string to HTML with standard extensions."""
    return markdown.markdown(text, extensions=MD_EXTENSIONS)


def get_welcome_doc() -> dict:
    """Read and parse Documents/Welcome.md."""
    path = os.path.join(DOCUMENTS_DIR, "Welcome.md")
    if not os.path.exists(path):
        return {
            "title": "Welcome to Resonate Church Street Ministry",
            "date": "",
            "author": "",
            "html": "<p>Welcome document coming soon.</p>",
        }
    post = frontmatter.load(path)
    html_content = render_markdown(post.content)
    return {
        "title": post.metadata.get("title", "Welcome to Street Ministry"),
        "date": post.metadata.get("date", ""),
        "author": post.metadata.get("author", "Resonate Street Ministry"),
        "metadata": post.metadata,
        "html": html_content,
        "raw": post.content,
    }


def get_announcements() -> list:
    """Read all markdown announcements from Documents/Announcements, sorted by date descending."""
    announcements_dir = os.path.join(DOCUMENTS_DIR, "Announcements")
    files = glob.glob(os.path.join(announcements_dir, "*.md"))
    announcements = []

    for file_path in files:
        filename = os.path.basename(file_path)
        if filename.lower() == "index.md":
            continue

        try:
            post = frontmatter.load(file_path)
            raw_date = post.metadata.get("date")
            sort_date = ""

            if isinstance(raw_date, (datetime, date)):
                sort_date = raw_date.strftime("%Y-%m-%d")
                display_date = raw_date.strftime("%B %d, %Y")
            elif raw_date:
                sort_date = str(raw_date)
                try:
                    dt = datetime.strptime(sort_date[:10], "%Y-%m-%d")
                    display_date = dt.strftime("%B %d, %Y")
                except Exception:
                    display_date = sort_date
            else:
                sort_date = "1970-01-01"
                display_date = "Recent"

            slug = os.path.splitext(filename)[0]
            html_content = render_markdown(post.content)

            announcements.append({
                "slug": slug,
                "filename": filename,
                "title": post.metadata.get("title", slug.replace("-", " ").title()),
                "date_sort": sort_date,
                "date_display": display_date,
                "author": post.metadata.get("author", "Ministry Team"),
                "category": post.metadata.get("category", "General"),
                "priority": post.metadata.get("priority", "standard").lower(),
                "metadata": post.metadata,
                "html": html_content,
                "raw": post.content,
            })
        except Exception as e:
            print(f"Error parsing announcement {file_path}: {e}")

    # Sort descending by date (filename as tiebreaker for a stable order)
    announcements.sort(key=lambda x: (x["date_sort"], x["filename"]), reverse=True)
    return announcements


def get_stories() -> list:
    """Read all stories from Documents/Stories, sorted by date descending."""
    stories_dir = os.path.join(DOCUMENTS_DIR, "Stories")
    files = glob.glob(os.path.join(stories_dir, "*.md"))
    stories = []

    for file_path in files:
        filename = os.path.basename(file_path)
        if filename.lower() == "index.md":
            continue

        try:
            post = frontmatter.load(file_path)
            raw_date = post.metadata.get("date")
            sort_date = ""

            if isinstance(raw_date, (datetime, date)):
                sort_date = raw_date.strftime("%Y-%m-%d")
                display_date = raw_date.strftime("%B %d, %Y")
            elif raw_date:
                sort_date = str(raw_date)
                try:
                    dt = datetime.strptime(sort_date[:10], "%Y-%m-%d")
                    display_date = dt.strftime("%B %d, %Y")
                except Exception:
                    display_date = sort_date
            else:
                sort_date = "1970-01-01"
                display_date = "Recent"

            slug = os.path.splitext(filename)[0]
            html_content = render_markdown(post.content)

            # Calculate rough read time
            word_count = len(post.content.split())
            read_time_min = max(1, round(word_count / 180))

            stories.append({
                "slug": slug,
                "filename": filename,
                "title": post.metadata.get("title", slug.replace("-", " ").title()),
                "date_sort": sort_date,
                "date_display": display_date,
                "city": post.metadata.get("city", "East Bay"),
                "author": post.metadata.get("author", "Outreach Volunteer"),
                "tags": post.metadata.get("tags", []),
                "summary": post.metadata.get("summary", ""),
                "cover_image": post.metadata.get("cover_image", ""),
                "read_time_min": read_time_min,
                "metadata": post.metadata,
                "html": html_content,
                "raw": post.content,
            })
        except Exception as e:
            print(f"Error parsing story {file_path}: {e}")

    # Sort descending by date (filename as tiebreaker for a stable order)
    stories.sort(key=lambda x: (x["date_sort"], x["filename"]), reverse=True)
    return stories


def get_story_by_slug(slug: str) -> dict | None:
    """Find a specific story by its slug."""
    stories = get_stories()
    for s in stories:
        if s["slug"] == slug:
            return s
    return None


_build_lock = threading.Lock()


def build_mkdocs() -> bool:
    """Build the MkDocs site into a temp dir, then swap it into site/documents.

    The swap is two renames, so visitors never see a half-built wiki.
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    mkdocs_bin = os.path.join(project_root, ".venv", "bin", "mkdocs")
    if not os.path.exists(mkdocs_bin):
        mkdocs_bin = shutil.which("mkdocs") or "mkdocs"
    new_dir = SITE_DOCS_DIR + ".new"
    old_dir = SITE_DOCS_DIR + ".old"
    with _build_lock:
        shutil.rmtree(new_dir, ignore_errors=True)
        result = subprocess.run(
            [mkdocs_bin, "build", "--site-dir", new_dir],
            cwd=project_root,
            capture_output=True,
            text=True,
            env={**os.environ, "NO_MKDOCS_2_WARNING": "1"},
        )
        if result.returncode != 0:
            print(f"mkdocs build failed:\n{result.stderr}")
            shutil.rmtree(new_dir, ignore_errors=True)
            return False
        shutil.rmtree(old_dir, ignore_errors=True)
        if os.path.isdir(SITE_DOCS_DIR):
            os.rename(SITE_DOCS_DIR, old_dir)
        os.rename(new_dir, SITE_DOCS_DIR)
        shutil.rmtree(old_dir, ignore_errors=True)
    return True

