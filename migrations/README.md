# Migrations

Plain SQL migrations applied in filename order. No Alembic, no ORM-driven
auto-migration: DDL changes get reviewed and committed the same way as
application code.

## Files

| File | Purpose |
|---|---|
| `0001_init_extensions.sql`    | Enable `timescaledb` + `pgcrypto`. |
| `0002_core_tables.sql`        | Devices, packages, parameters, sessions, session_logs, events, event_windows, session_samples, sensor_offsets, users, user_otps, mqtt_brokers. |
| `0003_hypertables.sql`        | Convert `events` and `session_samples` into TimescaleDB hypertables with sensible chunk intervals. |
| `0004_triggers.sql`           | Cascade-end local children when the global session ends; lock the package on session close; touch `updated_at`. |
| `0005_retention_policies.sql` | Compress `events` > 30 d, drop `event_windows` > 1 y, compress `session_samples` > 1 h. |

## Running

Development, from `docker-compose.dev.yml`:

```bash
./scripts/db-migrate.sh
```

Equivalent manual invocation:

```bash
for f in migrations/00*.sql; do
  echo ">>> $f"
  psql "$MIGRATE_DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

Production uses the same script, but the Postgres URL points at the
local systemd-managed Postgres instance and `MIGRATE_DATABASE_URL` loads
credentials from `/etc/hermes/secrets.env`.

## Authoring rules

1. **Forward-only.** Every migration is idempotent (`CREATE TABLE … IF NOT
   EXISTS`, `CREATE INDEX … IF NOT EXISTS`, `add_*_policy(..., if_not_exists => TRUE)`).
2. **Numbering.** Four-digit prefix, underscore-slug. A PR touching the
   schema gets the next available number; conflicts on main are rebased,
   not squashed.
3. **Down scripts.** Not currently required (Timescale does not cleanly
   support all drops), but reversible migrations are preferred. When a
   down path is infeasible, explain why in the migration's header comment.
4. **No data migrations here.** Data backfills live in `tools/migrate_*.py`
   with proper transaction handling and progress reporting.
5. **Review.** DDL changes need a maintainer review per `CODEOWNERS`.

## Schema source of truth

See [`docs/design/DATABASE_REDESIGN.md`](../docs/design/DATABASE_REDESIGN.md).
Changes to the schema require updating that document in the same PR.
