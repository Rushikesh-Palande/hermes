# INSTALLATION.md — getting HERMES on a Linux box

> **Audience:** anyone deploying HERMES from source. Covers the three
> shipping paths (one-shot installer, container, offline bundle) plus
> what each does internally so you can debug a failed install.
>
> **Companion docs:**
> - [`../guides/CONFIGURATION.md`](../guides/CONFIGURATION.md) — every env var that survives the install
> - [`../guides/DEVELOPMENT.md`](../guides/DEVELOPMENT.md) — the dev-machine path, NOT this one
> - [`../design/MULTI_SHARD.md`](../design/MULTI_SHARD.md) — how to scale past one process after install

---

## Pick your path

```
                      Do you have apt or dnf?
                              │
            ┌─────────────────┼─────────────────┐
           yes               yes              no
            │                 │                 │
       Internet?       I want a container?   Air-gapped?
            │                 │                 │
        ┌───┴───┐              │                 │
        ▼       ▼              ▼                 ▼
    PATH A   PATH C        PATH B           PATH C
    install. tarball       Docker           tarball
    sh       (offline)     compose          (offline)
```

| Scenario | Path | What you need on the host before starting |
|----------|------|-------------------------------------------|
| Pi 4 / EC2 with internet, want it on the host | **A** | `bash`, `sudo`, internet to apt sources |
| Cloud / dev / "I have Docker already" | **B** | Docker (or Podman) + Docker Compose v2 |
| Air-gapped factory floor, brand-new Debian | **C** | `bash`, `sudo`, the offline bundle on a USB stick |
| Brand-new RHEL / Fedora / Rocky | **A** | `bash`, `sudo`, internet to dnf repos |
| Brand-new Arch / Alpine | **A** (best-effort) | `bash`, `sudo`, internet |

Note: **HERMES is not a desktop app**. It's a multi-process service
stack (Postgres + MQTT + Python ingest + Python API + nginx) that
cannot meaningfully run "as a single binary". Every path below
installs all of it.

---

## Path A — one-shot installer (`install.sh`)

The headline path. Detects distro family, installs system
dependencies via the right package manager, sets up the database,
generates secrets, installs systemd units, starts the services.

### Quickstart

```bash
git clone https://github.com/Rushikesh-Palande/hermes.git /tmp/hermes
cd /tmp/hermes
sudo ./packaging/install.sh --operator-email you@your-org.com
```

That's it. After ~5 minutes you have:
- HERMES API on `http://<host>:8080/`
- nginx reverse proxy on `http://<host>/`
- `hermes-api.service` + `hermes-ingest.service` enabled in systemd
- `/etc/hermes/secrets.env` with a generated JWT secret
- An operator email pre-allowlisted for OTP login

### What it does, step by step

```
┌──────────────────────────────────────────────────────────────────────┐
│ 1. Detect distro: deb / rpm / arch / alpine                          │
│ 2. Install system deps via apt / dnf / pacman / apk:                 │
│      postgresql-16, timescaledb, mosquitto, nginx, python3.11        │
│ 3. (deb only) Add TimescaleDB apt repo if missing                    │
│ 4. (rpm only) Run postgresql-setup initdb                            │
│ 5. Create system user `hermes` (gid hermes)                          │
│ 6. Create directories /opt/hermes, /etc/hermes, /var/log/hermes,     │
│    /var/lib/hermes (chown hermes:hermes)                             │
│ 7. Create Postgres roles `hermes_migrate` (DDL) + `hermes_app` (CRUD)│
│ 8. Create database `hermes`                                          │
│ 9. Grant default-privs on public schema to hermes_app                │
│ 10. Generate JWT secret + write /etc/hermes/secrets.env (mode 0640)  │
│ 11. Write /etc/hermes/api.env + ingest.env + allowed_emails.txt      │
│ 12. rsync repo source to /opt/hermes/                                │
│ 13. Create Python venv at /opt/hermes/.venv                          │
│ 14. pip install -e /opt/hermes (entry-point scripts hermes-api,      │
│     hermes-ingest become available)                                  │
│ 15. Build SvelteKit UI (skipped if ui/build/ already exists)         │
│ 16. Run ./scripts/db-migrate.sh                                      │
│ 17. Install systemd units to /etc/systemd/system/                    │
│ 18. Install nginx site to /etc/nginx/sites-available/hermes          │
│ 19. systemctl enable + start hermes-api + hermes-ingest              │
└──────────────────────────────────────────────────────────────────────┘
```

### Flags

| Flag | What |
|------|------|
| `--operator-email EMAIL` | Pre-allowlist this email for OTP login. Default `operator@example.com` |
| `--offline` | Use bundled `.deb`s from `packaging/offline/` instead of fetching via apt. Requires Path C bundle |
| `--skip-ui` | Don't build / install the UI (API-only deployment) |
| `--skip-nginx` | Don't install nginx (you're providing your own reverse proxy) |
| `--help`, `-h` | Show usage |

Plus environment variable overrides for paths and DB names — see
`install.sh --help`.

### Re-running

`install.sh` is **idempotent**. Re-running upgrades in place:
- `rsync` overwrites the source tree at `/opt/hermes/`
- `pip install -e .` refreshes the venv (cached wheels reused)
- Migrations re-apply (every migration is `IF NOT EXISTS`-guarded)
- Existing `secrets.env` / `api.env` / `ingest.env` are NOT overwritten
- Services restart at the end

To do a clean reinstall: `sudo ./packaging/uninstall.sh --drop-database`,
then re-run `install.sh`.

### Distro support matrix

| Distro | Status |
|--------|--------|
| Debian 12 (bookworm) | **primary** — tested in CI |
| Debian 11 (bullseye) | works (Postgres 14 instead of 16; minor) |
| Ubuntu 24.04 (noble) | **primary** — tested |
| Ubuntu 22.04 (jammy) | works |
| Fedora 40+ | best-effort — install + SELinux contexts may need manual tweaks |
| Rocky/Alma 9 | best-effort |
| Arch Linux | best-effort — package names differ; install.sh handles common cases |
| Alpine | best-effort — uses `apk` and `rc-service` instead of systemd |

If your distro isn't supported, install dependencies manually (the
list is in `install.sh:install_deps_<family>`) and re-run with
`HERMES_SKIP_DEPS=1`.

### Removing

```bash
sudo ./packaging/uninstall.sh                  # keep DB + config
sudo ./packaging/uninstall.sh --keep-config    # also keep /etc/hermes
sudo ./packaging/uninstall.sh --drop-database  # drop DB + roles too
```

System packages (`postgresql-16`, `mosquitto`, `nginx`) are NOT
removed — they may be used by other services on the host.

---

## Path B — Docker / Podman container

Truly any-Linux. Requires Docker (or Podman with Docker compatibility).

### Quickstart

```bash
git clone https://github.com/Rushikesh-Palande/hermes.git
cd hermes

# Generate a JWT secret + Postgres password
cat > .env <<EOF
HERMES_JWT_SECRET=$(openssl rand -base64 48 | tr -d '\n')
POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '\n/')
EOF

# Build + run
docker compose -f packaging/docker-compose.prod.yml up -d
```

After `up -d` finishes:
- API on `http://<host>:8080/`
- MQTT broker on `tcp://<host>:1883`
- Postgres on `tcp://<host>:5432` (only inside the compose network by default)

### What gets built

The Dockerfile is a three-stage build:

```
┌─ Stage 1: ui-builder ──────────┐
│ node:20-alpine                  │
│ pnpm install + pnpm build      │
│ produces /build/ui/build       │
└────────────┬───────────────────┘
             │
┌─ Stage 2: py-builder ──────────┐
│ python:3.11-slim                │
│ uv venv + uv sync (no-dev)     │
│ produces /build/.venv          │
└────────────┬───────────────────┘
             │
┌─ Stage 3: runtime ─────────────┐
│ python:3.11-slim + tini + curl │
│ COPY .venv from py-builder     │
│ COPY ui/build from ui-builder  │
│ HEALTHCHECK /api/health        │
│ CMD ["hermes-api"]             │
└────────────────────────────────┘
```

Resulting image: ~250 MB compressed.

### Compose layout

```
hermes (compose project)
├── postgres      timescale/timescaledb:2.17.2-pg16
│                   pgdata volume
│                   healthcheck: pg_isready
├── mosquitto     eclipse-mosquitto:2.0.20
│                   port 1883:1883
├── api           hermes:local (built from packaging/Dockerfile)
│                   command: db-migrate.sh && hermes-api
│                   port 8080:8080
│                   HERMES_INGEST_MODE=live_only
└── ingest        hermes:local
                    command: hermes-ingest
                    HERMES_INGEST_MODE=all
```

The compose runs **two HERMES processes** in two containers (api +
ingest) — same architecture as the systemd-managed install. They
share the Postgres + Mosquitto containers via the compose network.

### Multi-arch

To build a multi-arch image (amd64 + arm64) for distribution:

```bash
docker buildx create --use
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -t ghcr.io/<org>/hermes:0.1.0-alpha.X \
    -f packaging/Dockerfile \
    --push .
```

Once we publish to a registry (planned for alpha.27+), the compose
file's `image: hermes:local` becomes
`image: ghcr.io/rushikesh-palande/hermes:<tag>` and the `build:`
section can be removed for end-users.

### Resource limits

For a Pi 4 or low-memory cloud VM, add resource caps to
`docker-compose.prod.yml`:

```yaml
services:
  api:
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"
  ingest:
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"
  postgres:
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: "2.0"
```

Total ~2 GB which fits a Pi 4 with 4 GB RAM (the 2 GB Pi is tight —
Path A is recommended there).

---

## Path C — offline bundle

For air-gapped deployments where the install host has zero internet
access. A pre-built tarball includes every dependency.

### Building the bundle (on a connected machine)

```bash
git clone https://github.com/Rushikesh-Palande/hermes.git
cd hermes
./packaging/build-offline-bundle.sh \
    --arch amd64 \
    --out hermes-0.1.0-alpha.X-amd64-offline.tar.gz
```

Output: a ~150 MB compressed tarball containing:

```
hermes/
├── (full source tree)
├── packaging/
│   ├── offline/             ~80 MB of .debs
│   │   ├── postgresql-16_*.deb
│   │   ├── timescaledb-2-postgresql-16_*.deb
│   │   ├── mosquitto_*.deb
│   │   ├── nginx_*.deb
│   │   └── ... (transitive deps)
│   └── wheelhouse/          ~50 MB of Python wheels
│       ├── hermes-0.1.0-py3-none-any.whl
│       ├── fastapi-*.whl
│       ├── ... (everything in uv.lock)
└── ui/build/                pre-built SvelteKit bundle
```

### Building for a different architecture

```bash
# arm64 on an amd64 host (uses qemu-user-static)
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
docker buildx build --platform linux/arm64 \
    --target offline-builder \
    -f packaging/Dockerfile.offline-builder \
    -t hermes-offline-builder:arm64 .
docker run --rm -v "$(pwd):/repo" hermes-offline-builder:arm64 \
    /repo/packaging/build-offline-bundle.sh --arch arm64 --out /repo/hermes-arm64.tar.gz
```

### Installing on the air-gapped host

Copy the tarball over (USB, SCP from a jump-host, whatever), then:

```bash
tar -xzf hermes-0.1.0-alpha.X-amd64-offline.tar.gz
cd hermes
sudo ./packaging/install.sh --offline --operator-email you@your-org.com
```

The `--offline` flag tells `install.sh` to:
- Install `.deb`s from `packaging/offline/` via `apt-get install <files>` instead of `apt-get install <names>`
- Install Python deps from `packaging/wheelhouse/` via `pip install --no-index --find-links wheelhouse/`
- Use the pre-built `ui/build/` (no `pnpm` / `npm` install)

### Updating

For an upgrade, ship a new tarball and re-run `install.sh --offline`.
Old packages get apt-upgraded by the new bundle's `.deb`s; Python
venv refreshes from the new wheelhouse.

---

## Verifying the install

After any path:

```bash
# Services should be running
sudo systemctl status hermes-api hermes-ingest    # Path A
docker compose ps                                  # Path B

# API responds
curl http://localhost:8080/api/health
# {"status": "ok", "version": "0.1.0a26"}

# Logs
sudo journalctl -u hermes-api -u hermes-ingest --since "5 minutes ago"
# Path B:
docker compose logs --since 5m api ingest

# Live-data smoke (after the firmware starts publishing, OR inject a manual frame)
mosquitto_pub -h localhost -p 1883 -t stm32/adc -m \
    '{"device_id":1,"ts":0,"adc1":[50,50,50,50,50,50],"adc2":[50,50,50,50,50,50]}'

# Then visit http://<host>/devices/1 and you should see a flat line at 50.
```

---

## Common install failures

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `apt: package timescaledb-2-postgresql-16 not found` | TimescaleDB apt repo not added | install.sh adds it; if it failed, see `/etc/apt/sources.list.d/timescaledb.list` |
| `psql: error: connection to server` | Postgres not started yet | `systemctl start postgresql`; install.sh has a 10-second retry loop |
| `permission denied` reading `/etc/hermes/secrets.env` | Wrong group | The file is `root:hermes 0640`; check with `ls -la /etc/hermes` |
| `hermes-api` won't start: "DATABASE_URL is required" | `secrets.env` not loaded | Check the systemd unit's `EnvironmentFile=` directive points at the right path |
| Bound to 0.0.0.0 but not reachable from outside | nginx not configured | install.sh runs `--skip-nginx`? Check `nginx -T` for the hermes block |
| `pip install` failed with "no compatible wheel" (offline) | Wheelhouse arch mismatch | Build the wheelhouse on the target arch via Docker buildx |
| Browser shows "404 not found" on `/` | UI not built | Re-run install.sh without `--skip-ui`, or check that `/opt/hermes/ui/build/client/index.html` exists |
| Login email never arrives | SMTP not configured | Check `journalctl -u hermes-api`; OTPs are logged when SMTP_USER is blank |

---

## What you DON'T get from any of these paths

Be explicit about scope:

- **TLS certificates.** All three paths ship HTTP-only nginx. Run
  `certbot --nginx` after pointing a DNS record at the host.
- **Backups.** No automated `pg_dump` schedule. Set one up via cron.
  See (planned) `BACKUP_RESTORE.md`.
- **Monitoring.** Prometheus is exposed at `/api/metrics` but no
  Grafana / alertmanager is shipped. See
  [`../guides/METRICS.md`](../guides/METRICS.md) §Suggested-dashboards
  for a starter set of panels.
- **High availability.** Single Postgres, single Mosquitto. For HA,
  you're rolling your own deployment topology.
- **A "brand new laptop, plug in nothing, run the file" experience.**
  As called out at the top — that requires bundling Postgres / Mosquitto
  binaries inside an AppImage, which is a separate release-engineering
  project. The compose path (B) is the closest you get with what's
  realistic to ship from one repo.

---

## Where to look next

- [`../guides/CONFIGURATION.md`](../guides/CONFIGURATION.md) — every
  env var the install populated + how to edit it later.
- [`../design/MULTI_SHARD.md`](../design/MULTI_SHARD.md) — how to flip
  this single-process install into a 4-shard topology.
- [`../guides/METRICS.md`](../guides/METRICS.md) — what to scrape
  with Prometheus.
- (Planned) `BACKUP_RESTORE.md`, `MONITORING.md`, `TROUBLESHOOTING.md`.
