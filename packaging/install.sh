#!/usr/bin/env bash
#
# HERMES — one-shot installer for fresh Linux systems.
#
# Designed to bring up a complete production-style HERMES deployment
# on a never-before-touched Linux machine with a single command:
#
#     sudo ./install.sh
#
# What it does (in order):
#
#   1. Detects the distro family (deb/rpm/arch/alpine).
#   2. Installs system dependencies via the right package manager:
#        - postgresql-16 + timescaledb (TimescaleDB community edition)
#        - mosquitto (MQTT broker)
#        - nginx (reverse proxy)
#        - python3.11 + pip + venv
#        - nodejs + npm/pnpm (only for first-time UI build; not needed
#          if you ship a tarball with ui/build/ pre-built)
#   3. Creates a `hermes` system user + `/opt/hermes/` install root.
#   4. Creates a Postgres role + database with TimescaleDB extension.
#   5. Generates a fresh JWT secret, writes /etc/hermes/secrets.env
#      with mode 0640 (root:hermes).
#   6. Copies the source tree to /opt/hermes/, creates a Python venv,
#      installs Python deps from the bundled wheels (or PyPI fallback),
#      builds the UI (or copies pre-built ui/build/).
#   7. Runs all SQL migrations.
#   8. Installs systemd units from packaging/systemd/.
#   9. Installs the nginx site config + reloads.
#  10. Bootstraps an allowed_emails.txt with a default operator address
#      (configurable via --operator-email).
#  11. Enables + starts hermes-api.service + hermes-ingest.service.
#  12. Prints how to log in.
#
# Idempotent: re-running the same install upgrades in place. To remove,
# run packaging/uninstall.sh.
#
# Tested on:
#   - Debian 12 (bookworm), Ubuntu 24.04 (noble) — primary targets
#   - Fedora 40, Rocky/Alma 9 — best-effort
#   - Arch / Alpine — best-effort, manual fallback if package names differ
#
# What this script does NOT do:
#   - Configure TLS certificates. Use `certbot --nginx` after install,
#     or replace the nginx site config under /etc/nginx/sites-available.
#   - Set up backup. See docs/operations/BACKUP_RESTORE.md (planned).
#   - Tune Postgres for your hardware. Defaults are fine for a Pi 4.
#
# Exit codes:
#   0 — success
#   1 — generic failure (something logged before exit)
#   2 — unsupported distro
#   3 — must run as root
#   4 — required tool missing after install attempt

set -euo pipefail
IFS=$'\n\t'

# ─── Configuration ────────────────────────────────────────────────

INSTALL_ROOT="${HERMES_INSTALL_ROOT:-/opt/hermes}"
ETC_DIR="${HERMES_ETC_DIR:-/etc/hermes}"
LOG_DIR="${HERMES_LOG_DIR:-/var/log/hermes}"
DATA_DIR="${HERMES_DATA_DIR:-/var/lib/hermes}"
HERMES_USER="${HERMES_USER:-hermes}"
HERMES_GROUP="${HERMES_GROUP:-hermes}"

DB_NAME="${HERMES_DB_NAME:-hermes}"
DB_APP_USER="${HERMES_DB_APP_USER:-hermes_app}"
DB_MIGRATE_USER="${HERMES_DB_MIGRATE_USER:-hermes_migrate}"

# Operator email gets pre-allowlisted so the OTP flow can issue codes
# to it on the very first login. Override via --operator-email or the
# HERMES_OPERATOR_EMAIL env var.
OPERATOR_EMAIL="${HERMES_OPERATOR_EMAIL:-operator@example.com}"

# Repo root — directory containing this install.sh, two levels up if
# we're inside packaging/. Detected once.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Use plain log levels — no journalctl tagging here, this script runs
# before systemd is set up.
log() { printf '\033[1;34m[hermes]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[hermes WARN]\033[0m %s\n' "$*" >&2; }
err() { printf '\033[1;31m[hermes ERROR]\033[0m %s\n' "$*" >&2; }
die() { err "$@"; exit 1; }

# ─── CLI ──────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: sudo ./install.sh [OPTIONS]

Options:
  --operator-email EMAIL   Email to pre-allowlist for OTP login (default: operator@example.com)
  --offline                Use bundled .debs from packaging/offline/ instead of apt fetching
                           (only honoured on deb-based distros; see Path C in INSTALLATION.md)
  --skip-ui                Don't build/install the SvelteKit UI (API-only deployment)
  --skip-nginx             Don't install or configure nginx (you're providing your own reverse proxy)
  --help, -h               Show this help

Environment variable overrides:
  HERMES_INSTALL_ROOT      (default /opt/hermes)
  HERMES_ETC_DIR           (default /etc/hermes)
  HERMES_LOG_DIR           (default /var/log/hermes)
  HERMES_DATA_DIR          (default /var/lib/hermes)
  HERMES_USER              (default hermes)
  HERMES_DB_NAME           (default hermes)
  HERMES_DB_APP_USER       (default hermes_app)
  HERMES_DB_MIGRATE_USER   (default hermes_migrate)
EOF
}

USE_OFFLINE=0
SKIP_UI=0
SKIP_NGINX=0

while [ $# -gt 0 ]; do
    case "$1" in
        --operator-email) OPERATOR_EMAIL="$2"; shift 2 ;;
        --offline) USE_OFFLINE=1; shift ;;
        --skip-ui) SKIP_UI=1; shift ;;
        --skip-nginx) SKIP_NGINX=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage; die "Unknown option: $1" ;;
    esac
done

# ─── Pre-flight ───────────────────────────────────────────────────

if [ "$EUID" -ne 0 ]; then
    err "Must run as root (sudo ./install.sh)"
    exit 3
fi

# Detect distro family via os-release. $ID is the canonical name
# (debian, ubuntu, fedora, rhel, rocky, arch, alpine, etc).
if [ ! -r /etc/os-release ]; then
    err "/etc/os-release missing — can't detect distro"
    exit 2
fi
# shellcheck disable=SC1091
. /etc/os-release

DISTRO_FAMILY="unknown"
case "${ID:-}${ID_LIKE:-}" in
    *debian*|*ubuntu*) DISTRO_FAMILY="deb" ;;
    *fedora*|*rhel*|*rocky*|*alma*|*centos*) DISTRO_FAMILY="rpm" ;;
    *arch*|*manjaro*) DISTRO_FAMILY="arch" ;;
    *alpine*) DISTRO_FAMILY="alpine" ;;
esac

if [ "$DISTRO_FAMILY" = "unknown" ]; then
    err "Unsupported distro family. Detected ID=${ID:-?} ID_LIKE=${ID_LIKE:-?}"
    err "Supported: Debian, Ubuntu, Fedora, RHEL, Rocky, Alma, Arch, Alpine"
    err "For other distros, install dependencies manually and re-run with HERMES_SKIP_DEPS=1"
    exit 2
fi

log "Detected distro family: ${DISTRO_FAMILY} (${PRETTY_NAME:-$ID})"

# ─── Step 1 — install system dependencies ─────────────────────────

install_deps_deb() {
    log "Installing system dependencies via apt..."
    export DEBIAN_FRONTEND=noninteractive

    if [ "$USE_OFFLINE" = "1" ]; then
        local offline_dir="${REPO_ROOT}/packaging/offline"
        if [ ! -d "$offline_dir" ]; then
            die "--offline requested but ${offline_dir}/ doesn't exist. Run packaging/build-offline-bundle.sh first."
        fi
        log "Installing offline bundle from ${offline_dir}/"
        # apt installs in topological order if we let it figure out deps
        apt-get install -y --no-install-recommends "${offline_dir}"/*.deb \
            || die "Offline package install failed"
        return
    fi

    apt-get update
    # TimescaleDB needs its own apt repo. Add it if not present.
    if ! apt-key list 2>/dev/null | grep -q -i 'timescale' \
            && [ ! -f /etc/apt/keyrings/timescale.gpg ]; then
        log "Adding TimescaleDB apt repo..."
        install -d -m 0755 /etc/apt/keyrings
        # The TimescaleDB GPG key is fetched once; offline mode skips this.
        curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
            | gpg --dearmor -o /etc/apt/keyrings/timescale.gpg
        echo "deb [signed-by=/etc/apt/keyrings/timescale.gpg] https://packagecloud.io/timescale/timescaledb/$( . /etc/os-release; echo "${ID}" )/ $( . /etc/os-release; echo "${VERSION_CODENAME}" ) main" \
            > /etc/apt/sources.list.d/timescaledb.list
        apt-get update
    fi

    local pkgs=(
        postgresql-16
        timescaledb-2-postgresql-16
        timescaledb-tools
        mosquitto
        mosquitto-clients
        python3.11
        python3.11-venv
        python3-pip
        ca-certificates
        curl
        gnupg
    )
    [ "$SKIP_NGINX" = "0" ] && pkgs+=(nginx)
    [ "$SKIP_UI" = "0" ] && [ ! -d "${REPO_ROOT}/ui/build" ] && pkgs+=(nodejs npm)

    apt-get install -y --no-install-recommends "${pkgs[@]}" \
        || die "apt install failed"

    # TimescaleDB tune is recommended for Pi 4-class boxes but not required.
    if command -v timescaledb-tune >/dev/null 2>&1; then
        log "Running timescaledb-tune (non-interactive)..."
        timescaledb-tune --quiet --yes --conf-path /etc/postgresql/16/main/postgresql.conf \
            || warn "timescaledb-tune returned non-zero; continuing with defaults"
        systemctl restart postgresql
    fi
}

install_deps_rpm() {
    log "Installing system dependencies via dnf..."
    local pkg_mgr=dnf
    command -v dnf >/dev/null 2>&1 || pkg_mgr=yum

    # Add TimescaleDB repo
    if [ ! -f /etc/yum.repos.d/timescale_timescaledb.repo ]; then
        log "Adding TimescaleDB yum repo..."
        cat > /etc/yum.repos.d/timescale_timescaledb.repo <<'EOF'
[timescale_timescaledb]
name=timescale_timescaledb
baseurl=https://packagecloud.io/timescale/timescaledb/el/$releasever/$basearch
repo_gpgcheck=1
gpgcheck=0
enabled=1
gpgkey=https://packagecloud.io/timescale/timescaledb/gpgkey
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
metadata_expire=300
EOF
    fi

    local pkgs=(
        postgresql16-server
        postgresql16-contrib
        timescaledb-2-postgresql-16
        mosquitto
        python3.11
        python3-pip
        ca-certificates
        curl
        gnupg2
    )
    [ "$SKIP_NGINX" = "0" ] && pkgs+=(nginx)
    [ "$SKIP_UI" = "0" ] && [ ! -d "${REPO_ROOT}/ui/build" ] && pkgs+=(nodejs npm)

    "$pkg_mgr" install -y "${pkgs[@]}" || die "$pkg_mgr install failed"

    # Initialize Postgres on first install (RHEL family doesn't auto-init)
    if [ ! -d /var/lib/pgsql/16/data/base ]; then
        /usr/pgsql-16/bin/postgresql-16-setup initdb || die "initdb failed"
    fi
    systemctl enable --now postgresql-16
}

install_deps_arch() {
    log "Installing system dependencies via pacman..."
    pacman -Sy --noconfirm --needed \
        postgresql \
        timescaledb \
        mosquitto \
        python python-pip \
        nginx \
        nodejs npm \
        ca-certificates curl gnupg \
        || die "pacman install failed"

    if [ ! -d /var/lib/postgres/data/base ]; then
        sudo -u postgres initdb -D /var/lib/postgres/data \
            || die "initdb failed"
    fi
    systemctl enable --now postgresql
}

install_deps_alpine() {
    log "Installing system dependencies via apk..."
    apk add --no-cache \
        postgresql16 \
        timescaledb \
        mosquitto \
        python3 py3-pip \
        nginx \
        nodejs npm \
        ca-certificates curl gnupg \
        || die "apk install failed"

    if [ ! -d /var/lib/postgresql/16/data/base ]; then
        sudo -u postgres initdb -D /var/lib/postgresql/16/data \
            || die "initdb failed"
    fi
    rc-service postgresql start
    rc-update add postgresql
}

if [ "${HERMES_SKIP_DEPS:-0}" = "1" ]; then
    log "HERMES_SKIP_DEPS=1 — assuming caller has already installed system deps"
    log "  (this path is used by debian/postinst after dpkg has resolved Depends:)"
else
    case "$DISTRO_FAMILY" in
        deb) install_deps_deb ;;
        rpm) install_deps_rpm ;;
        arch) install_deps_arch ;;
        alpine) install_deps_alpine ;;
    esac
fi

# Verify the tools we now expect
for tool in psql mosquitto python3 systemctl; do
    command -v "$tool" >/dev/null 2>&1 \
        || die "Required tool missing after install: $tool"
done

# ─── Step 2 — system user + filesystem layout ─────────────────────

log "Setting up system user, install root, etc..."

if ! getent group "$HERMES_GROUP" >/dev/null 2>&1; then
    groupadd --system "$HERMES_GROUP"
fi
if ! id "$HERMES_USER" >/dev/null 2>&1; then
    useradd --system --gid "$HERMES_GROUP" \
        --home-dir "$INSTALL_ROOT" --shell /usr/sbin/nologin \
        --comment "HERMES sensor dashboard" \
        "$HERMES_USER"
fi

install -d -m 0755 -o "$HERMES_USER" -g "$HERMES_GROUP" \
    "$INSTALL_ROOT" "$LOG_DIR" "$DATA_DIR"
install -d -m 0750 -o root -g "$HERMES_GROUP" "$ETC_DIR"

# ─── Step 3 — Postgres role + database ────────────────────────────

log "Configuring Postgres roles + database..."

# Make sure postgresql is started — distros differ in service name.
PG_SERVICE=postgresql
case "$DISTRO_FAMILY" in
    rpm) PG_SERVICE=postgresql-16 ;;
esac
systemctl enable "$PG_SERVICE" 2>/dev/null || true
systemctl start "$PG_SERVICE" || die "Failed to start $PG_SERVICE"

# Wait briefly for Postgres to accept connections
for i in 1 2 3 4 5 6 7 8 9 10; do
    sudo -u postgres pg_isready >/dev/null 2>&1 && break
    sleep 1
done

DB_APP_PW="${HERMES_DB_APP_PW:-$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)}"
DB_MIGRATE_PW="${HERMES_DB_MIGRATE_PW:-$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)}"

# Create roles (idempotent — ALTER if exists)
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_MIGRATE_USER}') THEN
        CREATE ROLE ${DB_MIGRATE_USER} LOGIN PASSWORD '${DB_MIGRATE_PW}' CREATEDB;
    ELSE
        ALTER ROLE ${DB_MIGRATE_USER} WITH PASSWORD '${DB_MIGRATE_PW}';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_APP_USER}') THEN
        CREATE ROLE ${DB_APP_USER} LOGIN PASSWORD '${DB_APP_PW}';
    ELSE
        ALTER ROLE ${DB_APP_USER} WITH PASSWORD '${DB_APP_PW}';
    END IF;
END
\$\$;
SQL

# Create database if missing (CREATE DATABASE can't run inside a tx)
sudo -u postgres psql -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" \
    | grep -q 1 \
    || sudo -u postgres createdb -O "$DB_MIGRATE_USER" "$DB_NAME"

# Grant the app user read/write on data tables (DDL stays with migrate)
sudo -u postgres psql -d "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL
GRANT CONNECT ON DATABASE ${DB_NAME} TO ${DB_APP_USER};
GRANT USAGE ON SCHEMA public TO ${DB_APP_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${DB_APP_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO ${DB_APP_USER};
SQL

log "Postgres roles + database ready (${DB_NAME})"

# ─── Step 4 — generate secrets ────────────────────────────────────

JWT_SECRET="${HERMES_JWT_SECRET:-$(openssl rand -base64 48 | tr -d '\n')}"

cat > "${ETC_DIR}/secrets.env" <<EOF
# /etc/hermes/secrets.env — generated by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ).
# Owner: root:hermes  Mode: 0640
# Contains the database URLs + JWT secret. Do NOT check into git.

DATABASE_URL=postgresql+asyncpg://${DB_APP_USER}:${DB_APP_PW}@localhost:5432/${DB_NAME}
MIGRATE_DATABASE_URL=postgresql://${DB_MIGRATE_USER}:${DB_MIGRATE_PW}@localhost:5432/${DB_NAME}
HERMES_JWT_SECRET=${JWT_SECRET}
EOF
chown root:"$HERMES_GROUP" "${ETC_DIR}/secrets.env"
chmod 0640 "${ETC_DIR}/secrets.env"

# Non-secret runtime config
cat > "${ETC_DIR}/api.env" <<EOF
# /etc/hermes/api.env — operator-tunable API settings
HERMES_API_HOST=127.0.0.1
HERMES_API_PORT=8080
HERMES_API_LOG_LEVEL=info
HERMES_LOG_FORMAT=json
HERMES_INGEST_MODE=all
HERMES_JWT_EXPIRY_SECONDS=3600
ALLOWED_EMAILS_PATH=${ETC_DIR}/allowed_emails.txt
SMTP_HOST=
SMTP_USER=
SMTP_FROM=
EOF
chmod 0644 "${ETC_DIR}/api.env"

cat > "${ETC_DIR}/ingest.env" <<EOF
# /etc/hermes/ingest.env — operator-tunable ingest settings
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_TOPIC_ADC=stm32/adc
MQTT_TOPIC_EVENTS_PREFIX=stm32/events
EVENT_TTL_SECONDS=5.0
LIVE_BUFFER_MAX_SAMPLES=2000
MQTT_DRIFT_THRESHOLD_S=5.0
HERMES_LOG_FORMAT=json
HERMES_INGEST_MODE=all
EOF
chmod 0644 "${ETC_DIR}/ingest.env"

# Allowlist (the operator email gets a starting entry)
if [ ! -f "${ETC_DIR}/allowed_emails.txt" ]; then
    cat > "${ETC_DIR}/allowed_emails.txt" <<EOF
# Pre-allowed operator emails. One per line. Comments OK.
${OPERATOR_EMAIL}
EOF
    chown root:"$HERMES_GROUP" "${ETC_DIR}/allowed_emails.txt"
    chmod 0640 "${ETC_DIR}/allowed_emails.txt"
fi

log "Wrote ${ETC_DIR}/{secrets.env,api.env,ingest.env,allowed_emails.txt}"

# ─── Step 5 — copy source + venv + UI build ───────────────────────

log "Installing application to ${INSTALL_ROOT}/..."

# rsync if available, fallback to cp -a
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude='.git' --exclude='.venv' --exclude='node_modules' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='.svelte-kit' \
        --exclude='ui/.svelte-kit' \
        "${REPO_ROOT}/" "${INSTALL_ROOT}/"
else
    rm -rf "${INSTALL_ROOT}/services" "${INSTALL_ROOT}/ui" "${INSTALL_ROOT}/migrations" "${INSTALL_ROOT}/scripts" "${INSTALL_ROOT}/packaging" "${INSTALL_ROOT}/pyproject.toml" "${INSTALL_ROOT}/uv.lock" "${INSTALL_ROOT}/README.md"
    cp -a "${REPO_ROOT}/services" "${REPO_ROOT}/ui" "${REPO_ROOT}/migrations" \
          "${REPO_ROOT}/scripts" "${REPO_ROOT}/packaging" \
          "${REPO_ROOT}/pyproject.toml" "${REPO_ROOT}/uv.lock" \
          "${REPO_ROOT}/README.md" "${INSTALL_ROOT}/"
fi

chown -R "$HERMES_USER":"$HERMES_GROUP" "$INSTALL_ROOT"

# Python venv — built per-host because wheels can be platform-specific
log "Creating Python venv at ${INSTALL_ROOT}/.venv ..."
sudo -u "$HERMES_USER" python3 -m venv "${INSTALL_ROOT}/.venv"

# Install Python deps. If we have a vendored wheelhouse (offline mode),
# use it; otherwise fall back to PyPI.
WHEELHOUSE="${REPO_ROOT}/packaging/wheelhouse"
if [ "$USE_OFFLINE" = "1" ] && [ -d "$WHEELHOUSE" ]; then
    log "Installing Python deps from offline wheelhouse..."
    sudo -u "$HERMES_USER" "${INSTALL_ROOT}/.venv/bin/pip" install \
        --no-index --find-links "$WHEELHOUSE" \
        "${INSTALL_ROOT}" \
        || die "Offline pip install failed"
else
    log "Installing Python deps from PyPI..."
    sudo -u "$HERMES_USER" "${INSTALL_ROOT}/.venv/bin/pip" install \
        --upgrade pip
    sudo -u "$HERMES_USER" "${INSTALL_ROOT}/.venv/bin/pip" install \
        -e "${INSTALL_ROOT}" \
        || die "pip install failed"
fi

# Build the UI unless --skip-ui or pre-built bundle exists
if [ "$SKIP_UI" = "0" ]; then
    if [ -d "${INSTALL_ROOT}/ui/build" ]; then
        log "Pre-built ui/build/ found; skipping pnpm build"
    else
        log "Building UI..."
        if command -v pnpm >/dev/null 2>&1; then
            (cd "${INSTALL_ROOT}/ui" && sudo -u "$HERMES_USER" pnpm install --frozen-lockfile && sudo -u "$HERMES_USER" pnpm build) \
                || warn "UI build failed; API still usable without UI"
        elif command -v npm >/dev/null 2>&1; then
            (cd "${INSTALL_ROOT}/ui" && sudo -u "$HERMES_USER" npm install && sudo -u "$HERMES_USER" npm run build) \
                || warn "UI build failed; API still usable without UI"
        else
            warn "Neither pnpm nor npm found; UI not built. Skip with --skip-ui to silence."
        fi
    fi
fi

# ─── Step 6 — apply migrations ────────────────────────────────────

log "Applying database migrations..."
# Source the secrets so MIGRATE_DATABASE_URL is set, then run.
set -a
# shellcheck disable=SC1091
. "${ETC_DIR}/secrets.env"
set +a
"${INSTALL_ROOT}/scripts/db-migrate.sh" \
    || die "Migrations failed; investigate ${ETC_DIR}/secrets.env credentials"

# ─── Step 7 — install systemd units ───────────────────────────────

log "Installing systemd units..."
install -m 0644 -t /etc/systemd/system \
    "${INSTALL_ROOT}/packaging/systemd/hermes-api.service" \
    "${INSTALL_ROOT}/packaging/systemd/hermes-ingest.service" \
    "${INSTALL_ROOT}/packaging/systemd/hermes-ingest@.service" \
    "${INSTALL_ROOT}/packaging/systemd/hermes.target"

systemctl daemon-reload

# ─── Step 8 — nginx site (optional) ───────────────────────────────

if [ "$SKIP_NGINX" = "0" ]; then
    if [ -f "${INSTALL_ROOT}/packaging/nginx/hermes.conf" ]; then
        log "Installing nginx site config..."
        # Standard Debian/Ubuntu layout
        if [ -d /etc/nginx/sites-available ]; then
            install -m 0644 "${INSTALL_ROOT}/packaging/nginx/hermes.conf" \
                /etc/nginx/sites-available/hermes
            ln -sf /etc/nginx/sites-available/hermes /etc/nginx/sites-enabled/hermes
        else
            # RHEL/Alpine/Arch — drop into conf.d
            install -m 0644 "${INSTALL_ROOT}/packaging/nginx/hermes.conf" \
                /etc/nginx/conf.d/hermes.conf
        fi
        nginx -t && systemctl reload nginx \
            || warn "nginx reload failed; check /etc/nginx config"
    fi
fi

# ─── Step 9 — start services ──────────────────────────────────────

log "Enabling + starting hermes services..."
systemctl enable hermes-api.service hermes-ingest.service hermes.target

# Mosquitto + Postgres should already be enabled by their packages,
# but be defensive.
systemctl enable mosquitto.service 2>/dev/null || true

systemctl start hermes-api.service hermes-ingest.service \
    || die "Failed to start hermes services. Check: journalctl -u hermes-api -u hermes-ingest -n 50"

# ─── Done ─────────────────────────────────────────────────────────

ACCESS_URL="http://$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "${ACCESS_URL#http://}" ] && ACCESS_URL="http://localhost"

printf '\n\033[1;32m✓\033[0m HERMES installed.\n\n'
cat <<EOF
  Install root:   ${INSTALL_ROOT}
  Config:         ${ETC_DIR}/{secrets.env,api.env,ingest.env,allowed_emails.txt}
  Logs:           journalctl -u hermes-api -u hermes-ingest -f
  Services:       systemctl status hermes-api hermes-ingest

  Operator email: ${OPERATOR_EMAIL}
  Access URL:     ${ACCESS_URL}/
  API:            ${ACCESS_URL}:8080/api/health

To set up email-based OTP login, edit ${ETC_DIR}/api.env and set
SMTP_HOST + SMTP_USER + add SMTP_PASS to ${ETC_DIR}/secrets.env;
then \`systemctl restart hermes-api\`.

To set up TLS, run \`certbot --nginx\` after pointing a DNS record
at this host. The nginx site config ships HTTP-only by default.

To upgrade in place: re-run this script with the new source tree.
To uninstall: run packaging/uninstall.sh.

EOF
