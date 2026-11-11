# Resonate Church Street Ministry

Website for the Resonate Church Street Ministry, serving unhoused neighbors in
Fremont, Newark, Union City, and Hayward, California.

**Live site:** https://street-ministry-290723764988.us-west1.run.app
**GCP project:** `resonate-street-ministry` (Cloud Run service `street-ministry`, region `us-west1`)

## Tabs and where their content lives

| Tab | Source |
|---|---|
| Welcome | `Documents/Welcome.md` + `Documents/Announcements/*.md` (newest first) |
| Documents | MkDocs Material wiki built from everything in `Documents/` |
| Stories | `Documents/Stories/*.md` (newest first) |
| Routes | `data/routes.json` (table + Leaflet map) |
| About | `app/templates/about.html` |
| Admin | `/admin`: edit Announcements and Stories in the browser |

Generated files (do not edit by hand; `hooks.py` rewrites them on every `mkdocs build`):
`Documents/Routes/index.md`, `Documents/Stories/index.md`, `Documents/Announcements/index.md`.

## Where the master copy of content lives

> **Important:** On the live site, **Announcements and Stories live in the Cloud Storage
> bucket `gs://resonate-street-ministry-content`**, not in git. Edits made in `/admin`
> exist only in the bucket until someone pulls them. Redeploying does **not** overwrite
> the bucket, and editing those folders locally then deploying does **not** update the
> live site. Use `scripts/sync_content.sh push` for that.

Everything else (code, `Welcome.md`, other documents, `data/routes.json`) lives in git
and goes live by redeploying.

### Periodic backup to GitHub

Happens automatically on every `scripts/deploy.sh`. To archive without deploying:

```bash
scripts/deploy.sh --no-deploy
```

Bucket versioning keeps every prior version of each file, so a bad edit or delete can be
restored from the bucket even between pulls:
```bash
gcloud storage ls --all-versions gs://resonate-street-ministry-content/Stories/
```

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/mkdocs build                       # build the Documents wiki
.venv/bin/uvicorn app.main:app --reload --reload-include .env --port 8000
.venv/bin/pytest -q                          # tests
```

## Admin users

Users and the session key live in `.env` (gitignored, never committed or deployed).
Passwords are stored as PBKDF2 hashes.

```bash
.venv/bin/python scripts/manage_admins.py add <name>      # prompts for password
.venv/bin/python scripts/manage_admins.py remove <name>
.venv/bin/python scripts/manage_admins.py list
```

The live site reads the same values from Secret Manager (`admin-users`, `session-secret`).
After changing users, push the new value and redeploy:
```bash
grep '^ADMIN_USERS=' .env | cut -d= -f2- | tr -d "'" | \
  gcloud secrets versions add admin-users --data-file=- --project=resonate-street-ministry
```

## Deploy

```bash
scripts/deploy.sh               # pull live content -> test -> commit & push to GitHub -> deploy
scripts/deploy.sh --no-deploy   # just archive live content to GitHub
```
Every release pulls `/admin` edits from the bucket and commits them, so GitHub stays a
current backup automatically. If you have uncommitted local edits in Announcements/Stories,
the script stops rather than overwrite them.

The service keeps its bucket mount, secrets, and settings between deploys.

## Handing the site over to someone else

1. `scripts/sync_content.sh pull`, then commit and push, so GitHub has all live content.
2. Give the new owner access to the GitHub repo and the `resonate-street-ministry` GCP
   project (IAM: Owner), and move billing if needed.
3. Have them create their own admin login (`manage_admins.py add`), push it to Secret
   Manager, and remove the old users.
4. Hand over this README. The "master copy" section above is the one thing to understand.

