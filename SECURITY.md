# Security Policy

## Supported versions

HERMES is in pre-release. Until `v1.0.0`, only the `main` branch is covered.
After GA, the most recent two minor releases receive security fixes.

| Version | Supported |
|---------|-----------|
| `main`  | ✅        |
| < 1.0   | ❌        |

## Reporting a vulnerability

**Do not open a public GitHub issue for security reports.**

Email `security@embedsquare.com` with:

- A description of the issue and its impact.
- Steps to reproduce, or a proof-of-concept.
- Affected version / commit.
- Your contact for follow-up.

We acknowledge within **48 hours** and aim to triage within **5 working days**.
If the report qualifies as a vulnerability, we coordinate a disclosure timeline
with you before releasing a fix.

## Scope

In scope:

- The HERMES backend services (`hermes-api`, `hermes-ingest`).
- The SvelteKit frontend and its production build artefacts.
- The `.deb` packaging and systemd unit files.
- SQL migrations and database trigger definitions.
- Authentication flows (OTP issuance, JWT handling).

Out of scope:

- Vulnerabilities in upstream dependencies — please report those directly to
  the maintainers of the affected package.
- Attacks requiring physical access to the Raspberry Pi host.
- Social-engineering attacks that do not involve a technical flaw in HERMES.

## Credential handling

1. **Never commit credentials.** `.env`, `secrets/`, `*.pem`, `*.key` are
   `.gitignore`d. If a credential enters a commit by accident, **rotate it
   immediately** and then rewrite history via `git filter-repo` (do not only
   revert the commit — the secret remains in history).
2. **Production secrets live outside the repo**, in
   `/etc/hermes/secrets.env` (readable only by the `hermes` user).
3. **OTPs are hashed** with argon2id; we never store plaintext OTPs.
4. **MQTT broker credentials**, if set, are encrypted at the application
   layer before persistence.

### Historical leak

An earlier iteration of HERMES exposed a Gmail app password in the
repository. That credential has been revoked with Google and replaced. The
present codebase retains no plaintext secrets; any reference in
`docs/reference/` has been redacted.

If you are migrating from the legacy system and still see the string
`hlme zlvm wjoe zqgv` in a dump or backup — that password is **already
revoked** and cannot be used to authenticate. You should still excise it
from any archive you keep.

## Defence in depth

- TLS terminates at nginx; systemd drops privileges (`User=hermes`, `ProtectSystem=strict`).
- Database access uses a least-privilege role (`hermes_app`) distinct from the
  migration role (`hermes_migrate`).
- All external input is validated at the API layer; SQL uses parameterised
  queries exclusively.
- Audit trail is append-only (`session_logs`, `events`).

## Responsible disclosure

We credit reporters in release notes unless you request anonymity.
