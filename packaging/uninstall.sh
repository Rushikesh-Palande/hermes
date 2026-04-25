#!/usr/bin/env bash
#
# HERMES — clean uninstall.
#
# Removes services, systemd units, the install root, the system user,
# the nginx site, the etc/hermes directory, and (optionally) the
# Postgres database + role.
#
# Does NOT remove system packages (postgres, mosquitto, nginx) — those
# may be in use by other services on the host.
#
# Usage:
#     sudo ./uninstall.sh                 # keep DB
#     sudo ./uninstall.sh --drop-database # drop DB + roles too
#     sudo ./uninstall.sh --keep-config   # keep /etc/hermes/* (re-install reuses)

set -euo pipefail
IFS=$'\n\t'

INSTALL_ROOT="${HERMES_INSTALL_ROOT:-/opt/hermes}"
ETC_DIR="${HERMES_ETC_DIR:-/etc/hermes}"
LOG_DIR="${HERMES_LOG_DIR:-/var/log/hermes}"
DATA_DIR="${HERMES_DATA_DIR:-/var/lib/hermes}"
HERMES_USER="${HERMES_USER:-hermes}"
HERMES_GROUP="${HERMES_GROUP:-hermes}"
DB_NAME="${HERMES_DB_NAME:-hermes}"
DB_APP_USER="${HERMES_DB_APP_USER:-hermes_app}"
DB_MIGRATE_USER="${HERMES_DB_MIGRATE_USER:-hermes_migrate}"

DROP_DB=0
KEEP_CONFIG=0

log() { printf '\033[1;34m[hermes]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[hermes WARN]\033[0m %s\n' "$*" >&2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --drop-database) DROP_DB=1; shift ;;
        --keep-config) KEEP_CONFIG=1; shift ;;
        --help|-h)
            sed -n '3,/^$/p' "$0" | sed 's/^# *//'
            exit 0
            ;;
        *) warn "Unknown option: $1"; shift ;;
    esac
done

if [ "$EUID" -ne 0 ]; then
    echo "Must run as root (sudo ./uninstall.sh)" >&2
    exit 1
fi

# Stop + disable services
log "Stopping services..."
for unit in hermes-api hermes-ingest 'hermes-ingest@*'; do
    systemctl stop "${unit}.service" 2>/dev/null || true
    systemctl disable "${unit}.service" 2>/dev/null || true
done
systemctl disable hermes.target 2>/dev/null || true

log "Removing systemd units..."
rm -f /etc/systemd/system/hermes-api.service \
      /etc/systemd/system/hermes-ingest.service \
      /etc/systemd/system/hermes-ingest@.service \
      /etc/systemd/system/hermes.target
systemctl daemon-reload

# nginx site
log "Removing nginx site..."
rm -f /etc/nginx/sites-enabled/hermes \
      /etc/nginx/sites-available/hermes \
      /etc/nginx/conf.d/hermes.conf
if command -v nginx >/dev/null 2>&1; then
    nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
fi

# Install root
log "Removing ${INSTALL_ROOT}..."
rm -rf "$INSTALL_ROOT" "$LOG_DIR" "$DATA_DIR"

# Config
if [ "$KEEP_CONFIG" = "0" ]; then
    log "Removing ${ETC_DIR}..."
    rm -rf "$ETC_DIR"
else
    warn "Keeping ${ETC_DIR}/ per --keep-config"
fi

# DB
if [ "$DROP_DB" = "1" ]; then
    log "Dropping database + roles..."
    sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL || warn "DB drop had errors"
DROP DATABASE IF EXISTS ${DB_NAME};
DROP ROLE IF EXISTS ${DB_APP_USER};
DROP ROLE IF EXISTS ${DB_MIGRATE_USER};
SQL
fi

# System user
if id "$HERMES_USER" >/dev/null 2>&1; then
    log "Removing system user ${HERMES_USER}..."
    userdel "$HERMES_USER" 2>/dev/null || true
fi
if getent group "$HERMES_GROUP" >/dev/null 2>&1; then
    groupdel "$HERMES_GROUP" 2>/dev/null || true
fi

printf '\n\033[1;32m✓\033[0m HERMES uninstalled.\n\n'
cat <<EOF
System packages (postgresql, mosquitto, nginx) were NOT removed.
Remove them manually if desired:
  apt remove postgresql-16 timescaledb-2-postgresql-16 mosquitto nginx

EOF
