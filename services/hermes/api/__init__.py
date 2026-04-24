"""
hermes.api — FastAPI HTTP + SSE server.

Entry point: `hermes.api.__main__:main` (also installed as the
`hermes-api` console script via pyproject.toml).

Subpackages:
    routes  — one module per resource (health, auth, devices, …).
    deps    — FastAPI dependency injectables (current_user, db_session).
    main    — `create_app()` factory used by both __main__ and tests.

This package does NOT own business logic. Route handlers delegate to
service functions in higher-level modules (hermes.sessions,
hermes.packages, etc.) — the API layer only knows about HTTP shapes
and permission checks.
"""
