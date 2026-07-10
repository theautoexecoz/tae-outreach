-- 008_source_date.sql — recency gating + CM active-subscriber name cross-check (TAE-2606-07)
--
-- source_date: the date the contact's source evidence was published. For a
-- Newspress contact it is the press release's date, so we can gate the harvest
-- to the last N months and shed likely-departed OEM staff (GB, 2026-07-10).
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS source_date DATE;
CREATE INDEX IF NOT EXISTS idx_contacts_source_date ON contacts (source_date);

-- CM active subscribers, captured by the dedup pull, so a Newspress contact can
-- be cross-checked by NAME (not just email) against people who already subscribe.
-- This catches a person who subscribes under a different address or domain
-- (e.g. subscribes as jane@vwag.com, appears in Newspress as jane@volkswagen.com.au)
-- — the active-subscriber record trumps, so we do not cold-email them again.
CREATE TABLE IF NOT EXISTS cm_active_subscribers (
    email       TEXT PRIMARY KEY,
    first_name  TEXT,
    last_name   TEXT,
    name_key    TEXT,          -- normalised "firstname lastname" for matching
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cm_active_name_key ON cm_active_subscribers (name_key);
