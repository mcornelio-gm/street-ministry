"""Manage admin users in the local .env file (never committed to git).

Usage:
  .venv/bin/python scripts/manage_admins.py add <username>     # prompts for password
  .venv/bin/python scripts/manage_admins.py remove <username>
  .venv/bin/python scripts/manage_admins.py list

Also creates SESSION_SECRET in .env if missing.
After changing users, push to Secret Manager for the live site (see README).
"""
import getpass
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.auth import hash_password, parse_users  # noqa: E402

ENV_PATH = os.path.join(ROOT, ".env")


def read_env() -> dict[str, str]:
    values = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip("'\"")
    return values


def write_env(values: dict[str, str]):
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("# Local secrets for Street Ministry. DO NOT COMMIT (listed in .gitignore).\n")
        for k, v in values.items():
            f.write(f"{k}='{v}'\n")
    os.chmod(ENV_PATH, 0o600)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("add", "remove", "list"):
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    env = read_env()
    env.setdefault("SESSION_SECRET", secrets.token_urlsafe(48))
    users = parse_users(env.get("ADMIN_USERS", ""))

    if cmd == "list":
        print("\n".join(sorted(users)) or "(no admin users)")
        return

    if len(sys.argv) < 3:
        print("Username required.")
        sys.exit(1)
    name = sys.argv[2].strip().lower()
    if not name.replace("-", "").replace("_", "").replace(".", "").isalnum():
        print("Username may contain letters, numbers, '.', '-', '_' only.")
        sys.exit(1)

    if cmd == "add":
        pw = getpass.getpass(f"Password for {name}: ")
        if len(pw) < 10:
            print("Use at least 10 characters.")
            sys.exit(1)
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            sys.exit(1)
        users[name] = hash_password(pw)
        print(f"Saved user '{name}'.")
    else:
        if users.pop(name, None) is None:
            print(f"No such user '{name}'.")
            sys.exit(1)
        print(f"Removed user '{name}'.")

    env["ADMIN_USERS"] = ",".join(f"{u}:{h}" for u, h in sorted(users.items()))
    write_env(env)


if __name__ == "__main__":
    main()

