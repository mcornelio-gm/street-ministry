# AGENTS.md: guide for coding assistants and webmasters

Resonate Church Street Ministry website: FastAPI + Jinja2 site with a MkDocs Material
wiki, hosted on Cloud Run. See README.md for full documentation. This file is the short
list of rules that keep the live site and its content safe.

## Golden rules

1. **Live content is mastered in the bucket, not git.** `Documents/Announcements/`,
   `Documents/Stories/`, `Documents/Guides/`, and `data/routes.json` are edited on the live
   site in `/admin` and stored in `gs://resonate-street-ministry-content`. Git holds a backup.
   - Before touching those files locally: `scripts/sync_content.sh pull`.
   - Never overwrite uncommitted local content edits. `sync_content.sh pull` and
     `deploy.sh` refuse to; don't bypass with `--force` unless the user asks.
   - Local edits to those folders do **not** go live on deploy. Send them with
     `scripts/sync_content.sh push` (or `gcloud storage cp` for one file) *after* confirming
     the live copy hasn't changed since your last pull.
2. **Release with `scripts/deploy.sh`**: pull live content → tests → commit & push → deploy.
   If deploying by hand, do the same steps in the same order.
3. **Never commit secrets:** `.env` (admin users, session key) and `.admin_passwords.json`
   (self-chosen password hashes) are gitignored and dockerignored. Keep it that way.
4. **Don't use Google geocoding.** Its terms forbid showing results on non-Google maps
   (we use Leaflet + OpenStreetMap) and storing coordinates over 30 days. Lookups are
   US Census (street addresses), then OpenStreetMap Nominatim limited to the Bay Area.
   Cross streets get an approximate pin that admins drag into place.
5. **Respect privacy.** Stories use first names or pseudonyms ("name changed"), never last
   names, exact sleeping spots, or photos without consent. Route stops are often vague
   cross streets on purpose.
6. **Run the tests** (`.venv/bin/pytest -q`) before any commit. Tests must not depend on
   specific content (editors change it) and must not modify real data (use temp files /
   monkeypatch, see `tests/test_admin_routes.py`).

## Layout

| Path | What |
|---|---|
| `app/main.py` | Public pages: `/`, `/documents/` (static MkDocs), `/guides`, `/stories`, `/routes`, `/about` |
| `app/admin.py`, `app/admin_routes.py` | `/admin`: login, password change, CRUD for announcements/stories/guides/routes |
| `app/auth.py` | PBKDF2 hashes; users from `ADMIN_USERS`; self-chosen passwords; session fingerprint |
| `app/content_store.py` | Markdown CRUD, rename-on-title/date-change with `aliases` redirects, bucket mount setup |
| `app/routes_data.py` | `routes.json` load/save (version check rejects stale saves), geocoding |
| `app/md_engine.py` | Markdown rendering, listing, atomic MkDocs rebuild (`build_mkdocs`) |
| `hooks.py` | MkDocs hook: regenerates `Documents/{Routes,Stories,Announcements,Guides}/index.md` (never edit those by hand) |
| `scripts/` | `deploy.sh`, `sync_content.sh`, `manage_admins.py`, `import_routes_csv.py` |
| `theme_overrides/` | MkDocs Material overrides (site header, shared light/dark preference) |

## Common commands

```bash
.venv/bin/uvicorn app.main:app --reload --reload-include .env --port 8000   # local preview
.venv/bin/mkdocs build                                  # rebuild wiki after editing Documents/
.venv/bin/pytest -q                                     # tests
scripts/sync_content.sh pull                            # live content -> local
scripts/deploy.sh                                       # full release
.venv/bin/python scripts/manage_admins.py add <name>    # add/reset admin (then push secret, see README)
```

## Cloud setup

- GCP project `resonate-street-ministry`, account `mcornelio@gmail.com` (the gcloud default
  project may be a different site; always pass `--project`).
- Cloud Run service `street-ministry`, region `us-west1`, max 1 instance, gen2,
  bucket mounted at `/mnt/content` (`CONTENT_MOUNT`); at startup the content folders are
  symlinked into the bucket (seeded from the image only if empty).
- Secrets: `admin-users` → `ADMIN_USERS`, `session-secret` → `SESSION_SECRET`. The running
  site reads them only at startup; after updating a secret, start a new revision.
- Bucket `Private/` holds self-chosen password hashes; it is never synced to git.

## Gotchas learned the hard way

- Cloud Run reserves paths ending in `z` (`/healthz` returns 404); the health check is `/health`.
- Don't rename directories baked into the container image (EXDEV on overlayfs); the wiki
  is built at startup, and `build_mkdocs` swaps directories with a fallback.
- A local preview started inside a network sandbox can't geocode (lookups return 403).
- Browsers cache `style.css`; it's linked with `?v=<content hash>` (`css_v`). Keep that.
- Colors are scheme-aware tokens (`light-dark()` with a `prefers-color-scheme` fallback).
  Use `var(--surface)`, `var(--slate-*)`, `var(--link)` and the tint tokens, not hard-coded
  light colors. The base `color-scheme` must stay `light dark`.
- On the Routes page and in templates, only map pop-ups, flowcharts, and the editor stay
  light on purpose.
