"""WP / MemberPress dedup — Email-list finalisation program §2b (TAE-2606-07).

Rules out any contact whose email is a current-or-past WordPress / MemberPress
account on theautoexec.com — we never cold-pitch someone already in the TAE
ecosystem (paid member or free subscriber with a WP account). Complements §2a
(CM dedup): CM covers subscribers, WP covers account holders; the union is the
full "already ours" set. This is the dedup that also closes the gap left by the
deleted *Website Members* CM list (its people are WP members).

Input is a plain file of WP account emails (one per line), pulled from VentraIP
read-only via `wp user list --field=user_email` (current + past accounts; ~all
carry a MemberPress transaction). Matches on `in_play` contacts are stamped
disposition='ruled_out', ruled_out_stage='wp-member' — a non-derivable stage, so
`ledger-refresh` leaves it intact. Idempotent (re-run only touches in_play rows).
"""
import logging

from outreach.db import get_conn

log = logging.getLogger("outreach.enrich.wp_dedup")


def run_wp_dedup(emails_file: str) -> dict:
    with open(emails_file) as f:
        wp_emails = sorted({line.strip().lower() for line in f if "@" in line})
    if not wp_emails:
        raise SystemExit(f"{emails_file} has no emails — pull WP accounts first.")

    with get_conn() as conn:
        matched = conn.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE email IS NOT NULL AND lower(email) = ANY(%s)",
            (wp_emails,),
        ).fetchone()["n"]
        newly = conn.execute(
            "UPDATE contacts SET disposition = 'ruled_out', ruled_out_stage = 'wp-member', "
            "  ruled_out_reason = 'existing WP/MemberPress account (member or subscriber)' "
            "WHERE email IS NOT NULL AND lower(email) = ANY(%s) AND disposition = 'in_play' "
            "RETURNING email",
            (wp_emails,),
        ).fetchall()
        by_source = {
            r["source"]: r["n"]
            for r in conn.execute(
                "SELECT source, COUNT(*) AS n FROM contacts "
                "WHERE ruled_out_stage = 'wp-member' GROUP BY source"
            ).fetchall()
        }

    summary = {
        "wp_email_set": len(wp_emails),
        "contacts_matched": matched,       # incl. those already ruled out by cm/suppress
        "newly_ruled_out": len(newly),
        "wp_member_total": sum(by_source.values()),
        "by_source": by_source,
    }
    log.info(
        "wp-dedup: %d WP emails; %d contacts matched; %d newly ruled_out (wp-member total %d) %s",
        len(wp_emails), matched, len(newly), summary["wp_member_total"], by_source,
    )
    return summary
