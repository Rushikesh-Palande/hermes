# Packaging

Production deployment artefacts for HERMES.

> **For end-user "how do I install this on my Linux box?" answer:**
> see [`../docs/operations/INSTALLATION.md`](../docs/operations/INSTALLATION.md).
> This README documents the *contents* of `packaging/`; INSTALLATION.md
> walks through *using* it.

## Layout

```
packaging/
├── README.md                     ← this file
├── install.sh                    one-shot installer (Path A)
├── uninstall.sh                  clean removal
├── build-offline-bundle.sh       builds a self-contained .tar.gz (Path C)
├── Dockerfile                    multi-stage container build (Path B)
├── docker-compose.prod.yml       production compose stack (Path B)
├── debian/                       proper Debian package metadata
│   ├── control                   declares the .deb's deps + description
│   ├── changelog                 release history (Debian format)
│   ├── copyright                 license declaration
│   ├── rules                     dpkg-buildpackage orchestration
│   ├── install                   maps source paths → /opt/hermes/
│   ├── postinst                  runs install.sh after dpkg unpacks
│   └── postrm                    cleans up on remove/purge
├── nginx/
│   └── hermes.conf               TLS-ready reverse proxy site
├── systemd/
│   ├── hermes-api.service        FastAPI under systemd
│   ├── hermes-ingest.service     single-process default
│   ├── hermes-ingest@.service    multi-shard template (gap 3)
│   └── hermes.target             aggregate
├── offline/                      (created by build-offline-bundle.sh)
│                                 .debs for offline install
└── wheelhouse/                   (created by build-offline-bundle.sh)
                                  Python wheels for offline install
```

## Three install paths

| Path | Artifact | Best for |
|------|----------|----------|
| **A** | `install.sh` | Pi 4 / cloud VM with internet |
| **B** | `Dockerfile` + `docker-compose.prod.yml` | "I have Docker" / cloud / dev |
| **C** | `.tar.gz` from `build-offline-bundle.sh` + `install.sh --offline` | Air-gapped factory floor |

Detail in [`../docs/operations/INSTALLATION.md`](../docs/operations/INSTALLATION.md).

## Building a `.deb` package

```bash
# On a Debian 12 (or later) build host with debhelper installed
sudo apt install -y debhelper devscripts
dpkg-buildpackage -us -uc -b
# Output: ../hermes_0.1.0~alpha.X-1_amd64.deb
```

The resulting `.deb` carries the full source tree to `/opt/hermes/`
and runs `install.sh` automatically in `postinst`. End users do:

```bash
sudo dpkg -i hermes_0.1.0~alpha.X-1_amd64.deb
sudo apt install -f   # resolves any deps dpkg pulled
```

## Building the Docker image

```bash
docker build -t hermes:local -f packaging/Dockerfile .
```

Multi-arch:

```bash
docker buildx create --use
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -t ghcr.io/<org>/hermes:0.1.0-alpha.X \
    -f packaging/Dockerfile \
    --push .
```

## Building an offline bundle

```bash
./packaging/build-offline-bundle.sh \
    --arch amd64 \
    --out hermes-0.1.0-alpha.X-amd64-offline.tar.gz
```

Output: ~150 MB compressed. Includes:
- Full source tree
- `.deb`s for every system package (~80 MB)
- Python wheels for every dep (~50 MB)
- Pre-built SvelteKit bundle

## Why .deb (and Docker) and not Snap / Flatpak / AppImage

- **.deb** is the native package format on the Pi 4 production target
  (Raspberry Pi OS = Debian). Lower memory overhead than Snap/Flatpak
  runtimes.
- **AppImage** is a single-file desktop format; HERMES is a
  multi-process service stack, not a desktop app. AppImage doesn't
  handle systemd unit installation cleanly.
- **Docker** covers the "any-Linux" use case for everyone who's
  running anything other than Pi.

## Why ".deb" deps go through apt and not bundled

The realistic minimum on a fresh Debian/Ubuntu is:

```
postgresql-16             ~30 MB installed
timescaledb               ~5 MB
mosquitto                 ~2 MB
nginx                     ~6 MB
python3.11                ~50 MB (interpreter + stdlib)
+ HERMES wheels           ~50 MB
+ HERMES SvelteKit build  ~5 MB
─────────────────────────
Total                     ~150 MB
```

Bundling all that as a single AppImage or static binary would also be
~150 MB — not smaller. And the security model is worse (you'd ship
your own copies of OpenSSL / glibc and never patch them). Going
through apt means the host's package manager handles security
updates for everything-except-HERMES on its normal cadence.

The offline bundle (Path C) does ship all those .debs — but it's only
~150 MB compressed because dpkg has done the dedup against the host's
existing libs.
