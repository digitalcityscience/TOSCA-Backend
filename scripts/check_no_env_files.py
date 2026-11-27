"""
Pre-commit hook: block committing any `.env*` file that is not a template.

Environment files are named `.env.<mode>` with the mode written out
(`.env.development`, `.env.production`); they carry live credentials and must
never be committed. Only their templates, `.env.development.example` and
`.env.production.example` (placeholders only), are tracked. Any other `.env*`
name is refused too, including the old `.env`, `.env.dev`, `.env.prod` and
`.env.example`. Wired up as a local pre-commit hook (see
.pre-commit-config.yaml), which passes the staged filenames as argv.
"""
from __future__ import annotations

import sys
from pathlib import Path

ALLOWED_ENV_FILES = {".env.development.example", ".env.production.example"}


def find_forbidden_env_files(filenames: list[str]) -> list[str]:
    """Return the subset of filenames that are `.env*` files other than the templates."""
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
        print("Refusing to commit these environment file(s):")
        for path in forbidden:
            print(f"  {path}")
        print(
            "Only .env.development.example and .env.production.example "
            "(placeholders only) may be committed."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
