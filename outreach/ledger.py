"""Master provenance ledger refresh — Email-list finalisation program §3 (TAE-2606-07).

Keeps the ledger fields on `contacts` (migration 004) in sync with the current
viability signals, so a future Outreach round can eliminate addresses already
ruled out. Idempotent — safe to run after any harvest / dedup / suppress pass.

Two jobs:
  1. Backfill `company` (likely employer) where empty, from the best available
     signal per source: team_page → dealership name; newspress → the release
     client parsed from source_detail. OOO / manual left as-is.
  2. Derive `disposition` / `ruled_out_stage` / `ruled_out_reason` from the
     DERIVABLE viability signals — `suppressed` and `cm_status`. Rows ruled out by
     a NON-derivable stage (bounce / complaint / unsubscribe / do-not-contact,
     set later by the send-feedback loop) are left untouched.

Precedence: suppressed beats cm_status. Anything not ruled out by a derivable
stage is (re)set to in_play.
"""
import logging

from outreach.db import get_conn

log = logging.getLogger("outreach.ledger")

# Stages this refresh owns — it will set/clear only these, never a feedback-set stage.
DERIVABLE_STAGES = ("suppressed", "cm-active", "cm-unsubscribed", "cm-deleted")
CM_REASON = {
    "active": "already an active CM subscriber",
    "unsubscribed": "opted out in CM",
    "deleted": "deleted / hard-bounced in CM",
}


def run_ledger_refresh() -> dict:
    guard = "(ruled_out_stage IS NULL OR ruled_out_stage = ANY(%s))"
    with get_conn() as conn:
        # ── 1. company backfill (only where empty; never clobber) ──────────────
        team = conn.execute(
            "UPDATE contacts c SET company = d.name "
            "FROM dealerships d "
            "WHERE c.dealership_id = d.id AND c.company IS NULL AND d.name IS NOT NULL "
            "RETURNING c.id"
        ).rowcount
        # newspress source_detail = 'newspress: {client} — {title}' (em-dash separator)
        news = conn.execute(
            "UPDATE contacts SET company = "
            "  NULLIF(trim(split_part(substring(source_detail FROM 'newspress: (.*)'), ' — ', 1)), '') "
            "WHERE source = 'newspress' AND company IS NULL "
            "  AND source_detail LIKE 'newspress: % — %' "
            "RETURNING id"
        ).rowcount

        # ── 2. disposition derivation (suppressed beats cm; only derivable stages) ─
        supp = conn.execute(
            "UPDATE contacts SET disposition = 'ruled_out', ruled_out_stage = 'suppressed', "
            "  ruled_out_reason = COALESCE(suppress_reason, 'suppressed') "
            f"WHERE suppressed AND {guard}",
            (list(DERIVABLE_STAGES),),
        ).rowcount
        cm = conn.execute(
            "UPDATE contacts SET disposition = 'ruled_out', ruled_out_stage = 'cm-' || cm_status, "
            "  ruled_out_reason = CASE cm_status "
            "    WHEN 'active' THEN %s WHEN 'unsubscribed' THEN %s WHEN 'deleted' THEN %s END "
            f"WHERE NOT suppressed AND cm_status IN ('active','unsubscribed','deleted') AND {guard}",
            (CM_REASON["active"], CM_REASON["unsubscribed"], CM_REASON["deleted"], list(DERIVABLE_STAGES)),
        ).rowcount
        # back to in_play: not suppressed, not CM-known, and only if the current
        # ruled-out (if any) is a derivable stage we own.
        reset = conn.execute(
            "UPDATE contacts SET disposition = 'in_play', ruled_out_stage = NULL, ruled_out_reason = NULL "
            "WHERE disposition <> 'in_play' AND NOT suppressed "
            "  AND (cm_status IS NULL OR cm_status = 'not_found') "
            f"  AND {guard}",
            (list(DERIVABLE_STAGES),),
        ).rowcount

        counts = {
            r["disposition"]: r["n"]
            for r in conn.execute(
                "SELECT disposition, COUNT(*) AS n FROM contacts GROUP BY disposition"
            ).fetchall()
        }
        with_company = conn.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE company IS NOT NULL"
        ).fetchone()["n"]

    summary = {
        "company_backfilled": {"team_page": team, "newspress": news},
        "ruled_out_set": {"suppressed": supp, "cm": cm}, "reset_in_play": reset,
        "disposition": counts, "with_company": with_company,
    }
    log.info(
        "ledger-refresh: company +%d team +%d newspress; ruled_out suppressed=%d cm=%d; "
        "reset in_play=%d; now in_play=%d ruled_out=%d; with company=%d",
        team, news, supp, cm, reset, counts.get("in_play", 0),
        counts.get("ruled_out", 0), with_company,
    )
    return summary
