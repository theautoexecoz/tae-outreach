"""Do-not-email suppression by domain.

Some addresses must never receive a TAE outreach send regardless of CM state —
chiefly competitor / motoring-media outlets we don't want to cold-email. This
stamps `contacts.suppressed = true` (+ reason) for any contact whose
email_domain is on the blocklist. Re-runnable and idempotent — run it after any
import of new contacts so the exportable pool stays clean.

The exportable pool (csv export / Stage-7 batches) excludes suppressed contacts.
"""
import logging
from outreach.db import get_conn

log = logging.getLogger("outreach.enrich.suppress")

# Motoring-media / competitor domains — never cold-email these (journalists &
# rival outlets aren't outreach targets). Extended 2026-07-01 with the motoring-
# media domains surfaced by the Newspress §1b harvest.
SUPPRESS_DOMAINS = {
    "drive.com.au",
    "carsales.com.au",
    "carexpert.com.au",
    "carsauce.com",
    "carsauce.com.au",
    # motoring media surfaced via Newspress release contact blocks
    "evcentral.com.au",
    "wheelsmedia.com.au",
    "whichcar.com.au",
    "carsguide.com.au",
    "caradvice.com.au",
    "motoring.com.au",
    "carscoops.com",
    "thedriven.io",
    "goauto.com.au",
    "redriven.com.au",
    "news.com.au",
}


def run_suppress(domains: set[str] | None = None, reason: str = "media_domain") -> dict:
    """Stamp suppressed=true for contacts on the blocklisted domains. Idempotent."""
    domains = domains or SUPPRESS_DOMAINS
    dlist = sorted(domains)
    with get_conn() as conn:
        rows = conn.execute(
            "UPDATE contacts SET suppressed = TRUE, suppress_reason = %s "
            "WHERE email_domain = ANY(%s) AND (NOT suppressed OR suppress_reason IS DISTINCT FROM %s) "
            "RETURNING email",
            (reason, dlist, reason),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE suppressed"
        ).fetchone()["n"]
    log.info("suppressed %d newly-stamped on %s; %d suppressed in total",
             len(rows), ", ".join(dlist), total)
    return {"newly_suppressed": [r["email"] for r in rows], "total_suppressed": total}
