-- tae-outreach schema v1
-- Project OutReach: dealer contact harvesting for TAE outreach campaigns

CREATE TABLE dealer_groups (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    domain          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE dealerships (
    id              SERIAL PRIMARY KEY,
    brand_slug      TEXT NOT NULL,
    dealer_group_id INT REFERENCES dealer_groups(id),
    name            TEXT NOT NULL,
    address         TEXT,
    suburb          TEXT,
    state           TEXT,
    postcode        TEXT,
    phone           TEXT,
    website_url     TEXT,
    team_page_url   TEXT,
    team_page_html  TEXT,
    scrape_state    TEXT NOT NULL DEFAULT 'discovered',
    discovered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    scraped_at      TIMESTAMPTZ,
    UNIQUE(brand_slug, name, suburb)
);

CREATE INDEX idx_dealerships_brand ON dealerships(brand_slug);
CREATE INDEX idx_dealerships_state ON dealerships(scrape_state);

CREATE TABLE contacts (
    id              SERIAL PRIMARY KEY,
    dealership_id   INT REFERENCES dealerships(id),
    full_name       TEXT NOT NULL,
    first_name      TEXT,
    last_name       TEXT,
    role_raw        TEXT,
    role_normalised TEXT,
    email           TEXT,
    email_domain    TEXT,
    phone           TEXT,
    confidence      TEXT NOT NULL DEFAULT 'direct',
    source          TEXT NOT NULL DEFAULT 'team_page',
    source_detail   TEXT,
    email_pattern   TEXT,
    cm_status       TEXT,
    export_batch    INT,
    exported_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_contacts_email ON contacts(email) WHERE email IS NOT NULL;
CREATE INDEX idx_contacts_domain ON contacts(email_domain);
CREATE INDEX idx_contacts_batch ON contacts(export_batch);
