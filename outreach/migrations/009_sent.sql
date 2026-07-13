-- 009_sent.sql — Project Postie send record (TAE-2607-15; combines TAE-2606-07 + TAE-2607-13)
--
-- Project Postie creates N cold-outreach drafts per day in glenn@ Drafts, one
-- per prospect, drawn from the sendable universe (confidence='direct',
-- disposition='in_play', cm_status='not_found') in export_batch priority order.
-- After a contact is drafted, its disposition flips in_play -> 'sent' (a new
-- disposition value; the existing set was in_play|ruled_out) so it never
-- re-drafts, and sent_at records the day it went out (GB 2026-07-13: "make a
-- note of the day on which those prospects were sent"). A dedicated column,
-- not exported_at (which means "exported to CM") — Postie is the manual glenn@
-- channel, not a CM send.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS sent_at DATE;
CREATE INDEX IF NOT EXISTS idx_contacts_sent_at ON contacts (sent_at);
