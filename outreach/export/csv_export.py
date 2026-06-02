import csv
import logging
from outreach.db import get_conn

log = logging.getLogger("outreach.export.csv")


def export_csv(output_path: str = "contacts.csv", all_contacts: bool = False):
    """Export contacts to CSV.

    Default = the exportable mailout pool: has an email, not suppressed, and not
    already known to CM (cm_status not_found/NULL). LEFT JOINs dealerships so
    non-dealership contacts (source='manual'/'ooo', dealership_id NULL) survive
    — they were previously dropped by an INNER JOIN. Pass all_contacts=True to
    dump everything-with-email (incl. CM-matched / suppressed) for review.
    """
    pool = (
        ""
        if all_contacts
        else "AND NOT c.suppressed AND (c.cm_status = 'not_found' OR c.cm_status IS NULL) "
    )
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT c.full_name, c.first_name, c.last_name, "
            "c.role_raw, c.role_normalised, "
            "c.email, c.email_domain, c.email_pattern, "
            "c.phone, c.confidence, c.source, c.cm_status, c.suppressed, "
            "COALESCE(d.name, '') AS dealership, "
            "COALESCE(d.brand_slug, '') AS brand_slug, d.suburb, d.state "
            "FROM contacts c "
            "LEFT JOIN dealerships d ON c.dealership_id = d.id "
            "WHERE c.email IS NOT NULL "
            + pool +
            "ORDER BY brand_slug, dealership, c.full_name"
        )
        rows = cur.fetchall()

    if not rows:
        log.info("no contacts with emails to export")
        return

    fieldnames = list(rows[0].keys())
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    log.info("exported %d contacts to %s", len(rows), output_path)
