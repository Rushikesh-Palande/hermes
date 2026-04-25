# HERMES — production container image.
#
# Multi-stage build:
#   1. ui-builder   : Node + pnpm → builds the SvelteKit production bundle
#   2. py-builder   : Python + uv → builds the Python venv with all wheels
#   3. runtime      : Slim Python base, copies the built artefacts in,
#                     no toolchains in the final layer
#
# Image size target: ~250 MB compressed. The runtime base is python:3.11-slim
# (~120 MB) plus the venv (~80 MB) plus the SvelteKit build (~5 MB) plus
# some tools (curl, ca-certificates) for healthchecks.
#
# Run:
#     docker build -t hermes:local -f packaging/Dockerfile .
#     docker run -p 8080:8080 -e DATABASE_URL=... hermes:local
#
# More commonly via the compose file:
#     docker compose -f packaging/docker-compose.prod.yml up -d
#
# Multi-arch:
#     docker buildx build --platform linux/amd64,linux/arm64 \
#         -t ghcr.io/<org>/hermes:0.1.0-alpha.X \
#         -f packaging/Dockerfile --push .

# ─── Stage 1: UI build ────────────────────────────────────────────

FROM node:20-alpine AS ui-builder

# pnpm via corepack — pinned to the version we develop against.
RUN corepack enable && corepack prepare pnpm@9 --activate

WORKDIR /build/ui

# Cache deps install separately from the rest of the source.
COPY ui/package.json ui/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY ui/ ./
RUN pnpm build

# ─── Stage 2: Python venv build ───────────────────────────────────
#
# Build under /opt/hermes from the start so the editable install's
# .pth file references the same path the runtime stage will use. If
# we built in /build and copied to /opt/hermes, the .pth would point
# at /build/services and `import hermes` would ModuleNotFoundError
# at runtime.

FROM python:3.11-slim AS py-builder

# uv is the fastest installer — same one we use locally.
RUN pip install --no-cache-dir "uv>=0.5"

WORKDIR /opt/hermes

# Cache deps install separately from the rest of the source. We need
# pyproject.toml + uv.lock + a stub __init__ to satisfy `uv sync`'s
# editable build. The full source overlays in the next layer.
COPY pyproject.toml uv.lock README.md ./
COPY services/hermes/__init__.py services/hermes/__init__.py
COPY migrations/ migrations/

# Build the venv with locked deps. --no-dev skips ruff/mypy/pytest.
RUN uv venv .venv && uv sync --no-dev

COPY services/ services/
# Re-run uv sync to pick up the now-complete source tree for the
# editable install. The .pth file ends up pointing at
# /opt/hermes/services, which is where the runtime stage will copy.
RUN uv sync --no-dev

# ─── Stage 3: runtime ─────────────────────────────────────────────

FROM python:3.11-slim AS runtime

# Pinned versions of the runtime tools we need beyond Python itself.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. UID/GID 1000 by convention for container runtimes
# that map to host users (rootless Docker, Podman).
RUN groupadd --system --gid 1000 hermes \
    && useradd --system --uid 1000 --gid hermes \
        --home-dir /opt/hermes --shell /usr/sbin/nologin \
        --comment "HERMES sensor dashboard" \
        hermes \
    && mkdir -p /opt/hermes /var/log/hermes /var/lib/hermes \
    && chown -R hermes:hermes /opt/hermes /var/log/hermes /var/lib/hermes

WORKDIR /opt/hermes

# Pull in the venv + the source from the builder. Paths must match
# what py-builder used (/opt/hermes) so the editable install's .pth
# file resolves correctly.
COPY --from=py-builder --chown=hermes:hermes /opt/hermes/.venv /opt/hermes/.venv
COPY --from=py-builder --chown=hermes:hermes /opt/hermes/services /opt/hermes/services
COPY --from=py-builder --chown=hermes:hermes /opt/hermes/pyproject.toml /opt/hermes/
COPY --from=py-builder --chown=hermes:hermes /opt/hermes/uv.lock /opt/hermes/
COPY --from=py-builder --chown=hermes:hermes /opt/hermes/migrations /opt/hermes/migrations

# Pull in the UI build. Adapter-node mode — the bundle is a Node
# server we run alongside the API. For the static-only flavour, copy
# only ui/build/client/ and serve via nginx.
COPY --from=ui-builder --chown=hermes:hermes /build/ui/build /opt/hermes/ui/build
COPY --from=ui-builder --chown=hermes:hermes /build/ui/package.json /opt/hermes/ui/

# scripts/db-migrate.sh + packaging/ stay so the container can be
# used as a one-shot to apply migrations.
COPY --chown=hermes:hermes scripts/ /opt/hermes/scripts/
COPY --chown=hermes:hermes packaging/ /opt/hermes/packaging/

# Make the venv's bin first in PATH so `hermes-api` / `hermes-ingest`
# resolve without a venv activation.
ENV PATH="/opt/hermes/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
ENV HERMES_LOG_FORMAT=json

USER hermes

EXPOSE 8080

# Healthcheck hits /api/health — same shape the systemd unit uses.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health >/dev/null || exit 1

# tini reaps zombies + handles SIGTERM correctly inside containers
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default to the API service. Override with `command:` in compose to
# run the ingest. Dual-process containers are not idiomatic; the
# compose file runs API + ingest in two containers sharing volumes.
CMD ["hermes-api"]
