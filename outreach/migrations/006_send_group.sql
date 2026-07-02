-- 006_send_group.sql — per-tier batch label (TAE-2606-07)
-- send_group = the CM-list / cockpit "sending group" label, e.g. T1-B01, DLR-B03.
-- Distinct from export_batch (the global send-order integer): send_group carries
-- the tier + per-tier batch number so a CM list is named Outreach-<send_group>.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS send_group TEXT;
CREATE INDEX IF NOT EXISTS idx_contacts_send_group ON contacts (send_group);
