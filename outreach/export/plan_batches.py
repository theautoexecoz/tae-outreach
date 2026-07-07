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
  - Size = a +50 RAMP that restarts per tier and plateaus at 250:
    50, 100, 150, 200, 250, 250, … to warm the new reach.theautoexec.com sending
    domain. Between batches: 48h + the cockpit stop-lines. Any sub-50 tail batch
    (deep-domain drainage under the per-domain cap) is coalesced back so there are
    no dribble sends. Pass an explicit `ramp` list to override the +50 default.

  send_group = "<TIER>-B<NN>" (dealer → DLR), per-tier batch number, zero-padded.
  export_batch = global send order (1..N) across all tiers, for stable ordering.

Idempotent: clears and reassigns export_batch + send_group each run.
"""
import logging
import re
from collections import defaultdict, deque

from outreach.db import get_conn

log = logging.getLogger("outreach.export.plan_batches")

# Send order (GB 2026-07-08, supersedes the 2026-07-03 dealers-first ordering below):
# OEM (T1) first, restricted to domains that read as a car brand AND are .au or generic
# .com — i.e. band AU then COM, explicitly excluding INTL (.co.nz/.co.uk/… regional HQs)
# from this first phase. Once AU+COM T1 is exhausted, dealers (any band). Then everything
# else — T2-T4 across all bands, plus the T1-INTL contacts excluded from phase 1 land here
# too (still OEM, just not .au/.com).
#   Band  AU   = .au domains           — Australian, the real audience.
#         COM  = generic gTLDs (.com…) — many AU importers use a global address.
#         INTL = country-code TLDs      — obvious overseas HQ (.de/.co.uk/.co.jp/…); off-audience.
# On top of that, generic ROLE/department inboxes (sales@, service@, info@, sales.department@)
# are pushed to the very back of the queue — kept, not suppressed (GB 2026-07-03), but sent
# only after every real person, since they're shared inboxes not individuals. Real people
# get send_group "<BAND>-<TIER>-B<NN>"; role inboxes get "ROLE-<BAND>-<TIER>-B<NN>".
GEO_TIER_ORDER = [
    ("AU", "T1"), ("COM", "T1"),                       # phase 1: OEM, AU/COM only
    ("AU", "dealer"), ("COM", "dealer"), ("INTL", "dealer"),  # phase 2: dealers, any band
    ("AU", "T2"), ("COM", "T2"), ("INTL", "T2"),        # phase 3: the rest, tier order T2-T4
    ("AU", "T3"), ("COM", "T3"), ("INTL", "T3"),
    ("AU", "T4"), ("COM", "T4"), ("INTL", "T4"),
    ("INTL", "T1"),                                     # OEM-overseas, excluded from phase 1
]
TIER_LABEL = {"T1": "T1", "T2": "T2", "T3": "T3", "T4": "T4", "dealer": "DLR"}

# a functional/department inbox local-part, not a person (bounded tokens). Deprioritise
# only, so over-matching is cheap — a mis-flagged person is merely sent later, never dropped.
ROLE_INBOX_RE = re.compile(
    r"(^|[._-])(sales|parts|service|servicing|enquir|enquiries|admin|info|information|contact|"
    r"reception|accounts?|warranty|fleet|general|office|reservations?|newvehicles?|usedvehicles?|"
    r"newcars?|usedcars?|preowned|leads?|bdc|department|dept|workshop|bookings?|aftersales|"
    r"customercare|customerservice|showroom|principal|manager|consultant|coordinator|"
    r"representative|noreply|no-reply|donotreply)([._-]|$)"
)


def geo_band(domain: str) -> str:
    d = (domain or "").lower().split(";")[0].strip()
    if d.endswith(".au"):
        return "AU"
    tld = d.rsplit(".", 1)[-1] if "." in d else d
    return "INTL" if len(tld) == 2 else "COM"   # 2-char final label = ccTLD (overseas)


def is_role_inbox(email: str) -> bool:
    return bool(ROLE_INBOX_RE.search((email or "").split("@")[0].lower()))
RAMP_STEP = 50                      # +50 per send, restarts per tier
RAMP_CAP = 250                      # plateau at 250 (GB 2026-07-03, option B)
BATCH_FLOOR = 50                    # coalesce any sub-floor tail batch
DEFAULT_MAX_PER_DOMAIN = 5          # strict during warming (first WARMING_BATCHES)
WARMING_BATCHES = 3                 # per tier: batches 1-3 hold the strict cap
TAIL_DOMAIN_MULT = 2               # post-warming per-domain cap = 2x (drains deep
                                    # domains faster, shortening the tail — still bounded)
MERGE_DOMAIN_CEIL = 12             # a floor-merge may not push any domain past this


def _target(tier_batch_i: int, ramp: list[int] | None) -> int:
    """Size for the tier_batch_i-th (0-indexed) batch within a tier."""
    if ramp:
        return ramp[tier_batch_i] if tier_batch_i < len(ramp) else ramp[-1]
    return min((tier_batch_i + 1) * RAMP_STEP, RAMP_CAP)


def run_plan_batches(ramp: list[int] | None = None, include_inferred: bool = False,
                     max_per_domain: int = DEFAULT_MAX_PER_DOMAIN) -> dict:
    cond = ("disposition = 'in_play' AND email IS NOT NULL AND NOT suppressed "
            "AND (cm_status = 'not_found' OR cm_status IS NULL)")
    if not include_inferred:
        cond += " AND confidence = 'direct'"

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, COALESCE(proximity_tier, 'T4') AS tier, "
            "       COALESCE(email_domain, '?') AS domain, confidence, email "
            f"FROM contacts WHERE {cond}"
        ).fetchall()
        conn.execute("UPDATE contacts SET export_batch = NULL, send_group = NULL "
                     "WHERE export_batch IS NOT NULL OR send_group IS NOT NULL")

        # (global_batch_no, send_group_label, [contact_ids]) in send order
        assignments: list[tuple[int, str, list[int]]] = []
        global_bn = 0
        id2dom = {r["id"]: r["domain"] for r in rows}

        # real people first (role_flag=False), then role inboxes (role_flag=True) at the back;
        # within each, GEO_TIER_ORDER (OEM AU/COM → dealers → the rest). One flat ordered
        # sweep so the per-group build body stays a single loop level.
        group_order = [(rf, b, t) for rf in (False, True) for (b, t) in GEO_TIER_ORDER]
        for role_flag, band, tier in group_order:
            trows = [r for r in rows if r["tier"] == tier and geo_band(r["domain"]) == band
                     and is_role_inbox(r["email"]) == role_flag]
            if not trows:
                continue
            # per-domain queues within the band+tier, direct-first
            trows.sort(key=lambda r: 0 if r["confidence"] == "direct" else 1)
            dq: dict[str, deque] = defaultdict(deque)
            for r in trows:
                dq[r["domain"]].append(r["id"])
            # biggest domain first so the giants spread across many batches
            active = sorted(dq, key=lambda d: -len(dq[d]))

            tier_batches: list[list[int]] = []
            batch_i = 0
            while any(dq[d] for d in active):
                tgt = _target(batch_i, ramp)
                # strict per-domain cap for the warming head; relaxed (but still
                # bounded) after, so deep domains drain faster and the tail is short.
                cap = max_per_domain if batch_i < WARMING_BATCHES else max_per_domain * TAIL_DOMAIN_MULT
                cur: list[int] = []
                cur_dom: dict[str, int] = defaultdict(int)
                while len(cur) < tgt:
                    progressed = False
                    for d in active:
                        if len(cur) >= tgt:
                            break
                        if dq[d] and cur_dom[d] < cap:
                            cur.append(dq[d].popleft())
                            cur_dom[d] += 1
                            progressed = True
                    if not progressed:      # domain cap blocks any further fill
                        break
                if not cur:
                    break
                tier_batches.append(cur)
                batch_i += 1

            # fold a sub-floor tail batch into its predecessor, but never push a
            # domain past MERGE_DOMAIN_CEIL — any contact that would breach stays
            # behind as a small residual (better a short residual than a blast).
            while len(tier_batches) > 1 and len(tier_batches[-1]) < BATCH_FLOOR:
                tail = tier_batches.pop()
                prev = tier_batches[-1]
                pdom: dict[str, int] = defaultdict(int)
                for cid in prev:
                    pdom[id2dom[cid]] += 1
                residual: list[int] = []
                for cid in tail:
                    if pdom[id2dom[cid]] < MERGE_DOMAIN_CEIL:
                        prev.append(cid)
                        pdom[id2dom[cid]] += 1
                    else:
                        residual.append(cid)
                if residual:
                    tier_batches.append(residual)   # couldn't all fit — keep the rest
                    break

            # swallow any trivially small leftover (< 10) into the previous batch;
            # a whole send for 2-3 contacts isn't worth it. Accepts a minor
            # per-domain overage on these late, post-warming sends.
            while len(tier_batches) > 1 and len(tier_batches[-1]) < 10:
                tier_batches[-2].extend(tier_batches.pop())

            for k, chunk in enumerate(tier_batches, 1):
                global_bn += 1
                sg = f"{'ROLE-' if role_flag else ''}{band}-{TIER_LABEL[tier]}-B{k:02d}"
                assignments.append((global_bn, sg, chunk))

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
