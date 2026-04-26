#!/usr/bin/env bash
#
# HERMES — build a self-contained offline install bundle.
#
# Output: a single .tar.gz that, when unpacked on a fresh Debian-based
# Linux (no internet, no apt sources), can install the full HERMES
# stack via `sudo ./install.sh --offline`.
#
# Bundles:
#   1. The repository source tree
#   2. Pre-downloaded .deb files for system packages
#      (postgresql-16, timescaledb, mosquitto, nginx, python3.11, ...)
#   3. A Python wheelhouse with every dep pinned by uv.lock, built
#      for the target architecture
#   4. Pre-built SvelteKit production bundle (so node + pnpm aren't
#      needed at install time)
#
# Output sizes:
#   - amd64: ~150 MB compressed
#   - arm64: ~140 MB compressed (no Timescale toolchain in deps)
#
# Usage:
#     ./packaging/build-offline-bundle.sh --arch amd64 \
#         --out hermes-0.1.0-alpha.X-amd64-offline.tar.gz
#
# Requirements (on the build host, NOT the install host):
#   - apt-get + dpkg-deb (for downloading and re-archiving .debs)
#   - uv + a target-arch Python (or run inside a docker buildx container)
#   - pnpm (for the UI build)
#
# Tested on Debian 12 amd64. arm64 builds run inside qemu-user-static
# under docker buildx --platform linux/arm64.

set -euo pipefail
IFS=$'\n\t'

# ─── Configuration ────────────────────────────────────────────────

ARCH="amd64"
OUT="hermes-offline.tar.gz"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

while [ $# -gt 0 ]; do
    case "$1" in
        --arch) ARCH="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --help|-h)
            sed -n '3,/^# Usage/p' "$0" | sed 's/^# *//'
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ─── Stage in a temp dir ──────────────────────────────────────────

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
BUNDLE="${STAGE}/hermes"
install -d "$BUNDLE"

echo "[1/5] Copying repo source tree..."
rsync -a \
    --exclude='.git' --exclude='.venv' --exclude='node_modules' \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='.svelte-kit' \
    --exclude='ui/.svelte-kit' --exclude='packaging/offline' \
    --exclude='packaging/wheelhouse' \
    "${REPO_ROOT}/" "${BUNDLE}/"

# ─── Stage system .debs ───────────────────────────────────────────

echo "[2/5] Downloading system .deb packages for ${ARCH}..."
install -d "${BUNDLE}/packaging/offline"

# Use apt-get download to fetch packages without installing them.
# `apt-rdepends` would be more thorough but isn't always installed;
# this list is hand-curated to match install.sh's package list.
DEBS=(
    postgresql-16
    timescaledb-2-postgresql-16
    timescaledb-tools
    mosquitto
    mosquitto-clients
    nginx
    python3.11
    python3.11-venv
    python3-pip
)

(
    cd "${BUNDLE}/packaging/offline"
    for pkg in "${DEBS[@]}"; do
        echo "  - $pkg"
        apt-get download "$pkg:${ARCH}" 2>/dev/null \
            || echo "    (skipped — not available; install.sh will fall back to PyPI/source)"
    done

    # Resolve transitive deps. apt-get satisfy ... --print-uris is the
    # cleanest way; collect URIs, fetch via curl. apt-get --download-only
    # would require live apt sources.
    apt-cache depends --recurse --no-recommends --no-suggests \
        --no-conflicts --no-breaks --no-replaces --no-enhances \
        "${DEBS[@]}" 2>/dev/null | grep -E '^\w' | sort -u \
        | xargs -I {} apt-get download "{}:${ARCH}" 2>/dev/null || true
)

# ─── Build Python wheelhouse ──────────────────────────────────────

echo "[3/5] Building Python wheelhouse..."
install -d "${BUNDLE}/packaging/wheelhouse"
(
    cd "${REPO_ROOT}"
    # Export pinned deps from uv.lock (no dev extras) as a requirements
    # file, then download pre-built binary wheels for all of them.
    # `uv pip download` prefers binary wheels; falls back to sdist only
    # when no wheel exists on PyPI (unlikely for our deps).
    uv export --no-dev --format requirements-txt > "${STAGE}/requirements.txt"
    uv pip download \
        --dest "${BUNDLE}/packaging/wheelhouse" \
        -r "${STAGE}/requirements.txt"
    # Build the project itself as a wheel so install.sh can
    # `pip install hermes-*.whl` offline without the full source tree.
    uv build \
        --wheel \
        --out-dir "${BUNDLE}/packaging/wheelhouse"
)

# ─── Pre-build the UI ─────────────────────────────────────────────

echo "[4/5] Building UI..."
(
    cd "${REPO_ROOT}/ui"
    pnpm install --frozen-lockfile
    pnpm build
)
# Copy the ui/build/ output INTO the bundle so install.sh skips its
# `pnpm build` step on the install host.
mkdir -p "${BUNDLE}/ui/build"
cp -a "${REPO_ROOT}/ui/build/." "${BUNDLE}/ui/build/"

# ─── Tar it up ────────────────────────────────────────────────────

echo "[5/5] Creating ${OUT}..."
tar -czf "${OUT}" -C "${STAGE}" hermes
SIZE_MB=$(du -m "${OUT}" | cut -f1)

cat <<EOF

✓ Offline bundle built: ${OUT} (${SIZE_MB} MB)

To install on a fresh Linux box (no internet needed):
  tar -xzf ${OUT}
  cd hermes
  sudo ./packaging/install.sh --offline

EOF
