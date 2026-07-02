"""§4b final batching — plan-batches (Email-list finalisation program, TAE-2606-07).

Assigns `export_batch` + `send_group` to the sendable pool: an ordered, throttled
send schedule. Produces the PLAN only — nothing sends here (Campaign Monitor sends,
GB-gated).

Rules (GB 2026-07-03, "+50 growth, tier-homogeneous"):
  - Eligible = in_play · emailable · CM-new · not suppressed · confidence='direct'
    (GREEN / published only — inferred/guessed are no-send per the send policy;
    pass include_inferred=True to append them after, for a later cycle).
  - Order = industry proximity (T1 → T2 → T3 → T4 → dealer). Each batch is
    tier-homogeneous (never mixes tiers) so a CM list maps 1:1 to a send group
    named Outreach-<send_group> (e.g. Outreach-T1-B01, Outreach-DLR-B03).
  - Within a tier, domain-staggered (round-robin across domains, biggest first)
    so no batch blasts one domain; per-domain cap = at most `max_per_domain`
    (default 5) of any one domain per batch (overflow spills to later batches).
  - Size = a +50 RAMP that restarts per tier: 50, 100, 150, 200, 250, … (uncapped)
    to warm the new reach.theautoexec.com sending domain. Between batches: 48h +
    the cockpit stop-lines. The domain cap can end a batch below its ramp target —
    that's fine. Pass an explicit `ramp` list to override the +50 default.

  send_group = "<TIER>-B<NN>" (dealer → DLR), per-tier batch number, zero-padded.
  export_batch = global send order (1..N) across all tiers, for stable ordering.

Idempotent: clears and reassigns export_batch + send_group each run.
"""
import logging
from collections import defaultdict, deque

from outreach.db import get_conn

log = logging.getLogger("outreach.export.plan_batches")

PROX_ORDER = ["T1", "T2", "T3", "T4", "dealer"]
TIER_LABEL = {"T1": "T1", "T2": "T2", "T3": "T3", "T4": "T4", "dealer": "DLR"}
RAMP_STEP = 50                      # +50 per send, uncapped, restarts per tier
DEFAULT_MAX_PER_DOMAIN = 5


def _target(tier_batch_i: int, ramp: list[int] | None) -> int:
    """Size for the tier_batch_i-th (0-indexed) batch within a tier."""
    if ramp:
        return ramp[tier_batch_i] if tier_batch_i < len(ramp) else ramp[-1]
    return (tier_batch_i + 1) * RAMP_STEP


def run_plan_batches(ramp: list[int] | None = None, include_inferred: bool = False,
                     max_per_domain: int = DEFAULT_MAX_PER_DOMAIN) -> dict:
    cond = ("disposition = 'in_play' AND email IS NOT NULL AND NOT suppressed "
            "AND (cm_status = 'not_found' OR cm_status IS NULL)")
    if not include_inferred:
        cond += " AND confidence = 'direct'"

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, COALESCE(proximity_tier, 'T4') AS tier, "
            "       COALESCE(email_domain, '?') AS domain, confidence "
            f"FROM contacts WHERE {cond}"
        ).fetchall()
        conn.execute("UPDATE contacts SET export_batch = NULL, send_group = NULL "
                     "WHERE export_batch IS NOT NULL OR send_group IS NOT NULL")

        # (global_batch_no, send_group_label, [contact_ids]) in send order
        assignments: list[tuple[int, str, list[int]]] = []
        global_bn = 0

        for tier in PROX_ORDER:
            trows = [r for r in rows if r["tier"] == tier]
            if not trows:
                continue
            # per-domain queues within the tier, direct-first
            trows.sort(key=lambda r: 0 if r["confidence"] == "direct" else 1)
            dq: dict[str, deque] = defaultdict(deque)
            for r in trows:
                dq[r["domain"]].append(r["id"])
            # biggest domain first so the giants spread across many batches
            active = sorted(dq, key=lambda d: -len(dq[d]))

            tier_bn = 0
            while any(dq[d] for d in active):
                tgt = _target(tier_bn, ramp)
                cur: list[int] = []
                cur_dom: dict[str, int] = defaultdict(int)
                while len(cur) < tgt:
                    progressed = False
                    for d in active:
                        if len(cur) >= tgt:
                            break
                        if dq[d] and cur_dom[d] < max_per_domain:
                            cur.append(dq[d].popleft())
                            cur_dom[d] += 1
                            progressed = True
                    if not progressed:      # domain cap blocks any further fill
                        break
                if not cur:
                    break
                tier_bn += 1
                global_bn += 1
                sg = f"{TIER_LABEL[tier]}-B{tier_bn:02d}"
                assignments.append((global_bn, sg, cur))

        for gbn, sg, chunk in assignments:
            conn.execute(
                "UPDATE contacts SET export_batch = %s, send_group = %s WHERE id = ANY(%s)",
                (gbn, sg, chunk),
            )

        # composition report
        comp = conn.execute(
            "SELECT export_batch AS b, MAX(send_group) AS send_group, "
            "       COUNT(*) AS n, "
            "       COUNT(*) FILTER (WHERE proximity_tier='T1') AS t1, "
            "       COUNT(*) FILTER (WHERE proximity_tier='T2') AS t2, "
            "       COUNT(*) FILTER (WHERE proximity_tier='T3') AS t3, "
            "       COUNT(*) FILTER (WHERE proximity_tier='T4') AS t4, "
            "       COUNT(*) FILTER (WHERE proximity_tier='dealer') AS dealer, "
            "       COUNT(DISTINCT email_domain) AS domains, "
            "       MAX(dcount) AS max_per_domain "
            "FROM (SELECT id, export_batch, send_group, proximity_tier, email_domain, "
            "             COUNT(*) OVER (PARTITION BY export_batch, email_domain) AS dcount "
            "      FROM contacts WHERE export_batch IS NOT NULL) s "
            "GROUP BY export_batch ORDER BY export_batch"
        ).fetchall()

    summary = {
        "sendable": len(rows), "batches": len(assignments),
        "rows": [dict(r) for r in comp],
    }
    log.info("plan-batches: %d sendable (%s) → %d batches (max %d/domain)",
             len(rows), "direct only" if not include_inferred else "incl. inferred",
             len(assignments), max_per_domain)
    return summary
