# Changelog

All notable changes to the TOSCA backend. The project uses
[Semantic Versioning](https://semver.org/); see
[Versions and releases](README.md#versions-and-releases).

## [Unreleased]

### Added
- `make release VERSION=X.Y.Z` tags `main` and pushes the tag after checking
  it is releasable; `make image` builds the production image locally as CI
  does. Neither needs an environment file.

### Changed
- Environment files are named `.env.<mode>` with the mode written out, as in
  every TOSCA repository: the templates are `.env.development.example` and
  `.env.production.example` (was one `.env.example`), the local copies
  `.env.development` and `.env.production` (were `.env.dev`, `.env.prod`).
  Rename existing local files; `make` says so when it finds an old name.
  `ENV=dev|prod` is unchanged.
- The settings no longer fall back to a plain `.env` file.
- The pre-commit hook allows only the two templates.
- `geoserver-rest` is installed from Git at a pinned commit of
  `orttak/geoserver-rest` instead of the `vendor/geoserver-rest` submodule
  (same code, `10bef2c`). Dependabot could not resolve the dependencies
  because it does not check out submodules, so every update it tried failed.
  The images now install `git` for this.

## [1.0.0] - 2026-11-26

First release published to GHCR as a versioned image. The code is the
backend that runs the NGCN installation of the TOSCA platform.

### Added
- `manage.py init_storage`: creates the missing media buckets with the
  application's own S3 key and applies the storage CORS rules. Safe to run on
  every start; needs no Garage admin token.
- `GET /api/version`: the backend version and commit baked into the image, and
  the platform release the installation belongs to.
- The production image carries its version and commit as OCI labels and as
  `TOSCA_BACKEND_VERSION` / `TOSCA_BACKEND_REVISION`.
- CI on pull requests and `main`: secret scan, ruff, pytest against PostGIS and
  Garage, and a production-mode smoke test of the built image.
- Release workflow: images are published to GHCR only from a `v*` tag, after
  the tests and smoke test pass on the exact build that is pushed, together
  with a GitHub Release.
