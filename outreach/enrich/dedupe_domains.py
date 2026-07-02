"""Cross-domain same-person dedup (TAE-2606-07).

The same real person is often listed under several domains: dealer groups with
multiple brand sites (bartons.net.au / bartonsldv.com.au), OEM groups
(nissan.com / nissan.com.au / infiniticars.com.au), a marketing agency whose
staff appear under every client's domain (coderedmarketing.com.au), and even
typo'd domain variants (philgilbert vs philigilbert). Left alone, one person can
sit in several batches and get emailed more than once.

Signal (high precision, no alias map needed): the SAME email local-part, the SAME
person name, a person-like local-part (first.last / first_last, not a role word),
spanning 2-4 distinct domains. Generic role inboxes (sales.department@,
enquiry.form@) are excluded — they legitimately recur across unrelated dealers.

For each such group we KEEP one copy and suppress the rest as
`cross_domain_dupe`. Keep-preference: AU domain first, de-prefer agency/marketing
domains, then the earliest batch already assigned, then lowest id — so we retain
the most appropriate address (e.g. nissan.com.au over nissan.com) and never
disturb an existing batch-1 pick. A person is never fully dropped, only their
duplicates. Reversible (distinct suppress_reason). Dry-run by default.
"""
import logging

from outreach.db import get_conn

log = logging.getLogger("outreach.enrich.dedupe_domains")

# role words that make a local-part a shared inbox, not a person
ROLE_RE = (r"(sales|parts|service|enquir|admin|info|contact|dealer|princip|manage|"
           r"management|team|depart|dept|form|recept|account|warranty|finance|fleet|"
           r"used|general|office|mail|marketing|career|jobs|workshop|booking|aftersales|"
           r"make|anenquiry|customer|business|interpreter|reservation|leads?|bdc)")

CANDIDATE_SQL = f"""
  SELECT id, lower(split_part(email, '@', 1)) AS lp, email, email_domain,
         lower(coalesce(full_name, '')) AS nm, export_batch
  FROM contacts
  WHERE email IS NOT NULL AND NOT suppressed
    AND split_part(email, '@', 1) ~ '^[a-z]+[._][a-z]+$'
    AND split_part(email, '@', 1) !~* '{ROLE_RE}'
    AND coalesce(full_name, '') ~ '^[A-Za-z]+ +[A-Za-z]'
"""


def _keep_score(r):
    d = (r["email_domain"] or "").lower()
    return (
        0 if d.endswith(".au") else 1,                              # AU domain first
        1 if ("marketing" in d or "coderedmarketing" in d) else 0,  # agency last
        r["export_batch"] if r["export_batch"] is not None else 10 ** 9,  # earliest batch
        r["id"],
    )


def run_dedupe_domains(dry_run: bool = True) -> dict:
    with get_conn() as conn:
        rows = conn.execute(CANDIDATE_SQL).fetchall()

        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r["lp"], []).append(r)

        drop_ids: list[int] = []
        samples: list[dict] = []
        n_people = 0
        for lp, members in groups.items():
            domains = {m["email_domain"] for m in members}
            names = {m["nm"] for m in members if m["nm"]}
            if not (2 <= len(domains) <= 4) or len(names) > 1:
                continue
            n_people += 1
            ordered = sorted(members, key=_keep_score)
            keep, drop = ordered[0], ordered[1:]
            drop_ids.extend(m["id"] for m in drop)
            if len(samples) < 20:
                samples.append({
                    "person": lp,
                    "keep": keep["email"],
                    "drop": [m["email"] for m in drop],
                })

        if drop_ids and not dry_run:
            conn.execute(
                """UPDATE contacts
                   SET suppressed = true, suppress_reason = 'cross_domain_dupe',
                       disposition = 'ruled_out', ruled_out_stage = 'cross_domain_dupe',
                       ruled_out_reason = 'same person under another domain — kept one copy'
                   WHERE id = ANY(%s)""",
                (drop_ids,),
            )

    summary = {
        "people": n_people,
        "redundant": len(drop_ids),
        "applied": (bool(drop_ids) and not dry_run),
        "samples": samples,
    }
    log.info("dedupe-domains: %d people, %d redundant copies (%s)",
             n_people, len(drop_ids), "APPLIED" if summary["applied"] else "dry-run")
    return summary
