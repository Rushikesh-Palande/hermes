# Packaging

Production deployment artefacts for HERMES.

## Current state

| Subdirectory          | State           | Purpose                                                          |
| --------------------- | --------------- | ---------------------------------------------------------------- |
| [`systemd/`](./systemd/) | ✅ Shipped (alpha.15) | Service units for single-process and multi-shard deployments |
| `debian/`             | ⏳ Phase 9       | `.deb` build artefacts (control, rules, postinst, postrm)        |
| `nginx/`              | ⏳ Phase 9       | TLS-terminating reverse-proxy config                             |
| `logrotate/` (planned)| ⏳ Phase 9       | Rotation rules for `/var/log/hermes/*.log`                       |
| `Dockerfile` (planned)| ⏳ Phase 9       | Multi-stage ARM64 + ARM32 build for development / cloud          |

## systemd units (shipped)

The systemd unit files live in [`systemd/`](./systemd/):

| Unit                          | Purpose                                                              |
| ----------------------------- | -------------------------------------------------------------------- |
| `hermes-api.service`          | FastAPI server. Switches between `mode=all` and `mode=live_only` via `/etc/hermes/api.env`. |
| `hermes-ingest.service`       | Single-process default. `Conflicts=` the shard template so the two can't run together. |
| `hermes-ingest@.service`      | Shard template. Each instance reads `HERMES_SHARD_INDEX=%i` from the systemd specifier. |
| `hermes.target`               | Aggregate target — `systemctl start hermes.target` brings up the whole stack in dependency order. |

See [`docs/design/MULTI_SHARD.md`](../docs/design/MULTI_SHARD.md) §7 for
the step-by-step deployment and rollback procedures (single-process
default vs 4-shard scaling).

## Why .deb and not Docker on the Pi

- **Lower memory overhead.** Docker's overlay-fs + containerd add
  ~150 MB resident memory; a Pi 4 with 2 GB RAM feels the difference.
- **systemd native.** Restart policies, journal integration, unit
  dependencies all work the standard Linux way.
- **Offline installs.** Customers on air-gapped factory networks can
  `dpkg -i hermes_X.Y.Z_arm64.deb` without a registry.

A Docker image still exists for development and cloud deployments
(via `docker-compose.dev.yml`); the `Dockerfile` here will build the
same artefact for cloud targets but is not the primary production
target.

## Build commands (once Phase 9 lands)

```bash
make deb          # builds hermes_X.Y.Z_arm64.deb
make deb-armhf    # armhf / Pi 3 and earlier
make docker       # multi-arch Docker image
```
