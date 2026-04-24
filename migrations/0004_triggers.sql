-- 0004_triggers.sql
--
-- PL/pgSQL triggers enforcing two invariants the application layer would
-- otherwise have to chase:
--
--   1. Ending a global session auto-ends its local children (so the
--      operator doesn't have to stop every override individually).
--   2. A session that completes (ended_at set) locks its package (so
--      future edits to that package fork a new one via clone, preserving
--      the provenance of every event that fired during the session).
--
-- Both triggers fire AFTER UPDATE OF ended_at so they only run when the
-- session actually closes; partial updates to other columns are ignored.

BEGIN;

-- ─── Cascade end to local children ─────────────────────────────────

CREATE OR REPLACE FUNCTION end_local_children() RETURNS TRIGGER AS $$
BEGIN
    -- Only act on the global → children relationship, and only when
    -- ended_at actually transitioned from NULL to a value (avoid
    -- re-running on bookkeeping updates that touch ended_at but don't
    -- change it).
    IF NEW.scope = 'global'
       AND NEW.ended_at IS NOT NULL
       AND OLD.ended_at IS NULL
    THEN
        UPDATE sessions
           SET ended_at     = NEW.ended_at,
               ended_reason = 'global session ended'
         WHERE parent_session_id = NEW.session_id
           AND ended_at IS NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER sessions_cascade_end
    AFTER UPDATE OF ended_at ON sessions
    FOR EACH ROW EXECUTE FUNCTION end_local_children();

-- ─── Lock package on session end ───────────────────────────────────

CREATE OR REPLACE FUNCTION lock_package_on_session_end() RETURNS TRIGGER AS $$
BEGIN
    -- Lock unconditionally on first close; subsequent session closes
    -- that touch the same package are no-ops because is_locked is
    -- already TRUE.
    IF NEW.ended_at IS NOT NULL AND OLD.ended_at IS NULL THEN
        UPDATE packages
           SET is_locked = TRUE
         WHERE package_id = NEW.package_id
           AND is_locked  = FALSE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER sessions_lock_package
    AFTER UPDATE OF ended_at ON sessions
    FOR EACH ROW EXECUTE FUNCTION lock_package_on_session_end();

-- ─── updated_at maintenance ────────────────────────────────────────
--
-- Simple touch-updated-at trigger attached to tables that expose an
-- `updated_at` column. We keep the trigger function generic so new
-- tables can adopt it without extra PL/pgSQL.

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER devices_touch_updated_at
    BEFORE UPDATE ON devices
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TRIGGER sensor_offsets_touch_updated_at
    BEFORE UPDATE ON sensor_offsets
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMIT;
