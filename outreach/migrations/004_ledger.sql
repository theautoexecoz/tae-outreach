-- 004 — Master provenance ledger (Email-list finalisation program §3, TAE-2606-07).
--
-- Extends `contacts` into a cross-round ledger so a future Outreach round never
-- re-tries an address already ruled out. full_name / role_* / email already exist;
-- this adds the missing ledger fields (GB 2026-07-01):
--   company          likely employer (dealership name / release client / …)
--   details          distinguishing free-text notes
--   disposition      in_play | ruled_out   (default in_play)
--   ruled_out_stage  which viability stage killed it: suppressed · cm-active ·
--                    cm-unsubscribed · cm-deleted · wp-member · bounce · complaint ·
--                    unsubscribe · do-not-contact · bucket-red · …
--   ruled_out_reason free-text reason
-- Populated idempotently by `ledger-refresh` (derives from suppressed + cm_status,
-- backfills company) and, going forward, by the dedup/suppress/feedback stages.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS company          TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS details          TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS disposition      TEXT NOT NULL DEFAULT 'in_play';
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS ruled_out_stage  TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS ruled_out_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_contacts_disposition ON contacts(disposition);
