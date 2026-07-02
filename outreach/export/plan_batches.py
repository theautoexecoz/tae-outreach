"""§4b final batching — plan-batches (Email-list finalisation program, TAE-2606-07).

Assigns `export_batch` to the sendable pool: an ordered, throttled send schedule.
Produces the PLAN only — nothing sends here (Campaign Monitor sends, GB-gated).

Rules (GB 2026-07-02, "T1 GREEN first, then dealers"):
  - Eligible = in_play · emailable · CM-new · not suppressed · confidence='direct'
    (GREEN / published only — inferred/guessed are no-send per the send policy;
    pass include_inferred=True to append them after, for a later cycle).
  - Order = industry proximity (T1 → T2 → T3 → T4 → dealer), and within a tier,
    domain-staggered (round-robin across domains so no batch blasts one domain).
  - Per-domain cap = at most `max_per_domain` (default 5) contacts of any one
    domain per batch; overflow spills to later batches. Stops a big OEM domain
    (Mercedes/Daimler) from clustering into one send.
  - Size = a RAMP (default 50, 100, 200, 300, 500, then 500…) to warm the new
    `reach.theautoexec.com` sending domain — small, monitored first sends that
    grow as reputation builds. Between batches: 48h + the cockpit stop-lines.
    (The domain cap can make a batch end below its ramp target — that's fine.)

Idempotent: clears and reassigns export_batch each run.
"""
import logging
from collections import defaultdict, deque

from outreach.db import get_conn

log = logging.getLogger("outreach.export.plan_batches")

PROX_ORDER = ["T1", "T2", "T3", "T4", "dealer"]
DEFAULT_RAMP = [50, 100, 200, 300, 500]  # then 500 thereafter
DEFAULT_MAX_PER_DOMAIN = 5


def run_plan_batches(ramp: list[int] | None = None, include_inferred: bool = False,
                     max_per_domain: int = DEFAULT_MAX_PER_DOMAIN) -> dict:
    ramp = ramp or DEFAULT_RAMP
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
        conn.execute("UPDATE contacts SET export_batch = NULL WHERE export_batch IS NOT NULL")

        # per-domain queues, each ranked by its (best) proximity tier; contacts
        # within a domain ordered direct-first. A domain maps to one tier — rank
        # by the best (lowest) it appears under.
        prox_rank = {t: i for i, t in enumerate(PROX_ORDER)}
        dq: dict[str, deque] = defaultdict(deque)
        dom_rank: dict[str, int] = {}
        for tier in PROX_ORDER:
            trows = [r for r in rows if r["tier"] == tier]
            trows.sort(key=lambda r: 0 if r["confidence"] == "direct" else 1)
            for r in trows:
                dq[r["domain"]].append(r["id"])
                dom_rank.setdefault(r["domain"], prox_rank[tier])
        # sweep order: best tier first, then biggest domain first (spread the giants)
        active = sorted(dq, key=lambda d: (dom_rank[d], -len(dq[d])))

        batches: list[list[int]] = []
        ramp_i = 0
        while any(dq[d] for d in active):
            tgt = ramp[ramp_i] if ramp_i < len(ramp) else ramp[-1]
            cur: list[int] = []
            cur_dom: dict[str, int] = defaultdict(int)
            # fill to target by tier-priority round-robin (1 per domain per pass),
            # capping each domain per batch; fill spills into lower tiers to stay full
            while len(cur) < tgt:
                progressed = False
                for d in active:
                    if len(cur) >= tgt:
                        break
                    if dq[d] and cur_dom[d] < max_per_domain:
                        cur.append(dq[d].popleft())
                        cur_dom[d] += 1
                        progressed = True
                if not progressed:      # nothing else can enter this batch
                    break
            if not cur:
                break
            batches.append(cur)
            ramp_i += 1

        for bn, chunk in enumerate(batches, 1):
            conn.execute("UPDATE contacts SET export_batch = %s WHERE id = ANY(%s)", (bn, chunk))

        # composition report
        comp = conn.execute(
            "SELECT export_batch AS b, "
            "       COUNT(*) AS n, "
            "       COUNT(*) FILTER (WHERE proximity_tier='T1') AS t1, "
            "       COUNT(*) FILTER (WHERE proximity_tier='T2') AS t2, "
            "       COUNT(*) FILTER (WHERE proximity_tier='T3') AS t3, "
            "       COUNT(*) FILTER (WHERE proximity_tier='T4') AS t4, "
            "       COUNT(*) FILTER (WHERE proximity_tier='dealer') AS dealer, "
            "       MAX(dcount) AS max_per_domain "
            "FROM (SELECT id, export_batch, proximity_tier, email_domain, "
            "             COUNT(*) OVER (PARTITION BY export_batch, email_domain) AS dcount "
            "      FROM contacts WHERE export_batch IS NOT NULL) s "
            "GROUP BY export_batch ORDER BY export_batch"
        ).fetchall()

    summary = {
        "sendable": len(rows), "batches": len(batches),
        "rows": [dict(r) for r in comp],
    }
    log.info("plan-batches: %d sendable (%s) → %d batches (max %d/domain)",
             len(rows), "direct only" if not include_inferred else "incl. inferred",
             len(batches), max_per_domain)
    return summary
