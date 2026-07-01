-- tae-outreach schema v2
-- Send-events table for the CT Outreach cockpit's live send-monitoring panel
-- (Project OutReach Cycle 2 §4). One row per CM delivery event, keyed by contact.
-- Populated later by the CM feedback-loop reconcile job (pulls bounce/complaint/
-- open/unsub activity via the CM API); empty until the first monitored send.

CREATE TABLE IF NOT EXISTS send_events (
    id            SERIAL PRIMARY KEY,
    contact_id    INT REFERENCES contacts(id),
    email         TEXT,                     -- denormalised: survives contact edits/deletes
    campaign      TEXT,                     -- CM campaign id / name
    event         TEXT NOT NULL,            -- sent|delivered|bounced|opened|clicked|unsubscribed|complained
    bounce_type   TEXT,                     -- hard|soft (when event = bounced)
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    detail        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_send_events_contact  ON send_events(contact_id);
CREATE INDEX IF NOT EXISTS idx_send_events_event    ON send_events(event);
CREATE INDEX IF NOT EXISTS idx_send_events_occurred ON send_events(occurred_at);
