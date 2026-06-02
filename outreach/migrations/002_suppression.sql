-- 002 — per-contact suppression (do-not-email), independent of CM state.
-- Used to permanently exclude addresses we never want in an outreach send
-- (e.g. competitor / motoring-media domains), regardless of cm_status.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS suppressed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS suppress_reason TEXT;
CREATE INDEX IF NOT EXISTS idx_contacts_suppressed ON contacts(suppressed);
