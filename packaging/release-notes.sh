#!/usr/bin/env bash
#
# packaging/release-notes.sh — generates a professional GitHub Release
# body for a tagged release.
#
# Output goes to stdout; the release.yml workflow captures it via
#     gh release create ... --notes-file <(packaging/release-notes.sh "$TAG")
#
# Composition (in order):
#   1. One-line tagline + status badges
#   2. Extracted CHANGELOG section for this version
#   3. "Install" section with all three paths + actual command lines
#      pointing at this release's artifacts
#   4. "Documentation" links to the full doc set under docs/
#   5. "Artifacts" table with SHA256 sums (filled in later by workflow)
#   6. "What HERMES is" + "What this release is NOT" honest scope
#   7. Footer with CHANGELOG link + previous-release diff URL
#
# Inputs:
#   $1 — the git tag (e.g. v0.1.0-alpha.26)
#
# Behaviour on unknown tags: prints a minimal body so a manual release
# from the GitHub UI doesn't fail; just less polished.

set -euo pipefail

TAG="${1:?usage: release-notes.sh <tag>}"
# Strip the leading "v" for matching the CHANGELOG section header
# (`## [0.1.0-alpha.26] — 2026-...`).
VERSION="${TAG#v}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHANGELOG="${REPO_ROOT}/CHANGELOG.md"

# Extract the section for this version. CHANGELOG sections look like:
#   ## [0.1.0-alpha.26] — 2026-04-26
#   ...
#   ## [0.1.0-alpha.25] — ...   ← stop here
#
# The awk below prints lines AFTER the header line for our version
# until the NEXT `## [...]` header. The literal `[`/`]` in the awk
# pattern would clash with bash escaping in some shells, so we pass
# the version as an awk variable.
extract_changelog_section() {
    if [ ! -r "$CHANGELOG" ]; then
        echo "_(CHANGELOG.md unavailable)_"
        return
    fi
    awk -v v="$VERSION" '
        /^## \[/ {
            if (found) exit
            if ($0 ~ "\\[" v "\\]") { found=1; next }
        }
        found { print }
    ' "$CHANGELOG"
}

# Find the previous tag for the "what changed since" link.
PREV_TAG="$(git tag --list 'v*' --sort=-version:refname \
    | grep -v "^${TAG}$" | head -1 || true)"

# Project metadata pulled from the README hero copy. Keeps the release
# body in sync if the elevator pitch is updated.
TAGLINE="High-frequency industrial sensor monitoring, event detection, and operator dashboard."

# ── Compose the body ──────────────────────────────────────────────

cat <<MD
# HERMES ${TAG}

> ${TAGLINE}

[![License: Proprietary](https://img.shields.io/badge/license-Proprietary-red.svg)](https://github.com/Rushikesh-Palande/hermes/blob/main/LICENSE)
[![Status: Pre-Alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](https://github.com/Rushikesh-Palande/hermes/blob/main/CHANGELOG.md)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Node: 20+](https://img.shields.io/badge/node-20%2B-green.svg)](https://nodejs.org/)

---

## What's new

$(extract_changelog_section)

---

## Install

Three install paths ship with every release. Pick the one that fits
your host. Full walkthrough: [\`docs/operations/INSTALLATION.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/operations/INSTALLATION.md).

### Path A — one-shot installer (recommended for Pi 4 / Linux box)

Download the \`.deb\` from the [Assets](#-assets) below — one file works
on amd64 and arm64 (the package is architecture-independent; the
platform-specific Python venv is built at install time):

\`\`\`bash
sudo dpkg -i hermes_${VERSION}_all.deb
sudo apt install -f                # resolve declared Depends from apt
\`\`\`

Or from a fresh git clone (any deb/rpm/arch/alpine Linux with internet):

\`\`\`bash
git clone --branch ${TAG} https://github.com/Rushikesh-Palande/hermes.git
cd hermes
sudo ./packaging/install.sh --operator-email you@your-org.com
\`\`\`

### Path B — Docker / Podman (any-Linux)

\`\`\`bash
# Pull + run the published image
docker run -d --name hermes-api \\
    -p 8080:8080 \\
    -e DATABASE_URL=postgresql+asyncpg://user:pw@host/db \\
    -e MIGRATE_DATABASE_URL=postgresql://user:pw@host/db \\
    -e HERMES_JWT_SECRET=\$(openssl rand -base64 48 | tr -d '\\n') \\
    ghcr.io/rushikesh-palande/hermes:${VERSION}

# Or the full compose stack (Postgres + Mosquitto + api + ingest)
curl -fsSL https://raw.githubusercontent.com/Rushikesh-Palande/hermes/${TAG}/packaging/docker-compose.prod.yml \\
    -o docker-compose.yml
docker compose up -d
\`\`\`

Multi-arch image: \`linux/amd64\` and \`linux/arm64\`. ~317 MB compressed.

### Path C — Offline bundle (air-gapped install)

Download \`hermes-${VERSION}-<arch>-offline.tar.gz\` from Assets, copy
to the air-gapped host (USB / SCP), then:

\`\`\`bash
tar -xzf hermes-${VERSION}-amd64-offline.tar.gz
cd hermes
sudo ./packaging/install.sh --offline --operator-email you@your-org.com
\`\`\`

Bundle contains the source tree + every system .deb + a Python
wheelhouse + a pre-built UI bundle. ~150 MB compressed. No internet
needed at install time.

---

## Documentation

Every aspect of the rewrite is documented under [\`docs/\`](https://github.com/Rushikesh-Palande/hermes/tree/${TAG}/docs).

| Topic | Read |
|-------|------|
| Get productive in 30 minutes | [\`docs/design/ARCHITECTURE.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/design/ARCHITECTURE.md) |
| End-to-end data flow walkthrough | [\`docs/guides/WORKFLOW.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/guides/WORKFLOW.md) |
| Every Python module + responsibility | [\`docs/guides/BACKEND.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/guides/BACKEND.md) |
| Every SvelteKit page + behaviour | [\`docs/guides/UI.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/guides/UI.md) |
| Detector mechanics (A/B/C/D + BREAK + mode switching) | [\`docs/guides/EVENTS.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/guides/EVENTS.md) |
| Every table, column, index | [\`docs/design/DATABASE_SCHEMA.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/design/DATABASE_SCHEMA.md) |
| Every REST endpoint | [\`docs/design/REST_API.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/design/REST_API.md) |
| Every env var + DB-backed setting | [\`docs/guides/CONFIGURATION.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/guides/CONFIGURATION.md) |
| Every Prometheus metric | [\`docs/guides/METRICS.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/guides/METRICS.md) |
| Multi-process deployment (4 cores on Pi 4) | [\`docs/design/MULTI_SHARD.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/design/MULTI_SHARD.md) |
| Local dev environment setup | [\`docs/guides/DEVELOPMENT.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/guides/DEVELOPMENT.md) |
| Test tier strategy | [\`docs/guides/TESTING.md\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/docs/guides/TESTING.md) |

---

## Assets

The workflow attaches the following artefacts to this release:

- \`hermes_${VERSION}_all.deb\` — Debian package (architecture-independent;
  works on amd64 and arm64)
- \`hermes-${VERSION}-amd64-offline.tar.gz\` — offline install bundle, x86_64
- \`SHA256SUMS\` — checksums for all of the above

Container image:

- \`ghcr.io/rushikesh-palande/hermes:${VERSION}\` (multi-arch: amd64 + arm64)
- \`ghcr.io/rushikesh-palande/hermes:latest\` (only updated on stable
  releases; pre-alpha tags don't move \`latest\`)

---

## What HERMES is

Production-ready industrial monitoring stack:

- Ingests 12-channel ADC telemetry from STM32 hardware over MQTT at
  ~100 Hz per sensor (~2 000 msg/s on a 20-device deployment).
- Runs four parallel event-detection algorithms (A/B/C/D — variance,
  tolerance band, absolute bound, two-stage drift) plus a BREAK
  state machine for sensor disconnect detection.
- Persists every event with a ±9 s sample window to TimescaleDB
  (Postgres extension).
- Republishes detected events back over MQTT for downstream PLC /
  SCADA consumers.
- Optionally archives every raw sample to a separate hypertable for
  forensic playback.
- Modbus TCP support for legacy PLCs.
- SvelteKit dashboard (uPlot live charts, event window viewer, session
  + threshold management UI).
- Prometheus metrics + structured logs.
- Multi-process shard mode for using all 4 cores on a Pi 4.
- Golden-traffic harness for behaviour-regression testing.

Bench: ~16 700 msg/s on a developer laptop, ~5 500 msg/s on a Pi 4 —
~2.7× headroom over the 2 000 msg/s production target.

## What this release is NOT (honest scope)

- **Not v1.0.** Pre-alpha — APIs, schema, and the operator UI may
  change before v1. Do not deploy to a customer site without a
  contract update path.
- **Not a single-binary AppImage.** HERMES is a multi-process service
  stack (Postgres + MQTT + Python ingest + Python API). Path B
  (container) is the closest to "any Linux, one file"; the full single-
  binary path is a separate release-engineering project.
- **Not auto-TLS.** All install paths ship HTTP-only. Run \`certbot
  --nginx\` after pointing DNS at your host.
- **Not auto-backups.** Set up a \`pg_dump\` cron after install. See
  the planned \`docs/operations/BACKUP_RESTORE.md\`.

---

## Diff

**Full Changelog**: https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/CHANGELOG.md
$(if [ -n "$PREV_TAG" ]; then
    echo ""
    echo "**Compare against previous tag**: https://github.com/Rushikesh-Palande/hermes/compare/${PREV_TAG}...${TAG}"
fi)

---

🤖 This release was published automatically by [\`.github/workflows/release.yml\`](https://github.com/Rushikesh-Palande/hermes/blob/${TAG}/.github/workflows/release.yml).

MD
