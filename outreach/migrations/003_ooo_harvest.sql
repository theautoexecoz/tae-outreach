-- 003 — OOO reply harvest (Email-list finalisation program §1a, TAE-2606-07).
--
-- Per-domain address-format intelligence learned from out-of-office senders.
-- The SENDER of an OOO is NOT harvested as a contact (GB rule 2026-07-01 — it is
-- already a newsletter subscriber), but its From display-name paired against its
-- local-part reveals how that domain builds addresses (first.last, flast, …).
-- We aggregate the most-common pattern per domain so later passes can infer
-- addresses for other people on the same domain. Harvested delegate contacts go
-- into `contacts` as source='ooo_reply' (no schema change — dealership_id NULL).
CREATE TABLE IF NOT EXISTS ooo_domain_formats (
    domain        TEXT PRIMARY KEY,
    pattern       TEXT,                              -- most-common local-part pattern (first.last, flast, …)
    sample_email  TEXT,                              -- one real address seen on this domain
    sender_count  INT NOT NULL DEFAULT 0,            -- OOO senders seen on this domain
    pattern_count INT NOT NULL DEFAULT 0,            -- of those, how many matched `pattern`
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
