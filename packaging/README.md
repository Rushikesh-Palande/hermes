# Packaging

Production deployment artefacts for HERMES.

**Current state:** placeholder. The real content lands in Phase 9.

## Planned contents

```
packaging/
├── debian/
│   ├── control               — declares package metadata + deps
│   ├── changelog             — generated from the top-level CHANGELOG.md
│   ├── rules                 — Debian build rules
│   ├── postinst              — create hermes user, /etc/hermes/secrets.env
│   ├── postrm                — remove user + systemd units on purge
│   └── copyright
├── systemd/
│   ├── hermes-api.service    — FastAPI under uvicorn, systemd-managed
│   ├── hermes-ingest.service — MQTT consumer + detection workers
│   └── hermes.target         — groups the two services
├── nginx/
│   └── hermes.conf           — TLS-terminating reverse proxy
├── logrotate/
│   └── hermes                — rotation for /var/log/hermes/*.log
└── Dockerfile                — multi-stage build for ARM64 + ARM32
```

## Why .deb and not Docker on the Pi

- **Lower memory overhead.** Docker's overlay-fs + containerd add ~150 MB
  resident memory; a Pi 4 with 2 GB RAM feels the difference.
- **systemd native.** Restart policies, journal integration, unit
  dependencies all work the standard Linux way.
- **Offline installs.** Customers on air-gapped factory networks can
  `dpkg -i hermes_X.Y.Z_arm64.deb` without a registry.

A Docker image still exists for development and cloud deployments; the
Dockerfile here builds the same artefact but is not the primary target.

## Build commands (once implemented)

```bash
make deb          # builds hermes_X.Y.Z_arm64.deb
make deb-armhf    # armhf / Pi 3 and earlier
make docker       # multi-arch Docker image
```
