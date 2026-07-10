-- 007_verify.sql — email deliverability verification (TAE-2606-07)
-- Home-built verifier: paid services (ZeroBounce) return "unknown" for the ~83%
-- of this list behind Microsoft 365 / Google / gateways, which do not reveal
-- mailbox existence to anyone. What is genuinely knowable — dead domains,
-- catch-all domains, provider, role addresses — we compute ourselves and store
-- here so batching can draw from verified-deliverable.
--
-- verify_status:
--   pending          not yet checked
--   bad_syntax       not a valid address
--   dead_domain      domain has no MX / does not resolve  -> undeliverable
--   role             role inbox (info@, sales@, …)         -> low-value, not dead
--   unknown_gateway  behind M365/Google/gateway            -> unverifiable per-mailbox
--   unknown          self-hosted, awaiting or inconclusive SMTP probe
--   catchall         domain accepts every address          -> unverifiable per-mailbox
--   deliverable      SMTP RCPT accepted on a non-catch-all domain
--   undeliverable    SMTP RCPT rejected (550) on a non-catch-all domain
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS verify_status     TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS verify_provider   TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS verify_mx         TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS verify_catchall   BOOLEAN;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS verify_detail     TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS verify_checked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_contacts_verify_status ON contacts (verify_status);
