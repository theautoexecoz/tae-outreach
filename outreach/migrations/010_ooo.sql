-- 010_ooo.sql — Project Postie OOO follow-up marker (TAE-2607-13; GB 2026-07-14)
--
-- Prospects who returned an out-of-office auto-reply to a reach send were
-- DELIVERED to (the OOO proves receipt) — they are not bounces and must not be
-- suppressed. GB's rule: hold every OOO for follow-up at the END OF THE FIRST
-- PASS. At that point we check whether they subscribed on their own; if not, we
-- risk a single resend.
--
-- A dedicated column (not a new disposition): the contact keeps disposition
-- 'sent' so the normal in_play send query still skips it during this pass, and
-- ooo_at makes the follow-up cohort a first-class, queryable set — exactly the
-- pattern 009_sent.sql set for sent_at. End-of-pass follow-up candidates:
--   SELECT * FROM contacts WHERE ooo_at IS NOT NULL AND cm_status = 'not_found';
-- (run cm-dedup first so cm_status is current; 'not_found' = did not subscribe).
-- To risk the resend, flip those back to disposition='in_play'.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS ooo_at DATE;
CREATE INDEX IF NOT EXISTS idx_contacts_ooo_at ON contacts (ooo_at);
