"""
Pre-commit hook: block committing any real `.env*` file.

`.env`, `.env.dev`, `.env.prod`, and `.env.test` all carry live credentials
and must never be committed — only `.env.example` (placeholders only) is
allowed to be tracked. Wired up as a local pre-commit hook (see
.pre-commit-config.yaml), which passes the staged filenames as argv.
"""
from __future__ import annotations

import sys
from pathlib import Path

ALLOWED_ENV_FILES = {".env.example"}


def find_forbidden_env_files(filenames: list[str]) -> list[str]:
    """Return the subset of filenames that look like a real (non-example) .env file."""
    forbidden = []
    for filename in filenames:
        name = Path(filename).name
        if name in ALLOWED_ENV_FILES:
            continue
        if name == ".env" or name.startswith(".env."):
            forbidden.append(filename)
    return forbidden


def main(argv: list[str]) -> int:
    forbidden = find_forbidden_env_files(argv)
    if forbidden:
        print("Refusing to commit real .env file(s) — these carry live credentials:")
        for path in forbidden:
            print(f"  {path}")
        print("Only .env.example (placeholders only) may be committed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
