# Changelog

All notable changes to HERMES are documented in this file.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Pre-release suffixes (`-alpha.N`, `-beta.N`, `-rc.N`) are used until v1.0.0.

## [Unreleased]

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

[Unreleased]: https://github.com/Rushikesh-Palande/hermes/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/Rushikesh-Palande/hermes/releases/tag/v0.0.1
