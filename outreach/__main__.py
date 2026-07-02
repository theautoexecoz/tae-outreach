import argparse
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("outreach")

BRANDS = [
    "toyota", "mitsubishi", "hyundai", "kia", "ford",
    "mazda", "nissan", "isuzu", "honda", "subaru",
    "bmw", "mercedes", "ldv", "volkswagen", "audi",
    "volvo", "jeep", "mg", "byd", "gwm",
]


def cmd_discover(args):
    from outreach.discover import run_discovery
    brands = BRANDS if args.all else [args.brand]
    for brand in brands:
        log.info("discovering dealers for %s", brand)
        run_discovery(brand, limit=args.limit)


def cmd_scrape(args):
    from outreach.scrape.team_page_finder import find_team_pages
    from outreach.scrape.team_page_scraper import scrape_team_pages
    find_team_pages(brand=args.brand, limit=args.limit)
    scrape_team_pages(brand=args.brand, limit=args.limit)


def cmd_rescrape(args):
    from outreach.scrape.team_page_scraper import rescrape_empty_mailtos
    rescrape_empty_mailtos(brand=args.brand, limit=args.limit)


def cmd_extract(args):
    from outreach.extract import run_extraction
    run_extraction(brand=args.brand, limit=args.limit)


def cmd_apply_patterns(args):
    from outreach.extract import run_apply_patterns
    run_apply_patterns()


def cmd_cleanup(args):
    from outreach.extract import run_cleanup
    run_cleanup()


def cmd_cm_dedup(args):
    from outreach.enrich.cm_dedup import run_cm_dedup
    s = run_cm_dedup()
    print(
        f"\nCM dedup — {s['total_with_email']} emailable contacts:\n"
        f"  already active     : {s['active']:>5}  (existing subscribers — exclude)\n"
        f"  unsubscribed       : {s['unsubscribed']:>5}  (opted out — exclude)\n"
        f"  deleted/bounced    : {s['deleted']:>5}  (exclude)\n"
        f"  NEW (exportable)   : {s['not_found']:>5}  (cm_status=not_found)\n"
    )


def cmd_ooo_harvest(args):
    from outreach.harvest.ooo import run_ooo_harvest
    s = run_ooo_harvest(limit=args.limit, dry_run=args.dry_run)
    sk = s["skipped"]
    print(
        f"\nOOO harvest — {s['messages']} message(s) scanned"
        + (" [DRY RUN — no writes]" if s["dry_run"] else "") + ":\n"
        f"  senders excluded (GB rule) : {s['senders_excluded']:>5}\n"
        f"  skipped self/free/role/opq : {sk['self']}/{sk['freemail']}/{sk['role']}/{sk['opaque']}\n"
        f"  skipped unnamed (no person): {sk['unnamed']:>5}\n"
        f"  NEW delegate contacts      : {s['candidates']:>5}"
        + (f"  ({s['inserted']} inserted, rest already known)" if not s["dry_run"] else "") + "\n"
        f"  domains profiled (formats) : {s['domains_profiled']:>5}"
        + (f"  ({s['domains_written']} written)" if not s["dry_run"] else "") + "\n"
    )
    if s["dry_run"]:
        print("  sample new contacts:")
        for c in s.get("sample_contacts", []):
            print(f"    {c['email']:<40} {c['full_name']}  [{c['role_raw'] or '-'}]")
        print()


def cmd_newspress_harvest(args):
    from outreach.harvest.newspress import run_newspress_harvest
    s = run_newspress_harvest(limit=args.limit, dry_run=args.dry_run,
                              max_pages=args.max_pages, only_id=args.id)
    print(
        f"\nNewspress harvest — {s['releases']} release(s) parsed"
        + (" [DRY RUN — no writes]" if s["dry_run"] else "") + ":\n"
        f"  missing/404 releases : {s['missing']:>5}\n"
        f"  unique PR contacts   : {s['contacts']:>5}"
        + (f"  ({s['inserted']} inserted, rest already known)" if not s["dry_run"] else "") + "\n"
    )
    if s["dry_run"]:
        print("  sample contacts:")
        for c in s.get("sample_contacts", []):
            print(f"    {c['email']:<40} {c['full_name']}  [{c['role_raw'] or '-'}]")
        print()


def cmd_suppress(args):
    from outreach.enrich.suppress import run_suppress, SUPPRESS_DOMAINS
    r = run_suppress()
    print(
        f"\nSuppression (do-not-email) on {len(SUPPRESS_DOMAINS)} media domains:\n"
        f"  newly suppressed : {len(r['newly_suppressed'])}\n"
        f"  total suppressed : {r['total_suppressed']}\n"
        + ("  " + ", ".join(r['newly_suppressed']) + "\n" if r['newly_suppressed'] else "")
    )


def cmd_plan_batches(args):
    from outreach.export.plan_batches import run_plan_batches
    ramp = [int(x) for x in args.ramp.split(",")] if args.ramp else None
    s = run_plan_batches(ramp=ramp, include_inferred=args.include_inferred,
                         max_per_domain=args.max_per_domain)
    print(
        f"\nplan-batches (§4b) — {s['sendable']} sendable "
        + ("(GREEN/direct only)" if not args.include_inferred else "(incl. inferred)")
        + f" → {s['batches']} batches (NO SEND — plan only):\n"
        f"  {'batch':>5} {'size':>5} {'T1':>4} {'T2':>4} {'T3':>4} {'T4':>4} {'dealer':>7} {'max/domain':>11}"
    )
    for r in s["rows"]:
        print(f"  {r['b']:>5} {r['n']:>5} {r['t1']:>4} {r['t2']:>4} {r['t3']:>4} {r['t4']:>4} {r['dealer']:>7} {r['max_per_domain']:>11}")
    print()


def cmd_classify_proximity(args):
    from outreach.enrich.proximity import run_classify_proximity
    s = run_classify_proximity()
    c = s["classified"]
    ip = s["in_play_emailable"]
    print(
        "\nProximity classification (§4b):\n"
        f"  all contacts : dealer {c['dealer']}  T1 {c['T1']}  T2 {c['T2']}  T3 {c['T3']}  T4 {c['T4']}\n"
        f"  in_play emailable : " + "  ".join(f"{k} {ip.get(k, 0)}" for k in ("dealer", "T1", "T2", "T3", "T4")) + "\n"
    )


def cmd_wp_dedup(args):
    from outreach.enrich.wp_dedup import run_wp_dedup
    s = run_wp_dedup(args.emails_file)
    print(
        f"\nWP/MemberPress dedup (§2b) — {s['wp_email_set']} WP account emails:\n"
        f"  contacts matching WP : {s['contacts_matched']:>5}  (incl. already-excluded)\n"
        f"  newly ruled_out      : {s['newly_ruled_out']:>5}  (were in_play)\n"
        f"  wp-member total      : {s['wp_member_total']:>5}  by source {s['by_source']}\n"
    )


def cmd_ledger_refresh(args):
    from outreach.ledger import run_ledger_refresh
    s = run_ledger_refresh()
    d = s["disposition"]
    print(
        "\nLedger refresh (§3 provenance ledger):\n"
        f"  company backfilled : team_page +{s['company_backfilled']['team_page']}  "
        f"newspress +{s['company_backfilled']['newspress']}  (total with company: {s['with_company']})\n"
        f"  ruled_out stamped  : suppressed {s['ruled_out_set']['suppressed']}  "
        f"cm {s['ruled_out_set']['cm']}   |  reset to in_play: {s['reset_in_play']}\n"
        f"  disposition now    : in_play {d.get('in_play', 0)}  ruled_out {d.get('ruled_out', 0)}\n"
    )


def cmd_stats(args):
    from outreach.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT brand_slug, scrape_state, COUNT(*) AS n "
            "FROM dealerships GROUP BY brand_slug, scrape_state ORDER BY brand_slug, scrape_state"
        )
        rows = cur.fetchall()
        if not rows:
            print("No data yet.")
            return
        print(f"\n{'Brand':<15} {'State':<20} {'Count':>6}")
        print("-" * 45)
        for r in rows:
            print(f"{r['brand_slug']:<15} {r['scrape_state']:<20} {r['n']:>6}")

        cur = conn.execute(
            "SELECT confidence, COUNT(*) AS n FROM contacts GROUP BY confidence ORDER BY confidence"
        )
        contacts = cur.fetchall()
        if contacts:
            print(f"\n{'Confidence':<15} {'Contacts':>8}")
            print("-" * 25)
            for c in contacts:
                print(f"{c['confidence']:<15} {c['n']:>8}")

        cur = conn.execute("SELECT COUNT(*) AS n FROM contacts WHERE email IS NOT NULL")
        emails = cur.fetchone()
        print(f"\nTotal emails: {emails['n']}")


def cmd_migrate(args):
    from outreach.db import run_migration
    import os
    migration_dir = os.path.join(os.path.dirname(__file__), "migrations")
    for f in sorted(os.listdir(migration_dir)):
        if f.endswith(".sql"):
            path = os.path.join(migration_dir, f)
            log.info("running migration %s", f)
            run_migration(path)
    log.info("migrations complete")


def cmd_export(args):
    from outreach.export.csv_export import export_csv
    export_csv(args.output, all_contacts=args.all)


def main():
    parser = argparse.ArgumentParser(prog="outreach", description="TAE Outreach — dealer contact scraper")
    sub = parser.add_subparsers(dest="command")

    p_discover = sub.add_parser("discover", help="discover dealerships from OEM locators")
    p_discover.add_argument("--brand", choices=BRANDS)
    p_discover.add_argument("--all", action="store_true")
    p_discover.add_argument("--limit", type=int, default=0)

    p_scrape = sub.add_parser("scrape", help="find and scrape team pages")
    p_scrape.add_argument("--brand")
    p_scrape.add_argument("--limit", type=int, default=0)

    p_rescrape = sub.add_parser("rescrape", help="re-scrape pages with empty mailto links via playwright")
    p_rescrape.add_argument("--brand")
    p_rescrape.add_argument("--limit", type=int, default=0)

    p_extract = sub.add_parser("extract", help="extract contacts from scraped HTML")
    p_extract.add_argument("--brand")
    p_extract.add_argument("--limit", type=int, default=0)

    sub.add_parser("apply-patterns", help="retrospective cross-dealer email pattern pass")
    sub.add_parser("cleanup", help="remove department label contacts (no-email junk)")
    sub.add_parser("cm-dedup", help="mark contacts against the Campaign Monitor subscriber universe")
    sub.add_parser("suppress", help="do-not-email: suppress contacts on blocklisted media domains")

    p_ooo = sub.add_parser("ooo-harvest", help="harvest delegate contacts + domain formats from OOO replies (excludes the sender)")
    p_ooo.add_argument("--limit", type=int, default=0, help="cap to most-recent N messages")
    p_ooo.add_argument("--dry-run", action="store_true", help="parse + report, write nothing")

    p_np = sub.add_parser("newspress-harvest", help="harvest PR/marketing contacts from newspressaustralia.com releases")
    p_np.add_argument("--limit", type=int, default=0, help="cap number of releases processed")
    p_np.add_argument("--max-pages", type=int, default=0, help="cap release-list pagination")
    p_np.add_argument("--id", type=int, default=None, help="fetch+parse a single public release id (no cookie needed) — testing")
    p_np.add_argument("--dry-run", action="store_true", help="parse + report, write nothing")

    p_wp = sub.add_parser("wp-dedup", help="§2b: rule out contacts that are current/past WP/MemberPress accounts")
    p_wp.add_argument("--emails-file", default="data/wp-member-emails.txt", help="file of WP account emails, one per line")

    sub.add_parser("classify-proximity", help="§4b: tag contacts dealer/T1-T4 industry proximity for batch ordering")

    p_pb = sub.add_parser("plan-batches", help="§4b: assign export_batch — proximity-ordered, domain-staggered, ramped (no send)")
    p_pb.add_argument("--ramp", default="", help="comma batch sizes, e.g. 50,100,200,300,500 (default); last repeats")
    p_pb.add_argument("--include-inferred", action="store_true", help="also batch inferred/guessed (default GREEN/direct only)")
    p_pb.add_argument("--max-per-domain", type=int, default=5, help="max contacts of one domain per batch (default 5)")

    sub.add_parser("ledger-refresh", help="§3 ledger: backfill company + derive disposition/ruled_out from suppressed+cm_status")
    sub.add_parser("stats", help="show pipeline statistics")
    sub.add_parser("migrate", help="run database migrations")

    p_export = sub.add_parser("export", help="export the exportable mailout pool to CSV")
    p_export.add_argument("--output", default="contacts.csv")
    p_export.add_argument("--all", action="store_true",
                          help="dump every contact with an email (incl. CM-matched/suppressed) for review")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "discover": cmd_discover,
        "scrape": cmd_scrape,
        "rescrape": cmd_rescrape,
        "extract": cmd_extract,
        "apply-patterns": cmd_apply_patterns,
        "cleanup": cmd_cleanup,
        "cm-dedup": cmd_cm_dedup,
        "suppress": cmd_suppress,
        "ooo-harvest": cmd_ooo_harvest,
        "newspress-harvest": cmd_newspress_harvest,
        "wp-dedup": cmd_wp_dedup,
        "classify-proximity": cmd_classify_proximity,
        "plan-batches": cmd_plan_batches,
        "ledger-refresh": cmd_ledger_refresh,
        "stats": cmd_stats,
        "migrate": cmd_migrate,
        "export": cmd_export,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
