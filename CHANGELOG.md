# Changelog

All notable changes to HERMES are documented in this file.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Pre-release suffixes (`-alpha.N`, `-beta.N`, `-rc.N`) are used until v1.0.0.

## [Unreleased]

## [0.1.0-alpha.2] — 2026-04-24

### Fixed
- CI was red from the first push: the `astral-sh/setup-uv` and
  `pnpm/action-setup` cache steps fail without committed lockfiles.
  Generated and committed `uv.lock` and `ui/pnpm-lock.yaml`, and removed
  the `uv.lock` entry from `.gitignore` (it IS source for an application).
- FastAPI routes using `EmailStr` imported cleanly but failed at runtime
  for lack of `email-validator`. Added the `pydantic[email]` extra.
- `/api/health` returned 404 — the health router was mounted at prefix
  `/api` with route path `""`, resolving to `/api` instead of
  `/api/health`. Mount prefix now matches the contract in
  `tests/unit/test_health.py`.
- Dropped `default_response_class=ORJSONResponse` on the FastAPI app;
  FastAPI 0.115+ emits a deprecation error when `orjson` isn't installed
  and Pydantic v2's direct JSON serialisation is the recommended default.
- `hermes.db.models.Event` was unmappable: composite PK was declared via
  `UniqueConstraint` instead of `primary_key=True` on both columns.
  Fixed to match `migrations/0002_core_tables.sql`.
- ORM enums switched to `enum.StrEnum` (ruff UP042) and JSONB columns
  parameterised as `dict[str, Any]` (mypy `type-arg`).
- `ui/tsconfig.json` was overriding the `include` list inherited from
  SvelteKit's auto-generated tsconfig, silently dropping `vite.config.ts`
  from the IDE's project. Removed the override.

### Added
- `pydantic[email]` extra to `pyproject.toml`.

## [0.1.0-alpha.1] — 2026-04-24

### Added
- Phase 1 foundation scaffolding: Python (`pyproject.toml`, ruff, mypy, pytest)
  and SvelteKit frontend shells.
- Initial SQL migrations (`migrations/0001_init_extensions.sql` … `0005_retention_policies.sql`)
  implementing the packages + sessions + events data model from
  `docs/design/DATABASE_REDESIGN.md`.
- FastAPI skeleton (`services/hermes/api/`): health endpoint, OTP auth stubs,
  device CRUD stubs.
- MQTT ingest skeleton (`services/hermes/ingest/`) — connects, subscribes,
  no business logic yet.
- `docker-compose.dev.yml` for local Postgres+TimescaleDB, Mosquitto, and Redis.
- GitHub Actions CI: ruff, mypy, pytest, pnpm typecheck.
- Dev helper scripts (`scripts/db-migrate.sh`, `scripts/dev-up.sh`,
  `scripts/dev-down.sh`) and `packaging/README.md` placeholder.

### Security
- Redacted the Gmail app password that had been exposed in
  `docs/reference/ops/ops_files.md` (the credential has been rotated; see
  `SECURITY.md` for context).

## [0.0.1] — 2026-04-24

### Added
- Phase 0.5 behaviour contracts (`docs/contracts/`): hardware interface,
  ingestion pipeline, event detection, database, API, config catalog,
  worker protocol, bug decision log, golden traffic plan.
- Phase 0.5 per-file reference library (`docs/reference/`) covering every
  template, static asset, Modbus legacy subsystem, tests, scripts, and ops
  files from the legacy codebase.
- Database redesign design document (`docs/design/DATABASE_REDESIGN.md`)
  locking the packages + parameters + sessions + events schema.
- Repository metadata: README, LICENSE (proprietary), SECURITY, CONTRIBUTING,
  CODE_OF_CONDUCT, CODEOWNERS, issue and PR templates, Dependabot config,
  `.gitignore`, `.gitattributes`, `.editorconfig`.

[Unreleased]:     https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.2...HEAD
[0.1.0-alpha.2]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.1...v0.1.0-alpha.2
[0.1.0-alpha.1]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.0.1...v0.1.0-alpha.1
[0.0.1]:          https://github.com/Rushikesh-Palande/hermes/releases/tag/v0.0.1
