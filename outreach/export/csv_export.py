import csv
import logging
from outreach.db import get_conn

log = logging.getLogger("outreach.export.csv")


def export_csv(output_path: str = "contacts.csv"):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT c.full_name, c.first_name, c.last_name, "
            "c.role_raw, c.role_normalised, "
            "c.email, c.email_domain, c.email_pattern, "
            "c.phone, c.confidence, c.source, "
            "d.name AS dealership, d.brand_slug, d.suburb, d.state "
            "FROM contacts c "
            "JOIN dealerships d ON c.dealership_id = d.id "
            "WHERE c.email IS NOT NULL "
            "ORDER BY d.brand_slug, d.name, c.full_name"
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
