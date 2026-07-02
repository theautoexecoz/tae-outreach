-- 005 — Industry-proximity tier (Email-list finalisation program §4b, TAE-2606-07).
--
-- Batch-ordering axis, orthogonal to trust/GREEN. Values:
--   dealer  franchised dealership staff (the campaign's core retail audience; source=team_page)
--   T1      OEMs + importers / distributors
--   T2      car-industry bodies + direct suppliers
--   T3      agencies + other service providers (PR / media / marketing)
--   T4      the rest (freemail, gov, non-automotive, unclassified)
-- Set idempotently by `classify-proximity`. First monitored send = GREEN + high-trust,
-- ordered T1 → T4 within the industry contacts (dealers batched via plan-batches).
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS proximity_tier TEXT;
CREATE INDEX IF NOT EXISTS idx_contacts_proximity ON contacts(proximity_tier);
