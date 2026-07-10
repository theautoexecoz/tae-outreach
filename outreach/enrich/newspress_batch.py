"""Select the Newspress outreach batch per GB's 2026-07-10 rules.

The batch is the surviving Newspress contacts that:
  1. are within the recency window (source_date on/after the cutoff),
  2. are healthy (not dead_domain / bad_syntax / undeliverable, not suppressed),
  3. have NEVER been a subscriber in any CM state (cm_status = 'not_found'), and
  4. do not match an ACTIVE subscriber BY NAME — catching a person who already
     subscribes under a different address or domain. The active-subscriber
     record trumps: same person, we do not cold-email them again.

Rule 4 is the domain-trump / moved-roles case: jane@volkswagen.com.au in
Newspress is dropped if a "Jane Smith" subscribes actively as jane@vwag.com.

Dry-run by default; --apply stamps send_group so the batch is exportable.
"""
import logging

from outreach.db import get_conn
from outreach.enrich.cm_dedup import name_key

log = logging.getLogger("outreach.enrich.newspress_batch")

HEALTHY_EXCLUDE = ("dead_domain", "bad_syntax", "undeliverable")


def select_batch(months_back: int = 12, require_date: bool = True,
                 send_group: str | None = None, apply: bool = False) -> dict:
    with get_conn() as conn:
        active_keys = {
            r["name_key"]
            for r in conn.execute(
                "SELECT DISTINCT name_key FROM cm_active_subscribers WHERE name_key IS NOT NULL"
            ).fetchall()
        }

        date_clause = ""
        params: dict = {"excl": list(HEALTHY_EXCLUDE)}
        if months_back:
            date_clause = (
                " AND source_date >= (CURRENT_DATE - (%(months)s || ' months')::interval)"
                if require_date else
                " AND (source_date IS NULL OR source_date >= (CURRENT_DATE - (%(months)s || ' months')::interval))"
            )
            params["months"] = months_back

        rows = conn.execute(
            "SELECT id, email, full_name, first_name, last_name, email_domain, "
            "       source_date, verify_status, cm_status "
            "FROM contacts "
            "WHERE source = 'newspress' AND NOT suppressed AND disposition = 'in_play' "
            "  AND cm_status = 'not_found' "
            "  AND (verify_status IS NULL OR verify_status <> ALL(%(excl)s)) "
            + date_clause +
            " ORDER BY source_date DESC NULLS LAST, email",
            params,
        ).fetchall()

        kept, name_dropped = [], []
        for r in rows:
            k = name_key(r["first_name"], r["last_name"]) or name_key(r["full_name"])
            if k and k in active_keys:
                name_dropped.append(r)
            else:
                kept.append(r)

        result = {
            "candidates_before_name_check": len(rows),
            "dropped_active_name_match": len(name_dropped),
            "batch_size": len(kept),
            "name_dropped_sample": [
                {"email": r["email"], "name": r["full_name"]} for r in name_dropped[:15]
            ],
            "batch_sample": [
                {"email": r["email"], "name": r["full_name"],
                 "domain": r["email_domain"],
                 "date": str(r["source_date"]) if r["source_date"] else None,
                 "verify": r["verify_status"]}
                for r in kept[:25]
            ],
            "applied": False,
        }

        if apply and kept:
            grp = send_group or "NP-B01"
            conn.execute(
                "UPDATE contacts SET send_group = %s WHERE id = ANY(%s)",
                (grp, [r["id"] for r in kept]),
            )
            result["applied"] = True
            result["send_group"] = grp
            log.info("stamped send_group=%s on %d Newspress contacts", grp, len(kept))

    return result
